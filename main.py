"""astrbot_plugin_repeat

按 群组 + 白名单 隔离收集群成员发言,存入自托管 Qdrant 向量库,
在合适时机用两种模式把语义相关的历史发言"自然地"发出来:

- 附和(echo):语义相近就把历史里相近的那句翻出来
- 顺延(cont):命中话题触发词就接一句历史里的自然接续句
"""

import asyncio
import json
import random
import time
from typing import Dict, Optional, Set

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .embedding import build_embedder
from .qdrant_store import MODE_CONT, MODE_ECHO, QdrantStore

PLUGIN_NAME = "astrbot_plugin_repeat"

# Plugin Pages(WebUI 管理台)能力在较新的 AstrBot 才有,缺失时插件仍可正常运行。
try:
    from astrbot.api.web import error_response, json_response, request

    _WEB_AVAILABLE = True
except Exception:  # noqa: BLE001
    _WEB_AVAILABLE = False

_CONT_EXTRACT_PROMPT = """你在帮一个群聊机器人建立"接话"记忆库。下面是群里的一条消息。
请判断它是否包含"提到某个话题后,自然接上的一句个人化短评/接话"。

如果包含,抽取:
- trigger: 触发这次接话的核心话题/关键词(简短,几个字)
- response: 适合在别人下次提到该话题时自然接上的那句话(口语、简短、就是原话里的接话部分)

如果这条消息没有这种"话题→接话"结构,返回 {"skip": true}。
只返回 JSON,不要任何多余文字。

消息:「%s」"""


@register(
    "astrbot_plugin_repeat",
    "camera-2018",
    "按群组+白名单隔离收集发言存入 Qdrant,语义附和或顺延接话",
    "0.1.0",
)
class RepeatPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 连接 / 集合
        self.qdrant_url = config.get("qdrant_url") or "http://localhost:6333"
        self.qdrant_api_key = config.get("qdrant_api_key") or ""
        self.collection = config.get("collection_name") or "repeat_memory"

        # 白名单(留空=不限制)
        self.active_groups: Set[str] = {
            str(x) for x in (config.get("active_group_whitelist") or [])
        }
        self.collect_users: Set[str] = {
            str(x) for x in (config.get("collect_user_whitelist") or [])
        }

        # 模式开关
        self.enable_echo = bool(config.get("enable_echo", True))
        self.enable_continuation = bool(config.get("enable_continuation", True))
        self.continuation_use_llm = bool(config.get("continuation_use_llm", True))
        self.continuation_min_length = int(config.get("continuation_min_length", 8))
        self.continuation_cue_words = [
            str(w) for w in (config.get("continuation_cue_words") or [])
        ]

        # 阈值 / 时机
        self.echo_threshold = float(config.get("echo_threshold", 0.85))
        self.cont_threshold = float(config.get("cont_threshold", 0.80))
        self.reply_probability = float(config.get("reply_probability", 0.6))
        self.cooldown_seconds = int(config.get("cooldown_seconds", 30))
        self.min_length = int(config.get("min_length", 4))
        self.dedup_threshold = float(config.get("dedup_threshold", 0.97))

        self.embedder = None
        self.store: Optional[QdrantStore] = None
        self._ready = False
        self._init_lock = asyncio.Lock()
        self._last_reply: Dict[str, float] = {}

        self._register_web_apis()

    # ---------- WebUI 管理台(Plugin Pages) ----------

    def _register_web_apis(self):
        if not _WEB_AVAILABLE:
            return
        reg = getattr(self.context, "register_web_api", None)
        if not callable(reg):
            return
        p = PLUGIN_NAME
        try:
            reg(f"/{p}/stats", self.web_stats, ["GET"], "向量库统计")
            reg(f"/{p}/groups", self.web_groups, ["GET"], "群组列表")
            reg(f"/{p}/list", self.web_list, ["GET"], "浏览记忆点")
            reg(f"/{p}/search", self.web_search, ["POST"], "语义搜索")
            reg(f"/{p}/upsert", self.web_upsert, ["POST"], "新增/编辑记忆点")
            reg(f"/{p}/delete", self.web_delete, ["POST"], "删除记忆点/清空群")
            logger.info("[repeat] 已注册 WebUI 管理台接口")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[repeat] 注册 WebUI 接口失败: {e}")

    async def web_stats(self):
        if not await self._ensure_ready():
            return error_response("插件未就绪:请检查 Qdrant 连接与 embedding 配置")
        return json_response(
            {
                "status": "ok",
                "data": {
                    "total": await self.store.total(),
                    "by_mode": await self.store.facet("mode"),
                    "by_group": await self.store.facet("group_id"),
                },
            }
        )

    async def web_groups(self):
        if not await self._ensure_ready():
            return error_response("插件未就绪")
        groups = await self.store.facet("group_id")
        return json_response({"status": "ok", "data": [g["value"] for g in groups]})

    async def web_list(self):
        if not await self._ensure_ready():
            return error_response("插件未就绪")
        group = request.query.get("group", "")
        if not group:
            return error_response("缺少 group 参数")
        mode = request.query.get("mode", "") or None
        limit = request.query.get("limit", 20, type=int)
        offset = request.query.get("offset", "") or None
        points, nxt = await self.store.scroll(group, mode, limit, offset)
        items = [{"id": str(p.id), **(p.payload or {})} for p in points]
        return json_response({"status": "ok", "data": {"items": items, "next": nxt}})

    async def web_search(self):
        if not await self._ensure_ready():
            return error_response("插件未就绪")
        body = await request.json(default={})
        group = str(body.get("group", "")).strip()
        mode = str(body.get("mode", "echo")).strip() or "echo"
        query = str(body.get("query", "")).strip()
        limit = int(body.get("limit", 10) or 10)
        if not group or not query:
            return error_response("group 和 query 必填")
        try:
            vec = await self.embedder.embed(query)
        except Exception as e:  # noqa: BLE001
            return error_response(f"embedding 失败: {e}", 500)
        hits = await self.store.search(mode, group, vec, limit=limit)
        data = [{"id": str(h.id), "score": h.score, **(h.payload or {})} for h in hits]
        return json_response({"status": "ok", "data": data})

    async def web_upsert(self):
        if not await self._ensure_ready():
            return error_response("插件未就绪")
        body = await request.json(default={})
        group = str(body.get("group", "")).strip()
        mode = str(body.get("mode", "")).strip()
        text = str(body.get("text", "")).strip()
        response = str(body.get("response", "")).strip()
        pid = body.get("id") or None
        if not group or not text or mode not in (MODE_ECHO, MODE_CONT):
            return error_response("group/text 必填,mode 须为 echo 或 cont")
        if mode == MODE_CONT and not response:
            return error_response("顺延(cont)模式需要填写 response(接续句)")
        try:
            # echo 用全文,cont 用 text(即触发词)做向量
            vec = await self.embedder.embed(text)
        except Exception as e:  # noqa: BLE001
            return error_response(f"embedding 失败: {e}", 500)
        new_id = await self.store.upsert(
            mode, group, vec, text=text, response=response,
            sender_id="webui", point_id=pid,
        )
        return json_response({"status": "ok", "data": {"id": new_id}})

    async def web_delete(self):
        if not await self._ensure_ready():
            return error_response("插件未就绪")
        body = await request.json(default={})
        pid = body.get("id")
        group = str(body.get("group", "")).strip()
        if pid:
            await self.store.delete_point(pid)
            return json_response({"status": "ok", "data": {"deleted": str(pid)}})
        if group and body.get("clear"):
            await self.store.clear_group(group)
            return json_response({"status": "ok", "data": {"cleared_group": group}})
        return error_response("需要 id(删除单点)或 group + clear=true(清空整群)")

    # ---------- 生命周期 ----------

    async def initialize(self):
        await self._ensure_ready()

    async def _ensure_ready(self) -> bool:
        if self._ready:
            return True
        async with self._init_lock:
            if self._ready:
                return True
            try:
                self.embedder = build_embedder(self.config, self.context)
                dim = await self.embedder.dim()
                self.store = QdrantStore(
                    self.qdrant_url, self.qdrant_api_key, self.collection
                )
                await self.store.ensure_collection(dim)
                self._ready = True
                logger.info(f"[repeat] 初始化完成 (collection={self.collection}, dim={dim})")
            except Exception as e:  # noqa: BLE001
                logger.error(f"[repeat] 初始化失败,稍后会重试: {e}")
                self._ready = False
        return self._ready

    async def terminate(self):
        if self.store is not None:
            await self.store.close()

    # ---------- 工具 ----------

    @staticmethod
    def _clean_text(raw: str) -> str:
        # event.message_str 已是纯文本(At 等组件不在其中),只需规整空白。
        # 不要用 @\S+ 这种正则:中文 @ 后常无空格,会把正文一起删掉。
        return (raw or "").strip()

    def _maybe_continuation(self, text: str) -> bool:
        """便宜的前置闸:决定是否值得为这条消息调用 LLM 做顺延抽取。"""
        if len(text) < self.continuation_min_length:
            return False
        if self.continuation_cue_words:
            return any(w in text for w in self.continuation_cue_words)
        return True

    def _can_reply(self, group_id: str) -> bool:
        last = self._last_reply.get(group_id, 0)
        return (time.time() - last) >= self.cooldown_seconds

    async def _llm_extract(self, event: AstrMessageEvent, text: str):
        """返回 (trigger, response) 元组;"SKIP" 表示无接话结构;None 表示 LLM 不可用(调用方回退整句)。"""
        try:
            prov = self.context.get_using_provider(umo=event.unified_msg_origin)
        except TypeError:
            prov = self.context.get_using_provider()
        except Exception:  # noqa: BLE001
            prov = None
        if prov is None:
            return None
        try:
            resp = await prov.text_chat(prompt=_CONT_EXTRACT_PROMPT % text)
            content = getattr(resp, "completion_text", None) or str(resp)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[repeat] LLM 拆句失败,回退整句: {e}")
            return None

        data = self._parse_json(content)
        if data is None:
            return None
        if data.get("skip"):
            return "SKIP"
        trigger = str(data.get("trigger") or "").strip()
        response = str(data.get("response") or "").strip()
        if trigger and response:
            return (trigger, response)
        return "SKIP"

    @staticmethod
    def _parse_json(content: str) -> Optional[dict]:
        if not content:
            return None
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end < start:
            return None
        try:
            obj = json.loads(content[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except Exception:  # noqa: BLE001
            return None

    # ---------- 主流程 ----------

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id() or "")
        if not group_id:
            return
        if self.active_groups and group_id not in self.active_groups:
            return

        text = self._clean_text(event.message_str)
        if not text or text.startswith("/") or len(text) < self.min_length:
            return

        if not await self._ensure_ready():
            return

        sender_id = str(event.get_sender_id() or "")

        try:
            vec = await self.embedder.embed(text)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[repeat] embedding 失败: {e}")
            return

        # 1) 先检索(写入前,避免命中刚插入的自己)
        echo_match = None
        if self.enable_echo:
            echo_match = await self.store.best_match(MODE_ECHO, group_id, vec)
        cont_match = None
        if self.enable_continuation:
            cont_match = await self.store.best_match(MODE_CONT, group_id, vec)

        candidates = []
        if echo_match and echo_match[0] >= self.echo_threshold:
            reply = (echo_match[1].get("text") or "").strip()
            if reply and reply != text:
                candidates.append((echo_match[0], reply))
        if cont_match and cont_match[0] >= self.cont_threshold:
            reply = (cont_match[1].get("response") or "").strip()
            if reply and reply != text:
                candidates.append((cont_match[0], reply))

        # 2) 命中则过 冷却+概率 门后发话
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            _, reply = candidates[0]
            if self._can_reply(group_id) and random.random() < self.reply_probability:
                self._last_reply[group_id] = time.time()
                yield event.plain_result(reply)

        # 3) 收集写入(发送者在收集白名单内才写;与是否回复无关)
        if self.collect_users and sender_id not in self.collect_users:
            return

        if self.enable_echo:
            await self._collect_echo(group_id, vec, text, sender_id, echo_match)
        if self.enable_continuation:
            await self._collect_continuation(event, group_id, vec, text, sender_id)

    async def _collect_echo(self, group_id, vec, text, sender_id, echo_match):
        # 去重:与库内最相近句过于相似则跳过
        if (
            self.dedup_threshold < 1.0
            and echo_match is not None
            and echo_match[0] >= self.dedup_threshold
        ):
            return
        try:
            await self.store.upsert(
                MODE_ECHO, group_id, vec, text=text, sender_id=sender_id
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[repeat] 写入 echo 失败: {e}")

    async def _collect_continuation(self, event, group_id, vec, text, sender_id):
        if self.continuation_use_llm:
            # 前置闸:不值得抽取的消息直接跳过,省掉 LLM 调用
            if not self._maybe_continuation(text):
                return
            extracted = await self._llm_extract(event, text)
            # "SKIP"=无接话结构;None=LLM 出错/不可用。两者都跳过,避免污染 cont 库。
            if not isinstance(extracted, tuple):
                return
            trigger, response = extracted
        else:
            # 显式关闭 LLM:整句存储 + 检索
            trigger, response = text, text

        try:
            tvec = vec if trigger == text else await self.embedder.embed(trigger)
            # 去重:与库内最相近的 trigger 过于相似则跳过,防止 cont 库无界增长
            if self.dedup_threshold < 1.0:
                dup = await self.store.best_match(MODE_CONT, group_id, tvec)
                if dup and dup[0] >= self.dedup_threshold:
                    return
            await self.store.upsert(
                MODE_CONT,
                group_id,
                tvec,
                text=trigger,
                response=response,
                sender_id=sender_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[repeat] 写入 cont 失败: {e}")

    # ---------- 管理命令 ----------

    @filter.command_group("repeat")
    def repeat(self):
        pass

    @repeat.command("status")
    async def repeat_status(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id() or "")
        if not await self._ensure_ready():
            yield event.plain_result("[repeat] 未就绪:请检查 Qdrant 连接与 embedding 配置。")
            return
        cnt = await self.store.count(group_id) if group_id else 0
        lines = [
            "【群组语义复读 状态】",
            f"本群记忆条数: {cnt}",
            f"附和模式: {'开' if self.enable_echo else '关'} (阈值 {self.echo_threshold})",
            f"顺延模式: {'开' if self.enable_continuation else '关'} (阈值 {self.cont_threshold}, LLM拆句 {'开' if self.continuation_use_llm else '关'})",
            f"发话概率: {self.reply_probability} / 冷却 {self.cooldown_seconds}s",
            f"Embedding 来源: {self.config.get('embedding_source')}",
        ]
        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @repeat.command("clear")
    async def repeat_clear(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id() or "")
        if not group_id:
            yield event.plain_result("[repeat] 仅可在群内清空本群记忆。")
            return
        if not await self._ensure_ready():
            yield event.plain_result("[repeat] 未就绪,无法清空。")
            return
        await self.store.clear_group(group_id)
        yield event.plain_result("[repeat] 已清空本群记忆。")

"""Qdrant 封装。

单集合 + payload 过滤实现群隔离:
- 每个点的 payload 带 group_id / mode("echo"|"cont") / text / response / sender_id / ts /
  text_bigrams / response_bigrams(关键词查询用的 bigram 倒排索引 key,见 query_raw)
- 所有检索都加 group_id == 当前群 的 filter
"""

import time
import uuid
from typing import List, Optional, Tuple

from astrbot.api import logger
from qdrant_client import AsyncQdrantClient, models

MODE_ECHO = "echo"
MODE_CONT = "cont"


def _bigrams(s: str) -> List[str]:
    """按字符窗口切 2-gram,绕开中文分词做子串检索的倒排索引 key。

    比如 "火锅" -> ["火锅"];"我爱吃火锅" -> ["我爱","爱吃","吃火","火锅"](去重后顺序不定)。
    长度 <2 的字符串组不出任何 bigram,调用方需要对这种情况单独兜底。
    """
    s = (s or "").lower()
    if len(s) < 2:
        return []
    return list({s[i : i + 2] for i in range(len(s) - 1)})


class QdrantStore:
    def __init__(self, url: str, api_key: str, collection: str):
        self.collection = collection
        kwargs = {"url": url}
        if api_key:
            kwargs["api_key"] = api_key
        self.client = AsyncQdrantClient(**kwargs)

    async def ensure_collection(self, dim: int) -> None:
        exists = await self.client.collection_exists(self.collection)
        if not exists:
            await self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=dim, distance=models.Distance.COSINE
                ),
            )
            logger.info(f"[repeat] 已创建 Qdrant 集合 {self.collection} (dim={dim})")
        else:
            info = await self.client.get_collection(self.collection)
            existing_dim = info.config.params.vectors.size
            if existing_dim != dim:
                # 维度不一致时直接报错,让上层把插件标记为未就绪,
                # 而不是后续 upsert/search 持续抛异常被吞掉、表现为"静默不工作"。
                raise RuntimeError(
                    f"集合 {self.collection} 维度={existing_dim} 与当前 embedding 维度={dim} "
                    f"不一致!请更换 collection_name 或删除旧集合后重建。"
                )
        # 给过滤字段建索引(幂等,重复建会被忽略/报已存在,吞掉异常)
        index_fields = {
            "group_id": models.PayloadSchemaType.KEYWORD,
            "mode": models.PayloadSchemaType.KEYWORD,
            "sender_id": models.PayloadSchemaType.KEYWORD,
            "ts": models.PayloadSchemaType.INTEGER,
            # 数组字段建 KEYWORD 索引后,MatchValue 天然按"数组包含该值"匹配,
            # 用来做 bigram 倒排检索(关键词查询加速,见 query_raw)。
            "text_bigrams": models.PayloadSchemaType.KEYWORD,
            "response_bigrams": models.PayloadSchemaType.KEYWORD,
        }
        for field, schema in index_fields.items():
            try:
                await self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema=schema,
                )
            except Exception:  # noqa: BLE001
                pass

    def _group_mode_filter(self, group_id: str, mode: str) -> models.Filter:
        return models.Filter(
            must=[
                models.FieldCondition(
                    key="group_id", match=models.MatchValue(value=group_id)
                ),
                models.FieldCondition(key="mode", match=models.MatchValue(value=mode)),
            ]
        )

    async def upsert(
        self,
        mode: str,
        group_id: str,
        vector: List[float],
        text: str,
        response: str = "",
        sender_id: str = "",
        point_id: Optional[str] = None,
    ) -> str:
        pid = point_id or str(uuid.uuid4())
        point = models.PointStruct(
            id=pid,
            vector=vector,
            payload={
                "group_id": group_id,
                "mode": mode,
                "text": text,
                "response": response,
                "sender_id": sender_id,
                "ts": int(time.time()),
                # 关键词查询用的 bigram 倒排索引 key,查询时对候选点仍会做精确子串
                # 校验(见 query_raw),这里只负责快速圈定候选范围。
                "text_bigrams": _bigrams(text),
                "response_bigrams": _bigrams(response),
            },
        )
        await self.client.upsert(collection_name=self.collection, points=[point])
        return pid

    async def search(
        self, mode: str, group_id: str, vector: List[float], limit: int = 1
    ) -> List[models.ScoredPoint]:
        res = await self.client.query_points(
            collection_name=self.collection,
            query=vector,
            query_filter=self._group_mode_filter(group_id, mode),
            limit=limit,
            with_payload=True,
        )
        return res.points

    async def best_match(
        self, mode: str, group_id: str, vector: List[float]
    ) -> Optional[Tuple[float, dict]]:
        hits = await self.search(mode, group_id, vector, limit=1)
        if not hits:
            return None
        hit = hits[0]
        return hit.score, (hit.payload or {})

    async def scroll(
        self,
        group_id: str,
        mode: Optional[str] = None,
        limit: int = 20,
        offset: Optional[str] = None,
    ) -> Tuple[List[models.Record], Optional[str]]:
        must = [
            models.FieldCondition(
                key="group_id", match=models.MatchValue(value=group_id)
            )
        ]
        if mode:
            must.append(
                models.FieldCondition(key="mode", match=models.MatchValue(value=mode))
            )
        points, next_offset = await self.client.scroll(
            collection_name=self.collection,
            scroll_filter=models.Filter(must=must),
            limit=limit,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        return points, (str(next_offset) if next_offset is not None else None)

    async def query_raw(
        self,
        group_id: str,
        mode: Optional[str] = None,
        sender_id: Optional[str] = None,
        keyword: Optional[str] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        limit: int = 20,
        offset: Optional[str] = None,
        max_scan: int = 2000,
        chunk: int = 256,
    ) -> Tuple[List[dict], Optional[str], int]:
        """按原始存储字段(不走向量)查询记忆点。

        结构化条件(group/mode/sender/时间范围)走 Qdrant filter。keyword 用写入时
        预计算的 bigram(2 字符窗口)倒排索引(`text_bigrams`/`response_bigrams`)
        快速圈定候选,候选点仍会做一次客户端精确子串校验(bigram 都命中不代表连续拼接
        成原串,必须二次确认排除假阳性)。

        为什么不用 Qdrant 的 TEXT 索引 + MatchText:实测 multilingual/word/whitespace
        三种分词器,查询关键词"火锅"都搜不到明明包含"我爱吃火锅"的文档——中文场景下
        MatchText 基于分词 token 做匹配,不是任意位置的字符子串匹配,大多数子串查询会
        直接落空。自建 bigram 索引绕开分词,结果精确且能命中索引加速(CJK 全文搜索的
        标准做法,SQLite FTS5/MySQL ngram/Elasticsearch ngram 分词器都是同一思路)。

        旧数据兼容:bigram 索引是随本次改动才开始在 upsert 时写入的,上线前已存在的点
        payload 里没有 `text_bigrams` 字段。这类点用 `text_bigrams` 是否为空识别(见
        `IsEmptyCondition`),走和以前一样的全量客户端扫描兜底,不需要一次性迁移脚本——
        随着新消息不断写入,这个兜底子集会自然萎缩。

        关键词长度 <2 时组不出 bigram,直接退化为全量客户端扫描(覆盖新旧数据)。

        分页:对外仍是一个不透明的 offset 字符串。有关键词且能组出 bigram 时,内部
        用 "A:"/"B:" 前缀标记当前处于"bigram 快路径"还是"legacy 兜底"阶段,调用方
        不需要关心这个编码,原样透传即可。
        返回 (items, next_offset, scanned)。
        """
        must = [
            models.FieldCondition(
                key="group_id", match=models.MatchValue(value=group_id)
            )
        ]
        if mode:
            must.append(
                models.FieldCondition(key="mode", match=models.MatchValue(value=mode))
            )
        if sender_id:
            must.append(
                models.FieldCondition(
                    key="sender_id", match=models.MatchValue(value=sender_id)
                )
            )
        if since is not None or until is not None:
            must.append(
                models.FieldCondition(
                    key="ts", range=models.Range(gte=since, lte=until)
                )
            )
        flt = models.Filter(must=must)
        kw = (keyword or "").strip().lower()

        def to_item(p):
            return {"id": str(p.id), **(p.payload or {})}

        # 无关键词:结构化过滤已足够,单页返回
        if not kw:
            points, nxt = await self.client.scroll(
                collection_name=self.collection,
                scroll_filter=flt,
                limit=limit,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            return (
                [to_item(p) for p in points],
                (str(nxt) if nxt is not None else None),
                len(points),
            )

        async def scan(
            filt: models.Filter, start_offset: Optional[str], want: int
        ) -> Tuple[List[dict], Optional[str], int]:
            """chunk 翻页扫描 filt 命中的点,client 端精确子串校验,直到凑够
            want 条 / 扫完 / 达到 max_scan,按页边界续翻(无重无漏)。

            want 由调用方显式传入(而不是直接闭包 limit)——A 阶段耗尽后接着跑
            legacy 兜底时,只应再要"limit 减去 A 阶段已找到的量",不能重新按整个
            limit 算,否则合并结果会超过调用方要求的条数。
            """
            found: List[dict] = []
            scanned = 0
            cur = start_offset
            while True:
                points, nxt = await self.client.scroll(
                    collection_name=self.collection,
                    scroll_filter=filt,
                    limit=chunk,
                    offset=cur,
                    with_payload=True,
                    with_vectors=False,
                )
                scanned += len(points)
                for p in points:
                    pl = p.payload or {}
                    if kw in str(pl.get("text", "")).lower() or kw in str(
                        pl.get("response", "")
                    ).lower():
                        found.append(to_item(p))
                cur = str(nxt) if nxt is not None else None
                if cur is None or len(found) >= want or scanned >= max_scan:
                    return found, cur, scanned

        kw_bigrams = _bigrams(kw)
        if not kw_bigrams:
            # 关键词 <2 字符组不出 bigram,只能全量扫描兜底(覆盖新旧数据,无 A/B 分段)
            return await scan(flt, offset, limit)

        # 阶段 A:bigram 索引快路径——kw 的每个 bigram 都要出现在 text_bigrams 或
        # 都出现在 response_bigrams(OR),数组字段的 KEYWORD 索引天然做"包含"匹配。
        fast_filter = models.Filter(
            must=must
            + [
                models.Filter(
                    should=[
                        models.Filter(
                            must=[
                                models.FieldCondition(
                                    key="text_bigrams",
                                    match=models.MatchValue(value=b),
                                )
                                for b in kw_bigrams
                            ]
                        ),
                        models.Filter(
                            must=[
                                models.FieldCondition(
                                    key="response_bigrams",
                                    match=models.MatchValue(value=b),
                                )
                                for b in kw_bigrams
                            ]
                        ),
                    ]
                )
            ]
        )
        # 阶段 B:legacy 兜底——text_bigrams 缺失(上线前旧数据,或文本过短没算出
        # bigram 的边角情况)的点,套用和以前一样的全量扫描 + 精确校验。
        legacy_filter = models.Filter(
            must=must
            + [models.IsEmptyCondition(is_empty=models.PayloadField(key="text_bigrams"))]
        )

        stage, inner_offset = "A", None
        if offset:
            if offset.startswith("A:"):
                stage, inner_offset = "A", (offset[2:] or None)
            elif offset.startswith("B:"):
                stage, inner_offset = "B", (offset[2:] or None)
            # 不识别的旧格式 offset:防御性地当作从头开始,不抛异常

        if stage == "B":
            found, cur, scanned = await scan(legacy_filter, inner_offset, limit)
            return found, (f"B:{cur}" if cur is not None else None), scanned

        # stage == "A"
        found, cur, scanned = await scan(fast_filter, inner_offset, limit)
        if cur is not None:
            return found, f"A:{cur}", scanned
        # A 阶段已耗尽。已经凑够 limit 就不急着探 B,把续查游标留给下一次调用;
        # 否则在同一次调用内接着跑 legacy 兜底,只再要差额,避免合并结果超过 limit。
        if len(found) >= limit:
            return found, "B:", scanned
        more, cur_b, scanned_b = await scan(legacy_filter, None, limit - len(found))
        found.extend(more)
        return found, (f"B:{cur_b}" if cur_b is not None else None), (scanned + scanned_b)

    async def delete_point(self, point_id: str) -> None:
        await self.delete_points([point_id])

    async def delete_points(self, point_ids: List[str]) -> int:
        if not point_ids:
            return 0
        await self.client.delete(
            collection_name=self.collection,
            points_selector=models.PointIdsList(points=list(point_ids)),
        )
        return len(point_ids)

    async def facet(self, key: str, group_id: Optional[str] = None, limit: int = 200):
        flt = None
        if group_id:
            flt = models.Filter(
                must=[
                    models.FieldCondition(
                        key="group_id", match=models.MatchValue(value=group_id)
                    )
                ]
            )
        res = await self.client.facet(
            collection_name=self.collection, key=key, facet_filter=flt, limit=limit
        )
        return [{"value": h.value, "count": h.count} for h in res.hits]

    async def total(self) -> int:
        res = await self.client.count(collection_name=self.collection, exact=True)
        return res.count

    async def clear_group(self, group_id: str) -> None:
        await self.client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="group_id", match=models.MatchValue(value=group_id)
                        )
                    ]
                )
            ),
        )

    async def count(self, group_id: str) -> int:
        res = await self.client.count(
            collection_name=self.collection,
            count_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="group_id", match=models.MatchValue(value=group_id)
                    )
                ]
            ),
            exact=True,
        )
        return res.count

    async def close(self) -> None:
        try:
            await self.client.close()
        except Exception:  # noqa: BLE001
            pass

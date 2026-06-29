# astrbot_plugin_repeat — 群组语义复读 (Qdrant)

按 **群组 + 白名单** 隔离收集群成员发言,存入自托管 **Qdrant** 向量库,在合适时机把语义相关的历史发言"自然地"发出来。

两种模式:

- **附和(echo)**:语义相近就把历史里相近的那句翻出来。
  例:历史里有人说过"我也喜欢你",有人新说"我喜欢你" → 命中并发出"我也喜欢你"。
- **顺延(continuation)**:把发言拆成「话题触发词 → 自然接续句」存库,命中触发词就接那句。
  例:"我买了门票但没去过演唱会,但我去过我懂" → 存 `触发词=演唱会 / 接续句=我去过我懂`;
  下次有人提到"演唱会" → 接一句"我去过我懂"。

## 1. 起 Qdrant(自托管)

仓库自带 `docker-compose.yml`:

```bash
docker compose up -d
curl http://localhost:6333/healthz        # 验活
# 可视化面板: http://localhost:6333/dashboard
```

数据持久化在 `./qdrant_storage`。需要鉴权就在 compose 里打开 `QDRANT__SERVICE__API_KEY`,并在插件配置填同样的 `qdrant_api_key`。

## 2. 安装插件

把本目录放到 AstrBot 的 `data/plugins/astrbot_plugin_repeat`,安装依赖:

```bash
pip install -r requirements.txt
```

在 AstrBot 面板重载插件。

## 3. 配置(面板 → 插件配置)

| 项 | 说明 | 默认 |
|----|------|------|
| `qdrant_url` | Qdrant 地址 | `http://localhost:6333` |
| `qdrant_api_key` | Qdrant 鉴权(可选) | 空 |
| `collection_name` | 集合名(所有群共用,靠 group_id 隔离) | `repeat_memory` |
| `embedding_source` | `astrbot`=复用面板 embedding provider;`openai`=直连外部接口 | `astrbot` |
| `embedding_provider_id` | 指定 AstrBot embedding provider id(留空取第一个) | 空 |
| `openai_base_url` / `openai_api_key` / `openai_model` / `openai_dim` | 外部接口参数(`embedding_source=openai` 时用) | OpenAI 默认 |
| `active_group_whitelist` | 生效群白名单,**留空=所有群** | `[]` |
| `collect_user_whitelist` | 收集用户白名单,**留空=收集所有人** | `[]` |
| `enable_echo` / `enable_continuation` | 两种模式开关 | 都开 |
| `continuation_use_llm` | 顺延用 LLM 拆句(关掉则整句存储) | 开 |
| `continuation_min_length` | 顺延抽取最小长度(短消息不调 LLM) | 8 |
| `continuation_cue_words` | 含任一词才调 LLM 抽取顺延(省成本,留空=不按词过滤) | 一组默认词 |
| `echo_threshold` / `cont_threshold` | 相似度阈值(余弦,0~1) | 0.85 / 0.80 |
| `reply_probability` | 命中后发话概率(1=高分必发) | 0.6 |
| `cooldown_seconds` | 每群发话冷却 | 30 |
| `min_length` | 收集/匹配最小文本长度 | 4 |
| `dedup_threshold` | 写入去重阈值(1=关闭) | 0.97 |

> **Embedding 默认复用 AstrBot 面板里配置的 embedding 模型**(无需在插件再填 key),需先在 AstrBot 里配好一个 embedding provider(如 bge-m3、text-embedding-3-small 等)。

## 4. 管理命令

- `/repeat status` — 查看本群记忆条数与当前配置。
- `/repeat clear` — 清空本群记忆(**仅管理员**)。

## 4.5 内嵌 WebUI 管理台

插件自带一个原生内嵌在 AstrBot 后台的管理页(基于 AstrBot **Plugin Pages**),
在 AstrBot WebUI 的插件页里打开 `astrbot_plugin_repeat` 的页面即可,功能:

- **统计**:总记忆点数、按模式(附和/顺延)分布、群数量。
- **语义搜索**:选群 + 模式 + 关键词,实时 embedding 后向量检索,显示相似度分数。
- **浏览**:按群 / 模式分页翻看所有记忆点。
- **新增 / 编辑**:手动添加或按 id 覆盖记忆点(echo 填要翻出来的话;cont 填触发词 + 接续句),保存时自动重算向量。
- **删除 / 清空**:删除单条,或一键清空整群。

后端通过 `context.register_web_api` 暴露以下接口(前端用 `window.AstrBotPluginPage` 桥接调用,均带后台鉴权):
`stats` / `groups` / `list` / `search` / `upsert` / `delete`。

> 该页需要支持 Plugin Pages 的 AstrBot 版本;若版本过旧,插件会自动跳过页面注册、其余功能照常工作。

## 5. 工作机制

- 单集合 + payload(`group_id`/`mode`)过滤实现**群隔离**:每次检索都限定当前群。
- **先检索后写入**,避免命中刚插入的自己。
- 收集与回复相互独立:发话受 `冷却 + 概率` 控制,收集只看用户白名单。

## 注意

- 向量维度必须与 embedding 模型一致。集合一旦按某维度建好,**更换 embedding 模型需新建 `collection_name` 或删除旧集合重建**;维度不一致时插件会判定为"未就绪"(`/repeat status` 可见),不会静默写错。
- LLM 拆句失败/超时会**跳过本条顺延写入**(不污染顺延库),不阻塞消息处理;只有显式把 `continuation_use_llm` 关掉时才整句存储。
- 顺延抽取默认只对**达到 `continuation_min_length` 且命中 `continuation_cue_words`** 的消息调 LLM,以控制成本;按需调整或留空 `continuation_cue_words` 放宽。
- 冷却状态放内存,插件重载后重置。
- **唤醒前缀**:`/repeat` 等命令默认按 `/` 前缀;若你在 AstrBot 改了唤醒前缀,请相应使用,普通消息过滤也按 `/` 判断。
- **隐私**:本插件会把群成员发言逐条存入向量库。请仅对知情、同意的群和用户开启(用白名单约束),`/repeat clear` 可随时清空本群记忆。

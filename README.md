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
| `echo_threshold` / `cont_threshold` | 相似度阈值(余弦,0~1) | 0.85 / 0.80 |
| `reply_probability` | 命中后发话概率(1=高分必发) | 0.6 |
| `cooldown_seconds` | 每群发话冷却 | 30 |
| `min_length` | 收集/匹配最小文本长度 | 4 |
| `dedup_threshold` | 写入去重阈值(1=关闭) | 0.97 |

> **Embedding 默认复用 AstrBot 面板里配置的 embedding 模型**(无需在插件再填 key),需先在 AstrBot 里配好一个 embedding provider(如 bge-m3、text-embedding-3-small 等)。

## 4. 管理命令

- `/repeat status` — 查看本群记忆条数与当前配置。
- `/repeat clear` — 清空本群记忆(**仅管理员**)。

## 5. 工作机制

- 单集合 + payload(`group_id`/`mode`)过滤实现**群隔离**:每次检索都限定当前群。
- **先检索后写入**,避免命中刚插入的自己。
- 收集与回复相互独立:发话受 `冷却 + 概率` 控制,收集只看用户白名单。

## 注意

- 向量维度必须与 embedding 模型一致。集合一旦按某维度建好,**更换 embedding 模型需新建 `collection_name` 或删除旧集合重建**。
- LLM 拆句失败/超时会自动回退为整句存储,不阻塞消息处理。
- 冷却状态放内存,插件重载后重置。

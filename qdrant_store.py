"""Qdrant 封装。

单集合 + payload 过滤实现群隔离:
- 每个点的 payload 带 group_id / mode("echo"|"cont") / text / response / sender_id / ts
- 所有检索都加 group_id == 当前群 的 filter
"""

import time
import uuid
from typing import List, Optional, Tuple

from astrbot.api import logger
from qdrant_client import AsyncQdrantClient, models

MODE_ECHO = "echo"
MODE_CONT = "cont"


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
        for field in ("group_id", "mode"):
            try:
                await self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
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
    ) -> None:
        point = models.PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload={
                "group_id": group_id,
                "mode": mode,
                "text": text,
                "response": response,
                "sender_id": sender_id,
                "ts": int(time.time()),
            },
        )
        await self.client.upsert(collection_name=self.collection, points=[point])

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

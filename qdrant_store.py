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
        index_fields = {
            "group_id": models.PayloadSchemaType.KEYWORD,
            "mode": models.PayloadSchemaType.KEYWORD,
            "sender_id": models.PayloadSchemaType.KEYWORD,
            "ts": models.PayloadSchemaType.INTEGER,
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

        结构化条件(group/mode/sender/时间范围)走 Qdrant filter;
        keyword 做大小写无关子串包含,匹配 text 或 response(客户端过滤)。
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

        # 无关键词:结构化过滤已足够,单页返回,行为等同 scroll
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

        # 有关键词:按页扫描 + 客户端子串过滤,按页边界续翻(无重无漏)
        matches: List[dict] = []
        scanned = 0
        cur = offset
        while True:
            points, nxt = await self.client.scroll(
                collection_name=self.collection,
                scroll_filter=flt,
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
                    matches.append(to_item(p))
            cur = str(nxt) if nxt is not None else None
            if cur is None or len(matches) >= limit or scanned >= max_scan:
                break
        return matches, cur, scanned

    async def delete_point(self, point_id: str) -> None:
        await self.client.delete(
            collection_name=self.collection,
            points_selector=models.PointIdsList(points=[point_id]),
        )

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

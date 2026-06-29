"""Embedding 抽象层。

支持两种来源:
- astrbot: 复用 AstrBot 面板里配置的 embedding provider
- openai:  直连一个 OpenAI 兼容的 /embeddings 接口

对外统一暴露 async embed(text) -> list[float] 与 async dim() -> int。
"""

import inspect
from typing import List, Optional

import httpx

from astrbot.api import logger


async def _maybe_await(value):
    """provider 的方法可能是 sync 也可能是 async,这里统一处理。"""
    if inspect.isawaitable(value):
        return await value
    return value


class BaseEmbedder:
    async def embed(self, text: str) -> List[float]:
        raise NotImplementedError

    async def dim(self) -> int:
        raise NotImplementedError


class AstrBotEmbedder(BaseEmbedder):
    """复用 AstrBot 的 embedding provider。"""

    def __init__(self, context, provider_id: str = ""):
        self.context = context
        self.provider_id = provider_id
        self._provider = None
        self._dim: Optional[int] = None

    def _resolve_provider(self):
        if self._provider is not None:
            return self._provider
        providers = self.context.get_all_embedding_providers()
        if not providers:
            raise RuntimeError(
                "未找到任何 embedding provider,请先在 AstrBot 面板配置 embedding 模型,"
                "或把插件配置 embedding_source 改为 openai。"
            )
        provider = None
        if self.provider_id:
            for p in providers:
                pid = getattr(p, "id", None) or getattr(p, "provider_id", None)
                if pid == self.provider_id:
                    provider = p
                    break
            if provider is None:
                logger.warning(
                    f"[repeat] 未找到 id={self.provider_id} 的 embedding provider,改用第一个可用的。"
                )
        if provider is None:
            provider = providers[0]
        self._provider = provider
        return provider

    async def embed(self, text: str) -> List[float]:
        provider = self._resolve_provider()
        vec = await _maybe_await(provider.get_embedding(text))
        return list(vec)

    async def dim(self) -> int:
        if self._dim is not None:
            return self._dim
        provider = self._resolve_provider()
        get_dim = getattr(provider, "get_dim", None)
        if callable(get_dim):
            try:
                d = await _maybe_await(get_dim())
                if d:
                    self._dim = int(d)
                    return self._dim
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[repeat] provider.get_dim() 失败,改用试探法:{e}")
        # 退化:实际 embed 一次拿长度
        vec = await self.embed("维度探测")
        self._dim = len(vec)
        return self._dim


class OpenAIEmbedder(BaseEmbedder):
    """直连 OpenAI 兼容的 /embeddings 接口。"""

    def __init__(self, base_url: str, api_key: str, model: str, dim: int = 0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._dim: Optional[int] = dim if dim and dim > 0 else None

    async def embed(self, text: str) -> List[float]:
        url = f"{self.base_url}/embeddings"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {"model": self.model, "input": text}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        vec = data["data"][0]["embedding"]
        if self._dim is None:
            self._dim = len(vec)
        return list(vec)

    async def dim(self) -> int:
        if self._dim is not None:
            return self._dim
        await self.embed("dimension probe")
        return self._dim  # type: ignore[return-value]


def build_embedder(config, context) -> BaseEmbedder:
    source = (config.get("embedding_source") or "astrbot").lower()
    if source == "openai":
        return OpenAIEmbedder(
            base_url=config.get("openai_base_url") or "https://api.openai.com/v1",
            api_key=config.get("openai_api_key") or "",
            model=config.get("openai_model") or "text-embedding-3-small",
            dim=int(config.get("openai_dim") or 0),
        )
    return AstrBotEmbedder(context, provider_id=config.get("embedding_provider_id") or "")

"""文本向量化：调用 OpenAI-compatible embedding API。"""

from __future__ import annotations

from typing import List, Optional


class EmbedderError(RuntimeError):
    """Embedding 相关错误。"""


class TextEmbedder:
    def __init__(self, api_base_url: str, api_key: str, model: str, *,
                 timeout: int = 30, max_retries: int = 2):
        self._api_base_url = api_base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        endpoint = self._api_base_url + "/embeddings"
        payload = {"model": self._model, "input": texts}
        data = self._post_json(endpoint, payload)
        return _extract_vectors(data)

    def embed_one(self, text: str) -> List[float]:
        vectors = self.embed([text])
        return vectors[0] if vectors else []

    def _post_json(self, url: str, payload: dict) -> dict:
        from iris.core.http_client import http_post_json
        return http_post_json(url, payload, {"Authorization": f"Bearer {self._api_key}"},
                             timeout=self._timeout, max_retries=self._max_retries,
                             error_factory=lambda msg: EmbedderError(f"Embedding {msg}"))


def build_embedder_from_config(llm_config: dict) -> Optional[TextEmbedder]:
    """从 llm.json 的 embedding 段构造 TextEmbedder。"""
    emb_cfg = llm_config.get("embedding", {})
    if not emb_cfg.get("enabled", False):
        return None
    api_key = emb_cfg.get("api_key", "")
    if not api_key:
        return None
    return TextEmbedder(api_base_url=emb_cfg.get("api_base_url", ""),
                        api_key=api_key, model=emb_cfg.get("model", "text-embedding-v3"),
                        timeout=emb_cfg.get("timeout_seconds", 30),
                        max_retries=emb_cfg.get("max_retries", 2))


def _extract_vectors(data: dict) -> List[List[float]]:
    data_entries = data.get("data", [])
    sorted_entries = sorted(data_entries, key=lambda item: item.get("index", 0))
    return [item["embedding"] for item in sorted_entries if "embedding" in item]

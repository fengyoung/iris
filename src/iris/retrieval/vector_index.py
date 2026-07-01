"""向量索引：基于余弦相似度的轻量 embedding 检索。"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

import numpy as np

_VECTORS_NPY = "vectors.npy"
_IDS_JSON = "ids.json"
_META_JSON = "meta.json"


class VectorIndex:
    def __init__(self, index_path: Path):
        self._path = index_path
        self._data: Dict[str, Dict] = {}
        self._loaded = False
        # 缓存矩阵（避免每次 search 重建）
        self._matrix_cache: np.ndarray | None = None
        self._matrix_ids: List[str] = []
        self._valid_mask: np.ndarray | None = None

    def load(self) -> bool:
        bin_dir = self._binary_dir()
        if bin_dir.exists() and (bin_dir / _VECTORS_NPY).exists():
            ok = self._load_binary()
            if ok:
                self._invalidate_cache()
            return ok
        if self._path.exists():
            ok = self._load_legacy_json()
            if ok:
                self._invalidate_cache()
            return ok
        return False

    def _binary_dir(self) -> Path:
        return self._path.parent / self._path.stem

    def _load_binary(self) -> bool:
        bin_dir = self._binary_dir()
        try:
            vectors = np.load(str(bin_dir / _VECTORS_NPY))
            ids_data = json.loads((bin_dir / _IDS_JSON).read_text(encoding="utf-8"))
            chunk_ids: List[str] = ids_data["chunk_ids"]
            texts: List[str] = ids_data.get("texts", [""] * len(chunk_ids))
            if len(texts) < len(chunk_ids):
                texts.extend([""] * (len(chunk_ids) - len(texts)))
            self._data = {}
            for idx, cid in enumerate(chunk_ids):
                vec = vectors[idx].tolist() if idx < len(vectors) else []
                self._data[cid] = {"vector": vec, "text": texts[idx] if idx < len(texts) else ""}
            self._loaded = True
            return True
        except (OSError, ValueError, json.JSONDecodeError, KeyError):
            return False

    def _load_legacy_json(self) -> bool:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._data = raw
            self._loaded = True
            return True
        except (json.JSONDecodeError, OSError):
            return False

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._save_binary()

    def _save_binary(self) -> None:
        if not self._data:
            return
        bin_dir = self._binary_dir()
        bin_dir.mkdir(parents=True, exist_ok=True)
        chunk_ids = list(self._data.keys())
        dim = len(next(iter(self._data.values())).get("vector", []))
        matrix = np.zeros((len(chunk_ids), dim), dtype=np.float32)
        texts: List[str] = []
        for idx, cid in enumerate(chunk_ids):
            vec = self._data[cid].get("vector", [])
            if len(vec) == dim:
                matrix[idx] = np.array(vec, dtype=np.float32)
            texts.append(self._data[cid].get("text", ""))
        np.save(str(bin_dir / _VECTORS_NPY), matrix)
        (bin_dir / _IDS_JSON).write_text(json.dumps({"chunk_ids": chunk_ids, "texts": texts}, ensure_ascii=False), encoding="utf-8")
        (bin_dir / _META_JSON).write_text(json.dumps({"dim": dim, "count": len(chunk_ids), "updated_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2), encoding="utf-8")

    def upsert(self, chunk_id: str, vector: List[float], text: str = "") -> None:
        self._data[chunk_id] = {"vector": vector, "text": text}
        self._loaded = True
        self._invalidate_cache()

    def remove(self, chunk_id: str) -> None:
        self._data.pop(chunk_id, None)
        self._invalidate_cache()

    def exists(self, chunk_id: str) -> bool:
        return chunk_id in self._data

    def size(self) -> int:
        return len(self._data)

    def _invalidate_cache(self) -> None:
        """标记缓存矩阵为过期（upsert/remove 后调用）。"""
        self._matrix_cache = None
        self._matrix_ids = []
        self._valid_mask = None

    def _ensure_matrix(self) -> None:
        """按需构建缓存矩阵（首次 search 或缓存失效时调用）。"""
        if self._matrix_cache is not None and len(self._matrix_cache) == len(self._data):
            return
        if not self._data:
            self._matrix_cache = None
            self._matrix_ids = []
            self._valid_mask = None
            return
        self._matrix_ids = list(self._data.keys())
        dim = len(next(iter(self._data.values())).get("vector", []))
        if dim == 0:
            logger.warning("向量索引首个向量为空，搜索功能暂时不可用。请重建索引。")
            self._matrix_cache = None
            return
        self._matrix_cache = np.zeros((len(self._matrix_ids), dim), dtype=np.float32)
        valid_mask = np.ones(len(self._matrix_ids), dtype=bool)
        dim_mismatch_count = 0
        for idx, cid in enumerate(self._matrix_ids):
            vec = self._data[cid].get("vector", [])
            if len(vec) == dim:
                self._matrix_cache[idx] = np.array(vec, dtype=np.float32)
            else:
                valid_mask[idx] = False
                dim_mismatch_count += 1
        if dim_mismatch_count > 0:
            logger.warning("向量索引中 %d/%d 条记录维度不匹配（期望 %d），已跳过。"
                           "可能原因：embedding 模型更换。建议重建索引。",
                           dim_mismatch_count, len(self._matrix_ids), dim)
        self._valid_mask = valid_mask

    def search(self, query_vector: List[float], top_k: int = 10) -> List[Tuple[str, float]]:
        if not self._data:
            return []
        self._ensure_matrix()
        if self._matrix_cache is None or self._valid_mask is None:
            return []
        if not self._valid_mask.any():
            return []
        qvec = np.array(query_vector, dtype=np.float32)
        qnorm = float(np.linalg.norm(qvec))
        if qnorm == 0:
            return []
        valid_vecs = self._matrix_cache[self._valid_mask]
        valid_ids = [cid for i, cid in enumerate(self._matrix_ids) if self._valid_mask[i]]
        vnorms = np.linalg.norm(valid_vecs, axis=1)
        vnorms[vnorms == 0] = 1e-9
        dots = np.dot(valid_vecs, qvec)
        scores = dots / (qnorm * vnorms)
        sorted_indices = np.argsort(-scores)[:top_k]
        return [(valid_ids[i], round(float(scores[i]), 6)) for i in sorted_indices]

    def is_loaded(self) -> bool:
        return self._loaded


def build_vector_index(source_name: str, chunks: list, embedder, index_path: Path,
                       *, existing_index: Optional[VectorIndex] = None) -> VectorIndex:
    index = existing_index or VectorIndex(index_path)
    if not index.is_loaded():
        index.load()
    to_embed: List[Tuple[str, str]] = []
    for chunk in chunks:
        chunk_id = chunk.chunk_id
        if not index.exists(chunk_id):
            to_embed.append((chunk_id, chunk.content_preview))
    if not to_embed:
        return index
    batch_size = 10
    for i in range(0, len(to_embed), batch_size):
        batch = to_embed[i:i + batch_size]
        ids = [item[0] for item in batch]
        texts = [item[1] for item in batch]
        vectors = embedder.embed(texts)
        for chunk_id, vec, text in zip(ids, vectors, texts):
            index.upsert(chunk_id, vec, text)
    index.save()
    return index

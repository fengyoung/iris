"""向量索引：基于余弦相似度的轻量 embedding 检索。"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

import numpy as np

_VECTORS_NPY = "vectors.npy"
_IDS_JSON = "ids.json"
_META_JSON = "meta.json"
_CURRENT_JSON = "current.json"
_GENERATIONS_DIR = "generations"


class VectorIndexModelMismatchError(RuntimeError):
    """embedder 模型与已有索引不一致：新旧向量空间不可混用，拒绝增量写入。"""


class VectorIndex:
    def __init__(self, index_path: Path):
        self._path = index_path
        self._data: Dict[str, Dict] = {}
        self._loaded = False
        self._embedder_model: str = ""
        self._loaded_embedder_model: str = ""
        # 缓存矩阵（避免每次 search 重建）
        self._matrix_cache: np.ndarray | None = None
        self._matrix_ids: List[str] = []
        self._valid_mask: np.ndarray | None = None

    def load(self) -> bool:
        from iris.core.locks import FileLock

        bin_dir = self._binary_dir()
        if bin_dir.exists():
            with FileLock(bin_dir / "index"):
                data_dir = self._active_binary_dir()
                if data_dir is not None:
                    ok = self._load_binary(data_dir)
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

    def _active_binary_dir(self) -> Optional[Path]:
        """返回当前完整代际目录；兼容旧版直接三文件布局。"""
        bin_dir = self._binary_dir()
        current_path = bin_dir / _CURRENT_JSON
        if current_path.exists():
            try:
                generation = json.loads(current_path.read_text(encoding="utf-8"))["generation"]
                candidate = bin_dir / _GENERATIONS_DIR / generation
                if all((candidate / name).exists() for name in (_VECTORS_NPY, _IDS_JSON, _META_JSON)):
                    return candidate
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                return None
        if (bin_dir / _VECTORS_NPY).exists():
            return bin_dir
        return None

    def _load_binary(self, data_dir: Path) -> bool:
        try:
            vectors = np.load(str(data_dir / _VECTORS_NPY))
            ids_data = json.loads((data_dir / _IDS_JSON).read_text(encoding="utf-8"))
            chunk_ids: List[str] = ids_data["chunk_ids"]
            texts: List[str] = ids_data.get("texts", [""] * len(chunk_ids))
            if len(texts) < len(chunk_ids):
                texts.extend([""] * (len(chunk_ids) - len(texts)))
            # doc_hashes：v3.28.1 新增，旧索引缺失时为空串（不触发重嵌，靠 force-rebuild 补齐）
            doc_hashes: List[str] = ids_data.get("doc_hashes", [""] * len(chunk_ids))
            if len(doc_hashes) < len(chunk_ids):
                doc_hashes.extend([""] * (len(chunk_ids) - len(doc_hashes)))
            self._data = {}
            for idx, cid in enumerate(chunk_ids):
                vec = vectors[idx].tolist() if idx < len(vectors) else []
                self._data[cid] = {"vector": vec, "text": texts[idx] if idx < len(texts) else "",
                                   "doc_hash": doc_hashes[idx] if idx < len(doc_hashes) else ""}
            meta_path = data_dir / _META_JSON
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    self._loaded_embedder_model = meta.get("embedder_model", "")
                except (json.JSONDecodeError, OSError):
                    pass
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
        from iris.core.locks import FileLock
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # FileLock 保护向量索引三文件并发写入
        lock_path = self._binary_dir() / _VECTORS_NPY
        with FileLock(lock_path):
            self._save_binary()

    def _save_binary(self) -> None:
        from iris.utils.shared import atomic_write_json

        bin_dir = self._binary_dir()
        bin_dir.mkdir(parents=True, exist_ok=True)
        chunk_ids = list(self._data.keys())
        dim = len(next(iter(self._data.values())).get("vector", [])) if self._data else 0
        matrix = np.zeros((len(chunk_ids), dim), dtype=np.float32)
        texts: List[str] = []
        doc_hashes: List[str] = []
        for idx, cid in enumerate(chunk_ids):
            vec = self._data[cid].get("vector", [])
            if len(vec) == dim:
                matrix[idx] = np.array(vec, dtype=np.float32)
            texts.append(self._data[cid].get("text", ""))
            doc_hashes.append(self._data[cid].get("doc_hash", ""))
        generation = uuid.uuid4().hex
        generation_dir = bin_dir / _GENERATIONS_DIR / generation
        generation_dir.mkdir(parents=True, exist_ok=False)
        try:
            np.save(str(generation_dir / _VECTORS_NPY), matrix)
            atomic_write_json(generation_dir / _IDS_JSON,
                              {"chunk_ids": chunk_ids, "texts": texts, "doc_hashes": doc_hashes})
            atomic_write_json(generation_dir / _META_JSON, {
                "dim": dim,
                "count": len(chunk_ids),
                "embedder_model": self._embedder_model,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            atomic_write_json(bin_dir / _CURRENT_JSON, {"generation": generation})
        except Exception:
            shutil.rmtree(generation_dir, ignore_errors=True)
            raise

        generations_dir = bin_dir / _GENERATIONS_DIR
        for old_dir in generations_dir.iterdir():
            if old_dir.is_dir() and old_dir.name != generation:
                shutil.rmtree(old_dir, ignore_errors=True)

    def upsert(self, chunk_id: str, vector: List[float], text: str = "",
               doc_hash: str = "") -> None:
        self._data[chunk_id] = {"vector": vector, "text": text, "doc_hash": doc_hash}
        self._loaded = True
        self._invalidate_cache()

    def remove(self, chunk_id: str) -> None:
        self._data.pop(chunk_id, None)
        self._invalidate_cache()

    def exists(self, chunk_id: str) -> bool:
        return chunk_id in self._data

    def doc_hash(self, chunk_id: str) -> str:
        """返回该 chunk 入索引时的源文档 hash（旧索引/未知时为空串）。"""
        entry = self._data.get(chunk_id)
        return entry.get("doc_hash", "") if entry else ""

    def all_chunk_ids(self) -> List[str]:
        """返回索引中全部 chunk_id（供增量更新清理已删除文档的残留向量）。"""
        return list(self._data.keys())

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
        try:
            first_entry = next(iter(self._data.values()))
        except StopIteration:
            self._matrix_cache = None
            self._matrix_ids = []
            self._valid_mask = None
            return
        dim = len(first_entry.get("vector", []))
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

    def set_embedder_model(self, model: str) -> None:
        self._embedder_model = model


def build_vector_index(source_name: str, chunks: list, embedder, index_path: Path,
                       *, existing_index: Optional[VectorIndex] = None,
                       force_rebuild: bool = False) -> VectorIndex:
    index = existing_index or VectorIndex(index_path)
    if not index.is_loaded():
        index.load()
    current_model = getattr(embedder, "model", "")
    if force_rebuild:
        # 全量重建：丢弃旧向量（同时清理已删除文档的残留 chunk），重新嵌入全部 chunk
        logger.info("向量索引 %s 全量重建（embedder 模型 %s）", source_name, current_model or "unknown")
        index = VectorIndex(index_path)
    elif current_model:
        loaded_model = index._loaded_embedder_model
        if loaded_model and loaded_model != current_model:
            # 硬失败：混用两个模型的向量空间会使余弦相似度失去意义，
            # 静默带病运行比报错更危险。
            raise VectorIndexModelMismatchError(
                f"向量索引 embedder 模型已变更（{loaded_model} → {current_model}），"
                "新旧向量空间不可混用，已拒绝增量写入。"
                "请执行 build-vector-index --force-rebuild 完整重建。")
    if current_model:
        index.set_embedder_model(current_model)

    # ── 增量判定（v3.28.1 重写）────────────────────────────────────
    # 旧逻辑「exists 即跳过」有两个洞：
    #   ① chunk_id = 路径::序号，不含内容 hash——文档编辑后 id 不变，旧向量永不更新；
    #   ② 从不删除不在本次 chunks 中的 id——已删除/归档文档的死向量永久残留
    #     （v3.22.3「向量 > chunk」事故的复发通道）。
    # 现按 document_hash 判定重嵌，并按差集清理残留。
    # 注意：调用方必须传入该数据源的**全量** chunk 列表（两个现有调用方均满足），
    # 否则差集清理会误删。
    current_ids = {chunk.chunk_id for chunk in chunks}
    stale_ids = [cid for cid in index.all_chunk_ids() if cid not in current_ids]
    for cid in stale_ids:
        index.remove(cid)
    if stale_ids:
        logger.info("向量索引 %s 清理残留向量 %d 条（源文档已删除/归档）", source_name, len(stale_ids))

    to_embed: List[Tuple[str, str, str]] = []
    for chunk in chunks:
        chunk_id = chunk.chunk_id
        doc_hash = getattr(chunk, "document_hash", "") or ""
        if index.exists(chunk_id):
            indexed_hash = index.doc_hash(chunk_id)
            # 旧索引无 doc_hash（空串）时保持旧行为不重嵌，避免升级即全量重嵌；
            # 有 hash 且不一致 → 文档已编辑，必须重嵌。
            if not indexed_hash or indexed_hash == doc_hash:
                continue
        to_embed.append((chunk_id, chunk.content_preview, doc_hash))
    if not to_embed:
        if force_rebuild or stale_ids:
            index.save()  # 无新 chunk 也需落盘：强制重建覆盖旧文件 / 残留清理生效
        return index
    batch_size = 10
    for i in range(0, len(to_embed), batch_size):
        batch = to_embed[i:i + batch_size]
        ids = [item[0] for item in batch]
        texts = [item[1] for item in batch]
        hashes = [item[2] for item in batch]
        vectors = embedder.embed(texts)
        for chunk_id, vec, text, doc_hash in zip(ids, vectors, texts, hashes):
            index.upsert(chunk_id, vec, text, doc_hash=doc_hash)
    index.save()
    return index

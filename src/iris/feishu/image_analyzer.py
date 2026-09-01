"""飞书消息图片理解 — 下载 + 多模态 LLM 分析 + 描述缓存。

供 feed（话题聚合）与 chat-digest（聊天提炼）两条消息管道共用：在 LLM
看到消息之前，为图片消息补一段中文内容描述，使下游能引用图片信息，
而不是把 `[Image: img_v3_xxx]` 原始 token 当噪音文本。

成本控制：enabled 开关 + max_per_run 单次上限 + 按 image_key 缓存（跨运行、
跨管道共享），同一张图只被多模态分析一次。缓存命中不计入预算。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from iris.utils.shared import atomic_write_json

logger = logging.getLogger(__name__)

# 图片消息中可识别的 image_key token（实测形如 img_v3_0214t_xxx-xxx-xxx）
_IMAGE_KEY_RE = re.compile(r"img_[A-Za-z0-9_\-]+")

# 固定中文 prompt：要求简短、结构化描述图片内容
_IMG_PROMPT = (
    "这是飞书群聊中分享的图片。用中文 60 字内简要描述：图片类型、主要元素、"
    "可识别的文字或界面关键信息。直接输出描述，不要前缀。"
)

# 魔数 → MIME 嗅探（飞书消息图片通常为 PNG/JPEG/WEBP）
def _sniff_mime(head: bytes) -> str:
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/png"


class MessageImageAnalyzer:
    """消息图片 → 下载 → 多模态分析 → 返回中文描述。

    两种接入形态：
      - describe_raw_message(msg)  ：feed 的 RawMessage
      - describe_dict_message(msg)：chat-digest 的飞书原始 dict
    两者都委托给 _describe_image(msg_id, image_key)。

    Args:
        client: FeishuClient（as_user=True）
        llm: LLMService（adv_model 多模态）
        cache_dir: 缓存根目录（内含 images/ 与 image_descriptions.json）
        enabled: 总开关，False 则不分析任何图片
        max_per_run: 单次运行最多真正调用多模态 LLM 的图片张数（缓存命中不计）
    """

    def __init__(
        self,
        client,
        llm,
        *,
        cache_dir: Path,
        enabled: bool = True,
        max_per_run: int = 10,
    ):
        self._client = client
        self._llm = llm
        self._cache_dir = Path(cache_dir)
        self._images_dir = self._cache_dir / "images"
        self._index_path = self._cache_dir / "image_descriptions.json"
        self._enabled = enabled
        self._max_per_run = max_per_run
        self._used = 0
        self._index: Optional[Dict[str, Dict[str, str]]] = None

    # ── 对外入口 ────────────────────────────────────────────

    def describe_raw_message(self, msg) -> Optional[str]:
        """feed 形态：从 RawMessage 提取 image_key，返回描述（调用方赋给 msg.image_description）。"""
        image_key = self._extract_image_key(
            msg.raw_content, fallback=msg.content
        )
        if not image_key:
            return None
        return self._describe_image(msg.msg_id, image_key)

    def describe_dict_message(self, msg: Dict[str, Any]) -> Optional[str]:
        """chat-digest 形态：从飞书原始 dict 提取 image_key，返回描述。"""
        image_key = self._extract_image_key(
            msg, fallback=msg.get("image_key", "")
        )
        if not image_key:
            return None
        return self._describe_image(msg.get("message_id", ""), image_key)

    # ── 核心逻辑 ────────────────────────────────────────────

    def _describe_image(self, msg_id: str, image_key: str) -> Optional[str]:
        """下载并分析单张图片，返回描述；任一环节失败或受控返回 None。"""
        if not self._enabled:
            return None
        if image_key in self._load_index():
            return self._index[image_key]["description"]
        if self._used >= self._max_per_run:
            logger.debug("图片理解预算已用尽(%d)，跳过 %s", self._max_per_run, image_key)
            return None

        try:
            path = self._download(image_key, msg_id)
            if path is None:
                return None
            data_url = self._to_data_url(path)
            description = self._call_llm(data_url)
        except Exception as exc:
            logger.warning("图片 %s 分析失败: %s", image_key, exc)
            return None

        if not description:
            return None

        self._used += 1
        self._index[image_key] = {"description": description}
        self._save_index()
        return description

    def _download(self, image_key: str, msg_id: str) -> Optional[Path]:
        """下载图片到缓存目录；已存在则复用。返回本地路径。"""
        target = self._images_dir / f"{image_key}.png"
        if target.exists():
            return target
        try:
            # lark-cli 要求相对路径（拒绝绝对路径），用 relpath 满足约束
            rel = os.path.relpath(target, os.getcwd())
            self._client.download_message_image(msg_id, image_key, rel)
            return target if target.exists() else None
        except Exception as exc:
            logger.warning("图片 %s 下载失败: %s", image_key, exc)
            return None

    def _to_data_url(self, path: Path) -> str:
        """读取图片字节并编码为 data URL（带魔数嗅探的 MIME）。"""
        raw = path.read_bytes()
        mime = _sniff_mime(raw[:16])
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def _call_llm(self, data_url: str) -> str:
        """多模态 LLM 分析单张图片，返回描述文本。"""
        text = self._llm.generate_multimodal(
            [
                {"type": "text", "text": _IMG_PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
            route_context={
                "input_type": "multimodal",
                "task_type": "image_understanding",
                "complexity": "complex",
            },
            temperature=0.2,
            max_tokens=300,
        )
        return (text or "").strip()

    # ── 帮助方法 ────────────────────────────────────────────

    @staticmethod
    def _extract_image_key(container: Dict[str, Any], fallback: str = "") -> str:
        """从消息 body 提取 image_key。

        兼容两种形态：body.content 可能是 JSON 字符串 {"image_key": "img_v3_xxx"}，
        也可能是 `[Image: img_v3_xxx]` 文本；最后兜底直接在内容里正则匹配。
        """
        body = container.get("body", {}) if isinstance(container, dict) else {}
        content = body.get("content", "") if isinstance(body, dict) else ""
        if isinstance(content, str):
            content = content.strip()
            # JSON 形态
            if content.startswith("{"):
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict):
                        key = parsed.get("image_key", "")
                        if key:
                            return key
                except (ValueError, TypeError):
                    pass
            # 文本形态 / 兜底正则
            m = _IMAGE_KEY_RE.search(content)
            if m:
                return m.group(0)
        if isinstance(fallback, str):
            m = _IMAGE_KEY_RE.search(fallback)
            if m:
                return m.group(0)
        return ""

    def _load_index(self) -> Dict[str, Dict[str, str]]:
        """惰性加载描述索引。"""
        if self._index is None:
            try:
                data = json.loads(self._index_path.read_text(encoding="utf-8"))
                self._index = data if isinstance(data, dict) else {}
            except (OSError, ValueError):
                self._index = {}
        return self._index

    def _save_index(self) -> None:
        """原子写描述索引。"""
        try:
            atomic_write_json(self._index_path, self._index or {})
        except OSError as exc:
            logger.warning("图片描述索引写入失败: %s", exc)

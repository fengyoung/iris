"""飞书文档转本地 Markdown — 转换、归档、排重。"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from iris.config.loader import ConfigBundle
from iris.feishu.client import FeishuClient, FeishuClientError
from iris.core.write_guard import safe_write_text

# ── 常量 ────────────────────────────────────────────────────

_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
"""匹配 Markdown 图片引用 ![](url)。"""

_DATE_PREFIX_RE = re.compile(r"^\d{8}")
"""文件名开头的日期前缀。"""

_PIC_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}

DEFAULT_DEDUP_INDEX = "data/dedup/feishu_doc_index.json"
DEFAULT_PIC_SUBDIR = "../Pic"


class FeishuDocConvertError(RuntimeError):
    """文档转换错误。"""


class FeishuDocConverter:
    """飞书文档 → 本地 Markdown 转换器。

    用法:
        converter = FeishuDocConverter(bundle)
        result = converter.convert("https://bytedance.feishu.cn/docx/xxx")
        result = converter.convert_batch(["url1", "url2"], force=True)
    """

    def __init__(self, bundle: ConfigBundle) -> None:
        self._bundle = bundle
        self._client = FeishuClient(as_user=True)
        self._pic_dir = self._resolve_pic_dir()
        self._dedup_path = self._resolve_dedup_path()

    # ── 对外接口 ────────────────────────────────────────────

    def convert(self, url: str, *,
                output: str = "to_source",
                force: bool = False,
                dry_run: bool = False) -> Dict[str, Any]:
        """转换单个飞书文档并归档。

        Args:
            url: 飞书文档 URL 或 token
            output: "to_source" 自动路由 | 具体文件路径
            force: 跳过排重检查
            dry_run: 预览不写入

        Returns:
            {"status": "success"|"skipped"|"error", ...}
        """
        try:
            token = self._client.parse_doc_url(url)
        except FeishuClientError as e:
            return {"status": "error", "url": url, "error": str(e)}

        # 排重检查
        if not force:
            existing = self._check_dedup(url)
            if existing:
                return {
                    "status": "skipped", "url": url, "token": token,
                    "reason": f"⏭️ 已提取于 {existing.get('extracted_at', '?')}，使用 --force 覆盖",
                    "output": existing.get("local_path", ""),
                }

        # 拉取文档
        try:
            doc = self._client.fetch_doc_content(token)
        except FeishuClientError as e:
            return {"status": "error", "url": url, "token": token, "error": f"拉取失败: {e}"}
        if not doc.get("content", "").strip():
            return {"status": "error", "url": url, "token": token, "error": "文档内容为空"}

        title = doc.get("title", "") or ""
        # 若飞书 API 未返回标题，从正文第一个 # 标题提取
        if not title:
            title = self._extract_title_from_content(doc.get("content", "")) or "未命名文档"
        author = doc.get("owner_name", "").strip() or ""

        # 处理内容：图片下载 + 元信息插入
        try:
            processed = self._process_content(doc["content"], token, title, url, author=author)
        except FeishuClientError as e:
            return {"status": "error", "url": url, "token": token, "error": f"内容处理失败: {e}"}

        # 路由 / 输出路径
        if output == "to_source":
            route = self._classify(doc["content"], title)
            target_dir = self._resolve_routed_source_dir(route)
            filename = self._generate_filename(doc, route, title, author=author)
            output_path = target_dir / filename
            route_name = route
        else:
            output_path = Path(output)
            route_name = ""
            route = ""

        # 写入
        if dry_run:
            return {
                "status": "dry_run", "url": url, "token": token, "title": title,
                "route": route, "output": str(output_path),
                "content_preview": processed.get("content", "")[:200],
            }

        try:
            safe_write_text(output_path, processed["content"], self._bundle,
                            allow_existing_outside=True)
        except Exception as e:
            return {"status": "error", "url": url, "token": token, "error": f"写入失败: {e}"}

        # 更新排重索引
        self._update_dedup(url, token, title, str(output_path))

        images = processed.get("images_downloaded", [])
        return {
            "status": "success", "url": url, "token": token, "title": title,
            "route": route, "output": str(output_path),
            "images_downloaded": len(images),
        }

    def convert_batch(self, urls: List[str], **kwargs) -> List[Dict[str, Any]]:
        """批量转换多个文档。"""
        results = []
        for url in urls:
            result = self.convert(url, **kwargs)
            results.append(result)
        return results

    def convert_from_config(self, **kwargs) -> List[Dict[str, Any]]:
        """从配置文件读取文档列表并转换。"""
        cfg = self._load_config()
        docs = cfg.get("documents", [])
        urls = [d["url"] for d in docs if d.get("enabled", True)]
        if not urls:
            return [{"status": "skipped", "reason": "配置中无可用的文档(所有 enabled=false 或列表为空)"}]
        return self.convert_batch(urls, **kwargs)

    # ── 内部处理 ────────────────────────────────────────────

    def _process_content(self, raw_md: str, doc_token: str,
                         title: str, source_url: str, *,
                         author: str = "") -> Dict[str, Any]:
        """处理文档内容：下载图片 + 插入元信息。

        返回:
            {"content": str, "images_downloaded": [str, ...]}
        """
        # Step 1: 下载图片并替换引用
        md_with_images, downloaded = self._process_images(raw_md, doc_token)

        # Step 2: 构建元信息块
        now_iso = datetime.now().astimezone().isoformat()
        meta_lines = [
            "## 文档信息",
            f"- 来源链接：{source_url}",
            f"- 提取时间：{now_iso}",
            f"- 来源类型：飞书文档",
            f"- 提取工具：feishu-doc-convert",
        ]
        if author:
            meta_lines.insert(1, f"- 作者：{author}")
        meta_block = "\n".join(meta_lines) + "\n\n"

        # Step 3: 去掉正文中可能已有的重复信息，插入元信息块
        # 确保元信息插在标题之后、正文之前
        content = self._insert_after_title(md_with_images, meta_block)

        return {"content": content, "images_downloaded": downloaded}

    def _process_images(self, md: str, doc_token: str) -> Tuple[str, List[str]]:
        """查找 Markdown 中的图片引用，下载到 Pic 目录，替换为 Obsidian 链接。

        返回:
            (处理后的 Markdown, 已下载图片路径列表)
        """
        downloaded = []
        seq = 0

        def _replacer(m: re.Match) -> str:
            nonlocal seq
            alt = m.group(1)
            ref = m.group(2).strip()

            # 跳过已经是 Obsidian 引用的或外链
            if ref.startswith("[[") or ref.startswith("http"):
                # 外链保留原样
                if ref.startswith("http"):
                    return m.group(0)
                # Obsidian 引用保留
                return m.group(0)

            # 尝试下载
            seq += 1
            ext = _guess_image_ext(ref)
            local_name = f"feishu_{doc_token}_{seq:03d}{ext}"
            pic_path = self._pic_dir / local_name

            try:
                self._client.download_image(ref, str(pic_path))
                downloaded.append(str(pic_path))
            except FeishuClientError:
                # 下载失败时保留原始引用，不阻塞流程
                return m.group(0)

            return f"![[{local_name}]]"

        result = _IMAGE_PATTERN.sub(_replacer, md)
        return result, downloaded

    def _insert_after_title(self, body: str, insert_block: str) -> str:
        """在 Markdown 标题（# 开头第一行）后插入内容。"""
        lines = body.split("\n")
        # 找到第一个 # 标题行
        title_idx = -1
        for i, line in enumerate(lines):
            if line.startswith("# "):
                title_idx = i
                break
        if title_idx >= 0:
            # 在标题行后插入
            insert_pos = title_idx + 1
            # 跳过标题后的空行
            while insert_pos < len(lines) and lines[insert_pos].strip() == "":
                insert_pos += 1
            result = "\n".join(lines[:insert_pos] + [insert_block] + lines[insert_pos:])
        else:
            # 无标题，直接插在开头
            result = insert_block + body
        return result

    # ── 路由 ────────────────────────────────────────────────

    def _classify(self, content: str, title: str) -> str:
        """根据文档内容判断路由目标目录。

        基于关键词规则快速分类，不做 LLM 调用（文档内容通常较长，
        用轻量规则判断更高效）。"""
        text = (title + "\n" + content[:2000]).lower()

        # 关键词权重评分
        scores = {
            "03-方案报告": 0,
            "04-讨论思考": 0,
            "05-会议纪要": 0,
            "08-参考资料": 0,
        }

        # 方案/需求类
        if any(kw in text for kw in ["需求文档", "prd", "技术方案", "设计文档",
                                       "架构设计", "项目计划", "排期"]):
            scores["03-方案报告"] += 3
        if any(kw in text for kw in ["方案", "设计", "架构", "需求", "计划"]):
            scores["03-方案报告"] += 1

        # 会议类
        if any(kw in text for kw in ["会议纪要", "会议记录", "周会", "评审",
                                       "meeting", "会议", "纪要"]):
            scores["05-会议纪要"] += 2

        # 讨论类
        if any(kw in text for kw in ["讨论", "分析", "调研", "对齐", "思考",
                                       "脑暴", "draft", "草稿"]):
            scores["04-讨论思考"] += 2

        # 参考资料类
        if any(kw in text for kw in ["参考资料", "学习", "培训", "分享",
                                       "峰会", "演讲", "论文", "行业"]):
            scores["08-参考资料"] += 2

        # 项目/实施类（新增，硬件类文档多属此类）
        if any(kw in text for kw in ["项目", "实施", "硬件", "交付", "推广", "落地", "设备验证"]):
            scores["03-方案报告"] += 2
        if any(kw in text for kw in ["进度", "规划", "里程碑", "目标"]):
            scores["03-方案报告"] += 1

        # 参考资料类降低权重，作为兜底而非默认
        # 取最高分
        best = max(scores, key=scores.get)
        if scores[best] == 0:
            # 无法判断时默认 03-方案报告（大多数工作文档是方案/计划类）
            return "03-方案报告"
        return best

    def _extract_title_from_content(self, content: str) -> str:
        """从正文第一个 # 标题提取文档标题。"""
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("# ") and not line.startswith("## "):
                return line[2:].strip()
        return ""

    def _generate_filename(self, doc: Dict[str, Any], route: str, title: str = "", *,
                           author: str = "") -> str:
        """生成符合命名规范的本地文件名。

        优先使用文档创建时间，fallback 到提取时间。
        格式：{YYYYmmdd}-{主题}-{作者}.md
        """
        raw_title = title or doc.get("title", "") or "未命名"

        # 日期
        create_time = doc.get("create_time", "")
        date_str = self._extract_date(create_time) if create_time else datetime.now().strftime("%Y%m%d")

        # 清理标题
        clean = self._sanitize_title(raw_title)

        # 作者
        author_part = self._sanitize_title(author) if author else ""

        # 文件名：{YYYYmmdd}-{主题}-{作者}.md
        if author_part:
            return f"{date_str}-{clean}-{author_part}.md"
        return f"{date_str}-{clean}.md"

    # ── 排重 ────────────────────────────────────────────────

    def _check_dedup(self, source_url: str) -> Optional[Dict[str, Any]]:
        """检查 URL 是否已提取过。"""
        index = self._load_dedup_index()
        for item in index.get("items", []):
            if item.get("source_url") == source_url:
                return item
        return None

    def _update_dedup(self, source_url: str, doc_token: str,
                      title: str, local_path: str) -> None:
        """更新排重索引。"""
        index = self._load_dedup_index()
        # 移除旧的同 URL 记录
        index["items"] = [it for it in index.get("items", [])
                          if it.get("source_url") != source_url]
        # 添加新记录
        index.setdefault("items", []).append({
            "source_url": source_url,
            "doc_token": doc_token,
            "title": title,
            "local_path": local_path,
            "extracted_at": datetime.now().astimezone().isoformat(),
        })
        self._save_dedup_index(index)

    # ── 文件路径辅助 ────────────────────────────────────────

    def _resolve_pic_dir(self) -> Path:
        """确定图片存储目录。"""
        # 优先从配置文件读取
        cfg = self._load_config()
        pic = cfg.get("pic_dir", "")
        if pic:
            p = Path(pic).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            return p

        # fallback: SOURCE/../Pic
        src_root = self._resolve_source_root()
        if src_root:
            p = src_root.parent / "Pic"
            p.mkdir(parents=True, exist_ok=True)
            return p
        # 最后 fallback 到项目目录
        p = self._bundle.root / "data" / "pic"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _resolve_dedup_path(self) -> Path:
        """确定排重索引路径。"""
        cfg = self._load_config()
        path_str = cfg.get("doc_convert", {}).get("dedup_index", "")
        if path_str:
            p = Path(path_str)
            if not p.is_absolute():
                p = self._bundle.root / p
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
        return self._bundle.root / DEFAULT_DEDUP_INDEX

    def _resolve_source_root(self) -> Optional[Path]:
        """获取 SOURCE 根目录。"""
        ds = self._bundle.data_source
        for cfg in ds.get("sources", {}).values():
            if cfg.get("enabled") and cfg.get("path"):
                p = Path(cfg["path"]).resolve()
                if p.exists():
                    return p
        return None

    def _resolve_routed_source_dir(self, route_target: str) -> Path:
        """根据路由目标确定 SOURCE 子目录。"""
        src = self._resolve_source_root()
        if src:
            d = src / route_target
            d.mkdir(parents=True, exist_ok=True)
            return d
        return self._bundle.root / "output"

    # ── 配置加载 ────────────────────────────────────────────

    def _load_config(self) -> Dict[str, Any]:
        """从 bundle 或文件加载 feishu_ingest 配置。"""
        cfg = self._bundle.feishu_ingest
        if cfg:
            return cfg
        return {}

    def _load_dedup_index(self) -> Dict[str, Any]:
        """加载排重索引。"""
        p = self._dedup_path
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"version": "1.0", "items": []}

    def _save_dedup_index(self, index: Dict[str, Any]) -> None:
        """保存排重索引。"""
        self._dedup_path.parent.mkdir(parents=True, exist_ok=True)
        self._dedup_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 工具方法 ────────────────────────────────────────────

    @staticmethod
    def _extract_date(time_str: str) -> str:
        """从 ISO 时间字符串中提取 YYYYmmdd 格式日期。"""
        try:
            dt = datetime.fromisoformat(time_str)
            return dt.strftime("%Y%m%d")
        except (ValueError, TypeError):
            return datetime.now().strftime("%Y%m%d")

    @staticmethod
    def _sanitize_title(title: str) -> str:
        """清理标题为安全的文件名。"""
        # 去除非法字符
        clean = re.sub(r'[\\/:*?"<>|]', "", title)
        # 空格 → 短横线
        clean = re.sub(r"\s+", "-", clean.strip())
        # 多个连续短横线 → 单个
        clean = re.sub(r"-{2,}", "-", clean)
        # 长度限制
        if len(clean) > 80:
            clean = clean[:80].rstrip("-")
        return clean if clean else "未命名"


def _guess_image_ext(ref: str) -> str:
    """从图片引用中猜测扩展名。"""
    ext = Path(ref).suffix.lower()
    if ext in _PIC_EXTENSIONS:
        return ext
    return ".png"  # fallback

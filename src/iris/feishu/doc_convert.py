"""飞书文档转本地 Markdown — 转换、归档、排重。"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import URLError
from urllib.request import Request, urlopen

from iris.config.loader import ConfigBundle
from iris.feishu.client import FeishuClient, FeishuClientError

logger = logging.getLogger(__name__)
from iris.feishu._shared import (
    resolve_pic_dir, resolve_source_sub_dir, resolve_source_root,
    resolve_dedup_path, load_dedup_index, save_dedup_index,
    upsert_dedup_item, sanitize_title, extract_date, now_iso,
)
from iris.core.write_guard import safe_write_text

# ── 常量 ────────────────────────────────────────────────────

_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
from iris.utils.constants import IMAGE_EXTENSIONS_WITH_SVG as _PIC_EXTENSIONS


class FeishuDocConvertError(RuntimeError):
    """文档转换错误。"""


class FeishuDocConverter:
    """飞书文档 → 本地 Markdown 转换器。"""

    def __init__(self, bundle: ConfigBundle) -> None:
        self._bundle = bundle
        self._client = FeishuClient(as_user=True)
        self._pic_dir = resolve_pic_dir(bundle)
        self._dedup_path = resolve_dedup_path(
            bundle, "doc_convert.dedup_index", "data/dedup/feishu_doc_index.json")

    # ── 对外接口 ────────────────────────────────────────────

    def convert(self, url: str, *,
                output: str = "to_source",
                force: bool = False,
                dry_run: bool = False) -> Dict[str, Any]:
        """转换单个飞书文档并归档。"""
        try:
            token = self._client.parse_doc_url(url)
        except FeishuClientError as e:
            return {"status": "error", "url": url, "error": str(e)}

        # 1. 排重检查
        if not force:
            existing = self._check_dedup(url)
            if existing:
                return {
                    "status": "skipped", "url": url, "token": token,
                    "reason": f"⏭️ 已提取于 {existing.get('extracted_at', '?')}，使用 --force 覆盖",
                    "output": existing.get("local_path", ""),
                }

        # 2. 拉取文档
        try:
            doc = self._client.fetch_doc_content(token)
        except FeishuClientError as e:
            return {"status": "error", "url": url, "token": token, "error": f"拉取失败: {e}"}
        if not doc.get("content", "").strip():
            return {"status": "error", "url": url, "token": token, "error": "文档内容为空"}

        # 3. 提取元信息
        title = doc.get("title", "") or self._extract_title_from_content(doc.get("content", "")) or "未命名文档"
        author = doc.get("owner_name", "").strip() or ""
        create_time = doc.get("create_time", "") or ""

        # 3b. fallback：docs +fetch 可能不含 create_time / owner_name，通过 search 补充
        if not author or not create_time:
            meta = self._resolve_doc_meta_fallback(url, author, create_time, title)
            if not author and meta.get("owner_name"):
                author = meta["owner_name"]
            if not create_time and meta.get("create_time"):
                create_time = meta["create_time"]

        # 4. 计算文件名 stem
        date_str = extract_date(create_time) or datetime.now().strftime("%Y%m%d")
        clean_title = sanitize_title(title, max_len=60)
        clean_author = sanitize_title(author) if author else ""
        stem = f"{date_str}-{clean_title}" + (f"-from{clean_author}" if clean_author else "")

        # 5. 处理内容（图片下载 + 元信息）
        try:
            processed = self._process_content(doc["content"], token, title, url,
                                              author=author, subdir=stem)
        except FeishuClientError as e:
            return {"status": "error", "url": url, "token": token, "error": f"内容处理失败: {e}"}

        # 6. 路由
        if output == "to_source":
            route = self._classify(doc["content"], title)
            output_path = resolve_source_sub_dir(self._bundle, route,
                                                 f"{stem}.md")
        else:
            output_path = Path(output)
            route = ""

        # 7. 写入
        if dry_run:
            return {
                "status": "dry_run", "url": url, "token": token, "title": title,
                "route": route, "output": str(output_path),
                "content_preview": processed.get("content", "")[:200],
            }

        content = processed.get("content")
        if content is None:
            return {"status": "error", "url": url, "token": token, "error": "内容处理结果缺少 content 字段"}

        # ── 注入 wikilink 交叉引用 ──────────────────────
        try:
            _wiki_root = None
            if self._bundle.wiki:
                _wiki_root = Path(self._bundle.wiki["wiki_root"]).resolve()
            if _wiki_root and _wiki_root.exists():
                from iris.wiki.wikilink_injector import WikilinkInjector
                _injector = WikilinkInjector(_wiki_root)
                content = _injector.inject(content)
        except Exception:
            pass  # wikilink 注入失败不应阻塞文档转换

        # ── 注入 frontmatter ──────────────────────────────
        try:
            from iris.core.frontmatter import inject_frontmatter
            _fm_display_date = date_str if len(date_str) >= 8 else datetime.now().strftime("%Y-%m-%d")
            if len(_fm_display_date) == 8 and _fm_display_date.isdigit():
                _fm_display_date = f"{_fm_display_date[:4]}-{_fm_display_date[4:6]}-{_fm_display_date[6:8]}"
            _fm_fields = {
                "title": title,
                "date": _fm_display_date,
                "type": "飞书文档",
                "author": author or "",
                "source_url": url,
                "doc_token": token,
                "route": route or "",
            }
            content = inject_frontmatter(content, _fm_fields)
        except Exception:
            pass  # frontmatter 注入失败不应阻塞文档转换

        try:
            safe_write_text(output_path, content, self._bundle,
                            allow_existing_outside=True)
        except OSError as e:
            return {"status": "error", "url": url, "token": token, "error": f"写入失败: {e}"}

        # 8. 更新排重
        self._update_dedup(url, token, title, str(output_path))
        images_count = len(processed.get("images_downloaded", []))

        return {
            "status": "success", "url": url, "token": token, "title": title,
            "route": route, "output": str(output_path),
            "images_downloaded": images_count,
        }

    def convert_batch(self, urls: List[str], **kwargs) -> List[Dict[str, Any]]:
        """批量转换多个文档。"""
        return [self.convert(url, **kwargs) for url in urls]

    # ── 元信息 fallback ───────────────────────────────────

    def _resolve_doc_meta_fallback(self, url: str,
                                   author_hint: str = "",
                                   create_time_hint: str = "",
                                   title: str = "") -> Dict[str, str]:
        """通过 docs +search 补充 docs +fetch 可能缺失的作者和创建时间。

        仅在主流程中 author 或 create_time 为空时调用。
        """
        meta: Dict[str, str] = {}
        try:
            token = self._client.parse_doc_url(url)
        except FeishuClientError:
            return meta

        if not author_hint or not create_time_hint:
            search_meta = self._client.search_doc_meta(token, title)
            if not author_hint and search_meta.get("owner_name"):
                meta["owner_name"] = search_meta["owner_name"]
            if not create_time_hint and search_meta.get("create_time"):
                meta["create_time"] = search_meta["create_time"]

        return meta

    def convert_from_config(self, **kwargs) -> List[Dict[str, Any]]:
        """从配置文件读取文档列表并转换。"""
        cfg = self._bundle.feishu_ingest or {}
        docs = cfg.get("doc_convert", {}).get("documents", [])
        urls = [d["url"] for d in docs if d.get("enabled", True)]
        if not urls:
            return [{"status": "skipped", "reason": "配置中无可用的文档(所有 enabled=false 或列表为空)"}]
        return self.convert_batch(urls, **kwargs)

    # ── 内容处理 ────────────────────────────────────────────

    def _process_content(self, raw_md: str, doc_token: str,
                         title: str, source_url: str, *,
                         author: str = "", subdir: str = "") -> Dict[str, Any]:
        """下载图片 + 构建元信息块 → 合并输出。"""
        md_with_images, downloaded = self._process_images(raw_md, doc_token, subdir=subdir)

        meta_lines = [
            "## 文档信息",
            f"- 来源链接：{source_url}",
            f"- 提取时间：{now_iso()}",
            "- 来源类型：飞书文档",
            "- 提取工具：feishu-doc-convert",
        ]
        if author:
            meta_lines.insert(1, f"- 作者：{author}")
        meta_block = "\n".join(meta_lines) + "\n\n"

        content = self._insert_after_title(md_with_images, meta_block)
        return {"content": content, "images_downloaded": downloaded}

    def _process_images(self, md: str, doc_token: str, *, subdir: str = "") -> Tuple[str, List[str]]:
        """下载图片到 Pic/{subdir}/，替换 Markdown 引用为 Obsidian 链接。"""
        downloaded: List[str] = []
        seq = 0
        img_subdir = subdir if subdir else doc_token
        img_dir = self._pic_dir / img_subdir

        def _replacer(m: re.Match) -> str:
            nonlocal seq
            ref = m.group(2).strip()
            if ref.startswith("[["):  # 已处理的 Obsidian 引用，跳过
                return m.group(0)

            seq += 1
            ext = _guess_image_ext(ref)
            local_name = f"feishu_{seq:03d}{ext}"
            pic_path = img_dir / local_name

            # 安全检查：防止路径逃逸出 img_dir（深度防御）
            try:
                if not pic_path.resolve().is_relative_to(img_dir.resolve()):
                    return m.group(0)
            except (OSError, ValueError):
                return m.group(0)

            try:
                self._download_image_to(ref, str(pic_path))
                downloaded.append(str(pic_path))
                return f"![[{img_subdir}/{local_name}]]"
            except (OSError, URLError, FeishuClientError) as exc:
                logger.debug("图片下载跳过 [%s]: %s", ref, exc)
                return m.group(0)  # 下载失败不阻塞

        result = _IMAGE_PATTERN.sub(_replacer, md)
        return result, downloaded

    def _download_image_to(self, ref: str, save_path: str) -> None:
        """下载图片（HTTP URL 或飞书 file_token）。"""
        if ref.startswith("http"):
            req = Request(ref, headers={"User-Agent": "Iris/3.2"})
            with urlopen(req, timeout=30) as resp:
                data = resp.read()
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            Path(save_path).write_bytes(data)
        else:
            self._client.download_image(ref, save_path)

    @staticmethod
    def _insert_after_title(body: str, insert_block: str) -> str:
        """在正文第一个 # 标题后插入元信息块。"""
        lines = body.split("\n")
        title_idx = -1
        for i, line in enumerate(lines):
            if line.startswith("# "):
                title_idx = i
                break
        if title_idx >= 0:
            insert_pos = title_idx + 1
            while insert_pos < len(lines) and lines[insert_pos].strip() == "":
                insert_pos += 1
            return "\n".join(lines[:insert_pos] + [insert_block] + lines[insert_pos:])
        return insert_block + body

    @staticmethod
    def _extract_title_from_content(content: str) -> str:
        """从正文第一个 # 标题提取文档标题。"""
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("# ") and not line.startswith("## "):
                return line[2:].strip()
        return ""

    # ── 路由 — 配置驱动的关键词评分 ────────────────────

    def _classify(self, content: str, title: str) -> str:
        """根据文档内容关键词评分判定路由目标。

        关键词从 meeting_routes.json 的 route_targets 动态读取，
        fallback 到内置默认词表。
        """
        text = (title + "\n" + content[:2000]).lower()
        targets = self._load_route_targets()
        scores: Dict[str, int] = {d: 0 for d in targets}

        for dir_name, info in targets.items():
            for kw in info.get("keywords", []):
                if kw.lower() in text:
                    scores[dir_name] += 2 if len(kw) >= 3 else 1

        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else "03-方案报告"

    def _load_route_targets(self) -> Dict[str, Any]:
        """从 meeting_routes.json 加载路由目标及关键词。"""
        mr = self._bundle.meeting_routes or {}
        targets = mr.get("route_targets", {})
        if targets:
            return targets
        # 内置 fallback（脱敏）
        return {
            "03-方案报告": {
                "keywords": ["需求文档", "prd", "技术方案", "设计文档",
                             "架构设计", "项目计划", "方案", "设计", "架构",
                             "项目", "实施", "硬件", "交付", "推广", "落地"],
                "naming": "YYYYMMDD-{topic}",
            },
            "04-讨论思考": {
                "keywords": ["讨论", "分析", "调研", "对齐", "思考", "脑暴", "draft", "草稿"],
                "naming": "YYYYMMDD-internal-discussion-{topic}",
            },
            "05-会议纪要": {
                "keywords": ["会议纪要", "会议记录", "周会", "评审", "meeting", "会议", "纪要"],
                "naming": "YYYYMMDD-{type}-{topic}",
            },
            "08-参考资料": {
                "keywords": ["参考资料", "学习", "培训", "分享", "峰会", "演讲", "论文", "行业"],
                "naming": "YYYYMMDD-{source}-{topic}",
            },
        }

    # ── 排重 ────────────────────────────────────────────────

    def _check_dedup(self, source_url: str) -> Optional[Dict[str, Any]]:
        index = load_dedup_index(self._dedup_path)
        for item in index.get("items", []):
            if item.get("source_url") == source_url:
                return item
        return None

    def _update_dedup(self, source_url: str, doc_token: str,
                      title: str, local_path: str) -> None:
        index = load_dedup_index(self._dedup_path)
        upsert_dedup_item(index, "", {
            "dedup_key": source_url,
            "source_url": source_url,
            "doc_token": doc_token,
            "title": title,
            "local_path": local_path,
            "extracted_at": now_iso(),
        })
        save_dedup_index(self._dedup_path, index)


def _guess_image_ext(ref: str) -> str:
    """从图片引用中猜测扩展名。"""
    ext = Path(ref).suffix.lower()
    return ext if ext in _PIC_EXTENSIONS else ".png"

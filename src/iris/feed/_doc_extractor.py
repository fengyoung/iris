"""信息汇聚管道 — 文档提取。

从话题关联的消息中收集飞书文档链接，调用 FeishuDocConverter
转换为本地 Markdown，供简报生成器关联引用。

职责：
  1. 遍历话题消息，提取飞书文档 URL（docx/wiki/sheet/base）
  2. 单次 run 内 set 去重
  3. 调用 FeishuDocConverter 转换（复用其跨次排重机制）
  4. 失败静默跳过（warn 日志），不影响其他文档和话题
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Set

from iris.feed._types import ConvertedDoc, DetectedTopic

logger = logging.getLogger(__name__)

# feishu.cn 四类文档链接
_FEISHU_DOC_URL_PATTERN = re.compile(
    r'https?://[^\s]*feishu[^\s]*/(docx|wiki|sheet|base)/\w+'
)


def _extract_doc_urls(text: str) -> List[str]:
    """从文本中提取飞书文档链接（去重保持顺序）。"""
    seen: Set[str] = set()
    result: List[str] = []
    for m in _FEISHU_DOC_URL_PATTERN.finditer(text):
        url = m.group(0)
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


def _collect_topic_urls(topics: List[DetectedTopic]) -> Dict[str, List[str]]:
    """从话题消息中收集所有飞书文档链接。

    返回 {url: [topic_title, ...]}，记录每个 URL 被哪些话题引用。
    """
    url_topics: Dict[str, List[str]] = {}
    for topic in topics:
        for msg in topic.messages:
            for url in _extract_doc_urls(msg.content):
                if url not in url_topics:
                    url_topics[url] = []
                if topic.title not in url_topics[url]:
                    url_topics[url].append(topic.title)
    return url_topics


class DocExtractor:
    """文档提取器 — 发现链接 + 调用 FeishuDocConverter 转换。

    单次 run 内 set 去重，跨次排重复用 FeishuDocConverter 的
    dedup_index（feishu_doc_index.json）。
    """

    def __init__(self, source_dir: Path, bundle: Any = None):
        """初始化。

        Args:
            source_dir: SOURCE 根目录路径
            bundle: ConfigBundle 实例（传给 FeishuDocConverter）
        """
        self._source_dir = source_dir
        self._bundle = bundle

    def extract(
        self,
        topics: List[DetectedTopic],
        dry_run: bool = False,
        max_docs: int = 10,
    ) -> List[ConvertedDoc]:
        """从话题消息中提取并转换飞书文档。

        Args:
            topics: 已检测的话题列表（含消息）
            dry_run: 仅收集链接不实际转换
            max_docs: 单次最多转换文档数（0 表示不限制）

        Returns:
            ConvertedDoc 列表（成功转换的文档）
        """
        url_topics = _collect_topic_urls(topics)
        if not url_topics:
            logger.info("Step 5: 未发现飞书文档链接，跳过")
            return []

        total_urls = len(url_topics)
        logger.info("Step 5: 发现 %d 个飞书文档链接（去重后）", total_urls)
        for url, topic_titles in url_topics.items():
            logger.debug("  %s → %s", url[:80], ", ".join(topic_titles))

        # 数量上限截断（保持字典顺序，截取前 N 个）
        if max_docs > 0 and len(url_topics) > max_docs:
            truncated = dict(list(url_topics.items())[:max_docs])
            logger.info("文档数量 %d → 截断至 %d（doc_extract_max）", total_urls, max_docs)
            url_topics = truncated

        if dry_run:
            logger.info("(dry-run) 将提取 %d 份文档:", len(url_topics))
            for url in url_topics:
                logger.info("  📄 %s", url)
            # dry-run 返回占位对象供 CLI/Skill 预览
            return [
                ConvertedDoc(
                    original_url=url,
                    local_path=Path(),
                    relative_path="",
                    title="",
                    source_chat=", ".join(topic_titles),
                )
                for url, topic_titles in url_topics.items()
            ]

        # 实际转换
        converter = self._get_converter()
        converted: List[ConvertedDoc] = []
        success = 0
        skipped = 0
        failed = 0

        for url, topic_titles in url_topics.items():
            try:
                result = converter.convert(url)  # force=False 自动排重
            except Exception as e:
                logger.warning("文档转换异常 [%s]: %s", url[:80], e)
                failed += 1
                continue

            status = result.get("status", "error")
            if status == "success":
                output_path = result.get("output", "")
                local_path = Path(output_path) if output_path else None
                if local_path and local_path.exists():
                    try:
                        rel_path = str(local_path.relative_to(self._source_dir))
                    except ValueError:
                        rel_path = output_path
                    converted.append(ConvertedDoc(
                        original_url=url,
                        local_path=local_path,
                        relative_path=rel_path,
                        title=str(result.get("title", "")),
                        source_chat=", ".join(topic_titles),
                    ))
                    success += 1
                    logger.info("  ✅ 已转换: %s → %s", result.get("title", url[:60]), rel_path)
                else:
                    logger.warning("  ⚠️ 转换成功但路径无效: %s", output_path)
                    failed += 1
            elif status == "skipped":
                # 跨次排重命中，复用已有路径
                output_path = result.get("output", "")
                if output_path:
                    local_path = Path(output_path)
                    try:
                        rel_path = str(local_path.relative_to(self._source_dir))
                    except ValueError:
                        rel_path = output_path
                    # 从文件名推断标题（格式: YYYYMMDD-标题.md）
                    title = result.get("title", "")
                    if not title:
                        stem = local_path.stem
                        # 剥离日期前缀 YYYYMMDD-
                        if len(stem) > 9 and stem[:8].isdigit() and stem[8] == "-":
                            title = stem[9:]
                        else:
                            title = stem
                    converted.append(ConvertedDoc(
                        original_url=url,
                        local_path=local_path,
                        relative_path=rel_path,
                        title=title,
                        source_chat=", ".join(topic_titles),
                    ))
                    skipped += 1
                    logger.info("  ⏭️ 已存在: %s → %s", result.get("reason", url[:60]), rel_path)
                else:
                    skipped += 1
                    logger.info("  ⏭️ 已存在（无路径信息）")
            else:
                error_msg = result.get("error", result.get("reason", "未知错误"))
                logger.warning("  ❌ 转换失败 [%s]: %s", url[:60], error_msg)
                failed += 1

        logger.info("Step 5 完成: ✅ %d 成功, ⏭️ %d 跳过, ❌ %d 失败, 共 %d 份文档",
                     success, skipped, failed, len(converted))
        return converted

    def _get_converter(self):
        """延迟导入 FeishuDocConverter，避免循环依赖。"""
        from iris.feishu.doc_convert import FeishuDocConverter
        if self._bundle is not None:
            return FeishuDocConverter(self._bundle)
        # fallback: 构造最小 bundle
        from iris.config.loader import ConfigBundle
        return FeishuDocConverter(ConfigBundle(self._source_dir.parent))

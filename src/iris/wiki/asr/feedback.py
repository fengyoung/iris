"""ASR 反馈数据模型 — JSONL 纠错记录读写 + 数据提取。

为 Phase 1 反向优化准备数据基础设施。
每条校正由 iris-asr-corrector 实时写入，纯本地存储，不上传。

用法:
    from iris.wiki.asr.feedback import save_correction, load_corrections

    record = AsrCorrection(timestamp="...", raw_text="...", ...)
    save_correction(record, "data/asr_feedback.jsonl")

    corrections = load_corrections("data/asr_feedback.jsonl")
    mappings = extract_mappings_from_corrections(corrections)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from ._types import AsrCorrection, AsrTerm


def save_correction(record: AsrCorrection, feedback_path: str) -> None:
    """追加一条校正记录到 JSONL 文件。

    Args:
        record: AsrCorrection 实例
        feedback_path: JSONL 文件路径（相对于项目根或绝对路径）
    """
    path = Path(feedback_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    record_dict = {
        "timestamp": record.timestamp,
        "raw_text": record.raw_text,
        "fast_corrected": record.fast_corrected,
        "full_corrected": record.full_corrected,
        "mode": record.mode,
        "corrections_applied": record.corrections_applied,
        "llm_time_ms": record.llm_time_ms,
    }
    if record.context_ab is not None:
        record_dict["context_ab"] = record.context_ab
    line = json.dumps(record_dict, ensure_ascii=False)

    import fcntl
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(line + "\n")
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def load_corrections(feedback_path: str) -> List[AsrCorrection]:
    """从 JSONL 文件加载全部校正记录。

    Args:
        feedback_path: JSONL 文件路径

    Returns:
        AsrCorrection 列表（按写入时间排序）
    """
    path = Path(feedback_path)
    if not path.exists():
        return []

    records: List[AsrCorrection] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                records.append(
                    AsrCorrection(
                        timestamp=data.get("timestamp", ""),
                        raw_text=data.get("raw_text", ""),
                        fast_corrected=data.get("fast_corrected", ""),
                        full_corrected=data.get("full_corrected", ""),
                        mode=data.get("mode", "full"),
                        corrections_applied=data.get("corrections_applied", []),
                        llm_time_ms=data.get("llm_time_ms", 0),
                    )
                )
            except (json.JSONDecodeError, KeyError):
                continue

    return records


# [LLM] 标记前缀，用于区分词典命中 vs LLM 发现的修正
_LLM_PREFIX = "[LLM] "


def extract_mappings_from_corrections(
    corrections: List[AsrCorrection],
) -> Dict[str, List[str]]:
    """从校正记录中提取 (正确词 → 误识别词列表) 的映射。

    分析维度：
    - corrections_applied：替换词典命中的规则（格式 "误→正"）
    - [LLM] 标记：LLM 修正了词典未覆盖的错误（格式 "[LLM] 误→正"）
      此前缀会被自动剥离，确保解析结果正确。

    Args:
        corrections: AsrCorrection 列表

    Returns:
        字典 {正确词: [误识别词1, 误识别词2, ...]}
    """
    mappings: Dict[str, List[str]] = {}

    for c in corrections:
        for applied in c.corrections_applied:
            # 剥离 [LLM] 前缀（LLM 发现的修正）
            if applied.startswith(_LLM_PREFIX):
                applied = applied[len(_LLM_PREFIX):]
            # 格式: "误识别词→正确词"
            parts = applied.split("→", 1)
            if len(parts) == 2:
                wrong, right = parts[0].strip(), parts[1].strip()
                if not wrong or not right:
                    continue
                if right not in mappings:
                    mappings[right] = []
                if wrong not in mappings[right]:
                    mappings[right].append(wrong)

    return mappings


def extract_llm_discoveries(
    corrections: List[AsrCorrection],
) -> Dict[str, List[str]]:
    """仅提取 LLM 发现的新修正（[LLM] 标记的条目），词典命中的除外。

    用于 Phase 1 反向优化：高频 LLM 修正 → 提升为替换词典规则。

    Args:
        corrections: AsrCorrection 列表

    Returns:
        字典 {正确词: [LLM发现的误识别词列表]}
    """
    discoveries: Dict[str, List[str]] = {}

    for c in corrections:
        for applied in c.corrections_applied:
            if not applied.startswith(_LLM_PREFIX):
                continue
            applied_clean = applied[len(_LLM_PREFIX):]
            parts = applied_clean.split("→", 1)
            if len(parts) == 2:
                wrong, right = parts[0].strip(), parts[1].strip()
                if not wrong or not right:
                    continue
                if right not in discoveries:
                    discoveries[right] = []
                if wrong not in discoveries[right]:
                    discoveries[right].append(wrong)

    return discoveries


def compute_hit_frequency(
    corrections: List[AsrCorrection],
) -> Dict[str, int]:
    """统计每条替换规则的命中次数。

    Args:
        corrections: AsrCorrection 列表

    Returns:
        {"规则字符串": 命中次数}
    """
    freq: Dict[str, int] = {}
    for c in corrections:
        for applied in c.corrections_applied:
            freq[applied] = freq.get(applied, 0) + 1
    return freq


def apply_feedback_to_dict(
    corrections: List[AsrCorrection],
    current_terms: List[AsrTerm],
) -> List[AsrTerm]:
    """将反馈数据中的映射合并到替换词典。

    仅添加新映射，不删除现有映射（删除由 Phase 1 的淘汰逻辑负责）。

    Args:
        corrections: AsrCorrection 列表
        current_terms: 当前 AsrTerm 列表

    Returns:
        合并后的 AsrTerm 列表
    """
    # 构建现有索引
    existing_pairs: set = set()
    for t in current_terms:
        for mis in t.mis_asr:
            existing_pairs.add((mis, t.term))

    # 从 feedback 提取新映射
    new_mappings = extract_mappings_from_corrections(corrections)

    # 合并：找到对应 AsrTerm 添加新 mis_asr
    term_index: Dict[str, AsrTerm] = {}
    for t in current_terms:
        key = t.term.lower().replace(" ", "")
        term_index[key] = t

    for correct_word, mis_list in new_mappings.items():
        key = correct_word.lower().replace(" ", "")
        if key in term_index:
            for mis in mis_list:
                if (mis, correct_word) not in existing_pairs:
                    term_index[key].mis_asr.append(mis)
        else:
            # 新术语，添加为 domain_term
            current_terms.append(
                AsrTerm(
                    term=correct_word,
                    category="domain_term",
                    context="来自 feedback",
                    mis_asr=mis_list,
                )
            )

    return current_terms

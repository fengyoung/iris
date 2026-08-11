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
from pathlib import Path
from typing import Dict, List, Optional, Set

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
                # 防御历史脏数据：corrections_applied 非 list（如 str）时置空，
                # 否则下游迭代会按字符拆解污染分析结果
                corrections_applied = data.get("corrections_applied", [])
                if not isinstance(corrections_applied, list):
                    corrections_applied = []
                records.append(
                    AsrCorrection(
                        timestamp=data.get("timestamp", ""),
                        raw_text=data.get("raw_text", ""),
                        fast_corrected=data.get("fast_corrected", ""),
                        full_corrected=data.get("full_corrected", ""),
                        mode=data.get("mode", "full"),
                        corrections_applied=corrections_applied,
                        llm_time_ms=data.get("llm_time_ms", 0),
                    )
                )
            except (json.JSONDecodeError, KeyError):
                continue

    return records


# [LLM] 标记前缀，用于区分词典命中 vs LLM 发现的修正
_LLM_PREFIX = "[LLM] "
# [手动] 标记前缀：asr-report 手动确认写入的修正（解析时同样剥离）
_MANUAL_PREFIX = "[手动] "


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
            # 剥离 [LLM] / [手动] 前缀（LLM 发现的修正 / 手动确认的修正）
            if applied.startswith(_LLM_PREFIX):
                applied = applied[len(_LLM_PREFIX):]
            elif applied.startswith(_MANUAL_PREFIX):
                applied = applied[len(_MANUAL_PREFIX):]
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


def find_zombie_rules(
    corrections: List[AsrCorrection],
    terms: List[AsrTerm],
    min_samples: int = 50,
    history_rules: Optional[Set[str]] = None,
) -> List[tuple]:
    """找出反馈数据中从未命中的替换词典规则。

    判断依据：在一段时间的反馈数据中，该规则没有出现过任何命中记录。
    足够样本量（≥ min_samples）下仍未命中，说明规则大概率无效。

    时间窗：仅「历史规则」（上次部署词典中已存在的规则键 "误→正"）
    参与判定——本次 LLM 新生成的规则在反馈历史中必然无命中记录，
    若全部参与会被误判为僵尸导致「生成→淘汰→再生成」振荡。
    history_rules 为 None/空时返回 []（不淘汰）。

    Args:
        corrections: AsrCorrection 列表
        terms: 当前 AsrTerm 列表（含 mis_asr 映射）
        min_samples: 最少需要多少条反馈记录才触发分析
        history_rules: 上次部署替换词典的规则键集合（"误→正"），仅其中规则参与判定

    Returns:
        [(mis, correct, category), ...] 僵尸规则列表
    """
    if len(corrections) < min_samples:
        return []
    if not history_rules:
        return []

    # 收集所有被触发过的（非 LLM）规则
    triggered: set = set()
    for c in corrections:
        for applied in c.corrections_applied:
            if not applied.startswith(_LLM_PREFIX):
                triggered.add(applied)

    zombies: List[tuple] = []
    for t in terms:
        for mis in t.mis_asr:
            rule_key = f"{mis}→{t.term}"
            # 仅历史规则参与判定：本次新生成的规则不在 history_rules 中
            if rule_key in history_rules and rule_key not in triggered:
                zombies.append((mis, t.term, t.category))

    return zombies


def build_feedback_recommendations(
    corrections: List[AsrCorrection],
    terms: List[AsrTerm],
    hotwords: List[str],
    min_samples: int = 50,
    promote_threshold: int = 3,
    history_rules: Optional[Set[str]] = None,
) -> Dict[str, object]:
    """分析 feedback.jsonl 并返回优化建议。

    这是 Phase 1 反馈反向优化的核心分析函数。三个维度：
    1. 淘汰僵尸规则 — 从未命中的替换映射（仅历史规则参与，防振荡）
    2. 提升 LLM 发现 — 高频 LLM 修正提升为词典规则
    3. 补充热词 — 反馈中被 LLM 纠正的专有名词

    Args:
        corrections: AsrCorrection 列表
        terms: 当前 AsrTerm 列表
        hotwords: 当前热词列表
        min_samples: 最少需要多少条反馈记录
        promote_threshold: LLM 发现需达到多少次才提升为词典规则
        history_rules: 上次部署替换词典的规则键集合（透传给 find_zombie_rules）

    Returns:
        {
            "total_corrections": int,
            "dict_hit_count": int,
            "dict_hit_rate": float,
            "total_rules": int,
            "zombie_rules": [(mis, correct, category), ...],
            "zombie_count": int,
            "promote_to_dict": {correct: [mis1, mis2, ...], ...},
            "promote_count": int,
            "new_hotwords": ["词1", "词2", ...],
            "new_hotword_count": int,
        }
    """
    total_rules = sum(len(t.mis_asr) for t in terms)

    result: Dict[str, object] = {
        "total_corrections": len(corrections),
        "dict_hit_count": 0,
        "dict_hit_rate": 0.0,
        "total_rules": total_rules,
        "zombie_rules": [],
        "zombie_count": 0,
        "promote_to_dict": {},
        "promote_count": 0,
        "new_hotwords": [],
        "new_hotword_count": 0,
    }

    if len(corrections) < min_samples:
        return result

    # ── 1. 命中频率统计 ──────────────────────────────────
    hit_freq = compute_hit_frequency(corrections)
    dict_hits = {k: v for k, v in hit_freq.items()
                 if not k.startswith(_LLM_PREFIX)}
    result["dict_hit_count"] = sum(dict_hits.values())
    result["dict_hit_rate"] = (
        result["dict_hit_count"] / max(len(corrections), 1)
    )

    # ── 2. 僵尸规则检测 ──────────────────────────────────
    zombies = find_zombie_rules(corrections, terms, min_samples=min_samples,
                                history_rules=history_rules)
    result["zombie_rules"] = zombies
    result["zombie_count"] = len(zombies)

    # ── 3. LLM 发现 → 词典规则提升 ───────────────────────
    # 直接统计原始频率（不用 extract_llm_discoveries 因为其内部去重）
    discovery_freq: Dict[str, int] = {}
    for c in corrections:
        seen_in_record: Set[str] = set()
        for applied in c.corrections_applied:
            if not applied.startswith(_LLM_PREFIX):
                continue
            if applied in seen_in_record:
                continue  # 同一条记录内去重
            seen_in_record.add(applied)
            discovery_freq[applied] = discovery_freq.get(applied, 0) + 1

    promotions: Dict[str, List[str]] = {}
    for key, freq in discovery_freq.items():
        if freq >= promote_threshold:
            # 剥离 [LLM] 前缀后解析 "误→正"
            applied_clean = key[len(_LLM_PREFIX):]
            parts = applied_clean.split("→", 1)
            if len(parts) == 2:
                mis, correct = parts[0].strip(), parts[1].strip()
                if not mis or not correct:
                    continue
                if correct not in promotions:
                    promotions[correct] = []
                if mis not in promotions[correct]:
                    promotions[correct].append(mis)

    result["promote_to_dict"] = promotions
    result["promote_count"] = sum(len(v) for v in promotions.values())

    # ── 4. 高频被误识词 → 热词补充 ──────────────────────
    existing_hotword_keys: Set[str] = {
        hw.lower().replace(" ", "") for hw in hotwords
    }
    for t in terms:
        existing_hotword_keys.add(t.term.lower().replace(" ", ""))

    word_freq: Dict[str, int] = {}
    for c in corrections:
        if c.full_corrected == c.fast_corrected:
            continue
        for applied in c.corrections_applied:
            if applied.startswith(_LLM_PREFIX):
                applied_clean = applied[len(_LLM_PREFIX):]
                parts = applied_clean.split("→", 1)
                if len(parts) == 2:
                    correct_word = parts[1].strip()
                    if correct_word and len(correct_word) >= 2:
                        key = correct_word.lower().replace(" ", "")
                        if key not in existing_hotword_keys:
                            word_freq[correct_word] = (
                                word_freq.get(correct_word, 0) + 1
                            )

    result["new_hotwords"] = [
        w for w, f in sorted(word_freq.items(), key=lambda x: -x[1])
        if f >= 2
    ]
    result["new_hotword_count"] = len(result["new_hotwords"])

    return result


def apply_feedback_optimizations(
    terms: List[AsrTerm],
    hotwords: List[str],
    recommendations: Dict[str, object],
) -> tuple:
    """将反馈优化建议应用到 terms 和 hotwords（原地修改）。

    Args:
        terms: AsrTerm 列表（原地修改 — 移除僵尸规则、添加提升映射）
        hotwords: 热词列表（原地修改 — 追加新热词）
        recommendations: build_feedback_recommendations() 的返回值

    Returns:
        (zombie_removed, promoted, hotwords_added)
    """
    # ── 淘汰僵尸规则 ─────────────────────────────────────
    zombie_set: Set[str] = set()
    zombie_list = recommendations.get("zombie_rules", [])
    if isinstance(zombie_list, list):
        for item in zombie_list:
            if isinstance(item, tuple) and len(item) >= 2:
                zombie_set.add(f"{item[0]}→{item[1]}")

    zombie_removed = 0
    for t in terms:
        new_mis = []
        for mis in t.mis_asr:
            if f"{mis}→{t.term}" in zombie_set:
                zombie_removed += 1
            else:
                new_mis.append(mis)
        t.mis_asr = new_mis

    # ── 提升 LLM 发现为词典规则 ──────────────────────────
    promoted = 0
    promote_dict = recommendations.get("promote_to_dict", {})
    if isinstance(promote_dict, dict):
        for correct, mis_list in promote_dict.items():
            if not isinstance(mis_list, list):
                continue
            # 查找已有 term
            found = False
            for t in terms:
                if t.term == correct:
                    for mis in mis_list:
                        mis_str = str(mis)
                        if mis_str not in t.mis_asr:
                            t.mis_asr.append(mis_str)
                            promoted += 1
                    found = True
                    break
            if not found:
                terms.append(AsrTerm(
                    term=str(correct),
                    category="domain_term",
                    context="来自 feedback.jsonl 反馈提升",
                    mis_asr=[str(m) for m in mis_list],
                ))
                promoted += len(mis_list)

    # ── 补充新热词 ───────────────────────────────────────
    existing_keys: Set[str] = {
        hw.lower().replace(" ", "") for hw in hotwords
    }
    for t in terms:
        existing_keys.add(t.term.lower().replace(" ", ""))

    hotwords_added = 0
    new_list = recommendations.get("new_hotwords", [])
    if isinstance(new_list, list):
        for hw in new_list:
            hw_str = str(hw)
            key = hw_str.lower().replace(" ", "")
            if key not in existing_keys:
                hotwords.append(hw_str)
                existing_keys.add(key)
                hotwords_added += 1

    return zombie_removed, promoted, hotwords_added


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

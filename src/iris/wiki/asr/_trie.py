"""Aho-Corasick 多模式替换自动机（纯 Python，轻量，无外部依赖）。

供 ASR 校正引擎的 Step 1「替换词典」使用：一次扫描完成全部替换，<1ms。
"""

from __future__ import annotations

from collections import deque
from typing import Dict, List, Optional, Tuple


class _TrieNode:
    __slots__ = ("children", "fail", "output")

    def __init__(self):
        self.children: Dict[str, "_TrieNode"] = {}
        self.fail: Optional["_TrieNode"] = None
        self.output: List[Tuple[int, str]] = []  # [(pattern_len, replacement), ...]


class _AhoCorasick:
    """Aho-Corasick 多模式自动机，一次扫描完成全部替换。

    最长匹配优先 — 同一位置匹配多个模式时取最长者。
    """

    def __init__(self, replace_map: Dict[str, str]):
        self._root = _TrieNode()
        self._replace_map = replace_map  # 保留原始映射，供 list_patterns() 查询

        # 按模式长度降序插入（确保最长匹配优先）
        sorted_patterns = sorted(replace_map.keys(), key=len, reverse=True)
        for pattern in sorted_patterns:
            self._add_pattern(pattern, replace_map[pattern])

        self._build_failure_links()

    def _add_pattern(self, pattern: str, replacement: str) -> None:
        """向 Trie 插入一个模式。"""
        node = self._root
        for ch in pattern:
            if ch not in node.children:
                node.children[ch] = _TrieNode()
            node = node.children[ch]
        node.output.append((len(pattern), replacement))

    def _build_failure_links(self) -> None:
        """BFS 构建失败链接。"""
        queue = deque()
        for ch, child in self._root.children.items():
            child.fail = self._root
            queue.append(child)

        while queue:
            current = queue.popleft()
            for ch, child in current.children.items():
                queue.append(child)
                fail = current.fail
                while fail is not None and ch not in fail.children:
                    fail = fail.fail
                child.fail = fail.children[ch] if fail else self._root
                # 合并输出
                if child.fail:
                    child.output.extend(child.fail.output)
                    # 排序：最长匹配优先
                    child.output.sort(key=lambda x: -x[0])

    def list_patterns(self) -> Dict[str, str]:
        """返回全部已加载的替换规则 {误识别词: 正确词}。

        供 Phase 1 反向优化使用：对比 feedback 命中记录，
        识别僵尸规则（从未命中）和高价值规则。
        """
        return dict(self._replace_map)

    def replace_all(self, text: str) -> Tuple[str, List[str]]:
        """执行全部替换。

        Returns:
            (corrected_text, applied_rules): 校正文本 + 命中的规则列表
        """
        result_chars: List[str] = []
        applied: List[str] = []
        write_pos = 0  # 写指针：result_chars 中有效内容的长度
        i = 0
        n = len(text)
        node = self._root

        while i < n:
            ch = text[i]
            # 跟踪失败链接
            while node is not None and ch not in node.children:
                node = node.fail
            if node is None:
                node = self._root
                result_chars.append(ch)
                write_pos += 1
                i += 1
                continue

            node = node.children[ch]

            # 检查当前节点是否有输出
            if node.output:
                # 取最长匹配（已按长度降序排好）
                pattern_len, replacement = node.output[0]
                # 回退到匹配起点（调整写指针，覆盖已写入的模式字符）
                backtrack = pattern_len - 1
                write_pos -= backtrack
                # 截断列表到写指针位置
                del result_chars[write_pos:]
                result_chars.append(replacement)
                write_pos += 1
                applied.append(f"{text[i - pattern_len + 1:i + 1]}→{replacement}")
                i += 1
                node = self._root  # 重置（避免重叠匹配冲突）
            else:
                result_chars.append(ch)
                write_pos += 1
                i += 1

        return "".join(result_chars), applied

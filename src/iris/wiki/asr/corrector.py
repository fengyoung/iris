"""Iris ASR 实时校正引擎 — vocotype 文本校正伴侣。

独立常驻进程，通过剪贴板与 vocotype 交互：
  vocotype (ASR) → 剪贴板 → Iris (词典 + LLM) → 剪贴板 → 光标

用法:
    iris3 asr-corrector [--mode fast|full] [--profile <name>]

架构:
    - 剪贴板监听：轮询 NSPasteboard changeCount
    - 文本来源判定：热键 + 内容特征双重检测
    - 两步校正：替换词典（Aho-Corasick，<1ms）→ LLM 异步精修
    - 反馈记录：每次校正写入 feedback.jsonl
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from ._types import AsrCorrection

# ═══════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════

_DEFAULT_VOCO_DIR = os.path.expanduser("~/Library/Application Support/VocoType")
VOCO_DIR = os.environ.get("IRIS_VOCOTYPE_DIR", _DEFAULT_VOCO_DIR)

# ASR 文本特征阈值
_MIN_ASR_LENGTH = 5
_MAX_ASR_LENGTH = 500
_MIN_CHINESE_RATIO = 0.3

# 监听窗口（热键按下后等待剪贴板变化的秒数）
_LISTEN_WINDOW_SEC = 2.0

# 剪贴板轮询间隔
_POLL_INTERVAL = 0.2

# LLM 超时
_LLM_TIMEOUT_MS = 4000


# ═══════════════════════════════════════════════════════════════════
# 剪贴板工具
# ═══════════════════════════════════════════════════════════════════

def _read_clipboard() -> str:
    """读取剪贴板文本内容。"""
    try:
        return subprocess.check_output(["pbpaste"], text=True)
    except Exception:
        return ""


def _write_clipboard(text: str) -> None:
    """写入文本到剪贴板。"""
    try:
        subprocess.run(["pbcopy"], input=text, text=True)
    except Exception:
        pass


def _paste() -> None:
    """模拟 Cmd+V 粘贴。"""
    try:
        subprocess.run([
            "osascript", "-e",
            'tell application "System Events" to keystroke "v" using command down',
        ], timeout=3)
    except Exception:
        pass


def _replace_text_in_place(corrected: str, raw_length: int) -> None:
    """用校正文本替换 vocotype 刚粘贴的原始文本。

    策略：
    1. 写入校正文本到剪贴板（覆盖 vocotype 写入的原文）
    2. 基线等待 vocotype 的 Cmd+V 贴入完成（最小 0.15s）
    3. 轮询剪贴板确认稳定（最长额外 1.0s）
    4. 按 raw_length 次 Delete 键删除原始文本，粘贴校正文本
    """
    _write_clipboard(corrected)

    # 基线等待：vocotype 的 Cmd+V 至少需要 0.15s 完成
    _BASELINE_WAIT = 0.15
    time.sleep(_BASELINE_WAIT)

    # 轮询确认剪贴板稳定（无其他写入方），最长再等 1.0s
    _POLL_MAX_EXTRA = 1.0
    _POLL_STABLE_CYCLES = 3
    stable_count = 0
    last_clip = _read_clipboard()
    t_deadline = time.monotonic() + _POLL_MAX_EXTRA

    while time.monotonic() < t_deadline:
        time.sleep(0.05)
        current = _read_clipboard()
        if current == last_clip:
            stable_count += 1
            if stable_count >= _POLL_STABLE_CYCLES:
                break
        else:
            stable_count = 0
            last_clip = current

    try:
        subprocess.run([
            "osascript", "-e",
            f'''
            tell application "System Events"
                repeat {raw_length} times
                    key code 51  -- Delete / Backspace
                end repeat
                delay 0.05
                keystroke "v" using command down
            end tell
            ''',
        ], timeout=5)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
# macOS 键盘修饰键检测（通过 CoreGraphics + Carbon，零外部依赖）
# ═══════════════════════════════════════════════════════════════════

def _load_cg() -> Optional[ctypes.CDLL]:
    """加载 CoreGraphics 框架。"""
    path = ctypes.util.find_library("CoreGraphics")
    if not path:
        # macOS 上 CoreGraphics 通常在固定路径
        path = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    try:
        return ctypes.CDLL(path)
    except OSError:
        return None


_CG = _load_cg()

# 键码常量（macOS Carbon key codes）
_KEYCODE_MAP = {
    "shift": 56,     # kVK_Shift
    "rightShift": 60,
    "control": 59,   # kVK_Control
    "rightControl": 62,
    "option": 58,    # kVK_Option
    "rightOption": 61,
    "command": 55,   # kVK_Command
    "rightCommand": 54,
    "capsLock": 57,
    "KeyZ": 6,
    "KeyX": 7,
    "KeyC": 8,
    "KeyV": 9,
}

# 修饰键掩码
_MOD_MASKS = {
    "shift": 512,     # shiftKey
    "control": 4096,  # controlKey
    "option": 2048,   # optionKey
    "command": 256,   # cmdKey
}


def _check_modifiers() -> int:
    """返回当前按下的修饰键掩码。"""
    if _CG is None:
        return 0
    try:
        _CG.CGEventSourceKeyState.restype = ctypes.c_bool
        _CG.CGEventSourceKeyState.argtypes = [
            ctypes.c_int, ctypes.c_uint16,
        ]
        mask = 0
        for mod_name, mod_mask in _MOD_MASKS.items():
            keycode = _KEYCODE_MAP.get(mod_name, 0)
            if keycode and _CG.CGEventSourceKeyState(1, ctypes.c_uint16(keycode)):
                mask |= mod_mask
        return mask
    except Exception:
        return 0


def _parse_hotkey(hotkey_str: str) -> Tuple[int, int]:
    """解析 vocotype 热键字符串 → (modifiers_mask, key_code)。

    格式例如: "shift+control+KeyZ", "alt+ArrowRight"

    非修饰键（如 ArrowRight）无法通过轮询可靠检测，
    此类热键将仅依赖内容特征判定，修饰键检测作为辅助。
    """
    if not hotkey_str:
        return 0, 0

    parts = [p.strip() for p in hotkey_str.lower().split("+")]
    mask = 0
    keycode = 0

    for part in parts:
        # 修饰键别名（含 macOS 左右键变体）
        if part in ("shift", "leftshift", "rightshift"):
            mask |= _MOD_MASKS.get("shift", 0)
        elif part in ("control", "ctrl", "leftcontrol", "rightcontrol"):
            mask |= _MOD_MASKS.get("control", 0)
        elif part in ("option", "alt", "leftoption", "leftalt", "rightoption", "rightalt", "altright", "altleft"):
            mask |= _MOD_MASKS.get("option", 0)
        elif part in ("command", "cmd", "leftcommand", "rightcommand"):
            mask |= _MOD_MASKS.get("command", 0)
        else:
            # 普通键 — 规范化后查找
            key_name = part.replace("key", "").capitalize()
            mapped = f"Key{key_name}"
            keycode = _KEYCODE_MAP.get(
                mapped,
                _KEYCODE_MAP.get(part.capitalize(), 0),
            )

    return mask, keycode


# ═══════════════════════════════════════════════════════════════════
# Aho-Corasick 纯 Python 实现（轻量，无外部依赖）
# ═══════════════════════════════════════════════════════════════════

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
                i += 1
                continue

            node = node.children[ch]

            # 检查当前节点是否有输出
            if node.output:
                # 取最长匹配（已按长度降序排好）
                pattern_len, replacement = node.output[0]
                # 回退到匹配起点
                result_chars = result_chars[:len(result_chars) - (pattern_len - 1)]
                result_chars.append(replacement)
                applied.append(f"{text[i - pattern_len + 1:i + 1]}→{replacement}")
                i += 1
                node = self._root  # 重置（避免重叠匹配冲突）
            else:
                result_chars.append(ch)
                i += 1

        return "".join(result_chars), applied


# ═══════════════════════════════════════════════════════════════════
# ASR 文本特征检测
# ═══════════════════════════════════════════════════════════════════

# 非 ASR 文本特征（代码、URL、Markdown 等）
_CODE_PATTERNS = re.compile(
    r"[{};]|def\s|import\s|class\s|function\s|http[s]?://|"
    r"```|^#{1,6}\s|^\*\s|^\d+\.\s|^\-\s"
)


def _count_chinese(text: str) -> int:
    """统计中文字符数。"""
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _is_asr_text(text: str) -> bool:
    """判断文本是否像是 vocotype ASR 输出。

    检测维度：
    - 中文为主（>30%）
    - 长度在 5-500 之间
    - 无代码/URL/Markdown 特征
    """
    if not text:
        return False

    length = len(text)
    if length < _MIN_ASR_LENGTH or length > _MAX_ASR_LENGTH:
        return False

    chinese_count = _count_chinese(text)
    if chinese_count / max(length, 1) < _MIN_CHINESE_RATIO:
        return False

    if _CODE_PATTERNS.search(text):
        return False

    return True


# ═══════════════════════════════════════════════════════════════════
# vocotype 配置读取
# ═══════════════════════════════════════════════════════════════════

def _load_vocotype_hotkey() -> Tuple[int, int]:
    """从 vocotype 配置文件读取录音热键。

    Returns:
        (modifiers_mask, key_code) 或 (0, 0)
    """
    config_path = Path(VOCO_DIR) / "ui_settings.json"
    if not config_path.exists():
        return 0, 0

    try:
        with open(config_path) as f:
            settings = json.load(f)
        hotkey_str = settings.get("recording_hotkey", "")
        return _parse_hotkey(hotkey_str)
    except Exception:
        return 0, 0


# ═══════════════════════════════════════════════════════════════════
# 校正引擎
# ═══════════════════════════════════════════════════════════════════

class AsrCorrector:
    """实时 ASR 校正引擎。

    职责：
    - 剪贴板监听 + vocotype 热键检测
    - Step 1：替换词典（Aho-Corasick，<1ms）
    - Step 2：LLM 异步精修
    - 反馈日志写入
    """

    def __init__(
        self,
        replace_dict: Dict[str, str],
        llm_prompt: str = "",
        mode: str = "full",
        feedback_path: str = "",
        on_corrected: Optional[Callable[[AsrCorrection], None]] = None,
    ):
        """
        Args:
            replace_dict: {"误识别": "正确词"} 映射
            llm_prompt: LLM 校正 Prompt（~800 字）
            mode: "fast"（仅词典）| "full"（词典 + LLM）
            feedback_path: JSONL 反馈文件路径
            on_corrected: 每次校正完成时的回调（用于测试/日志）
        """
        self._automaton = _AhoCorasick(replace_dict)
        self._prompt = llm_prompt
        self._mode = mode
        self._feedback_path = feedback_path
        self._on_corrected = on_corrected

        # 热键状态
        self._hotkey_mask, self._hotkey_keycode = _load_vocotype_hotkey()
        self._last_modifiers = 0
        self._listen_window_start: float = 0.0

        # 剪贴板状态
        self._last_text = ""
        self._last_corrected = ""  # 防止自己写回的文本被重复处理

        # Prompt 热加载
        self._prompt_path = ""  # 由 CLI handler 设置
        self._prompt_mtime: float = 0.0
        self._reload_interval = 5  # 每 N 秒检查一次 Prompt 文件
        self._last_reload_check = 0.0

        # 替换词典热加载
        self._dict_path = ""  # 由 CLI handler 设置
        self._dict_mtime: float = 0.0

        # LLM provider（延迟初始化）
        self._provider = None

    def set_provider(self, provider) -> None:
        """设置 LLM Provider（由 CLI 层注入）。"""
        self._provider = provider

    def set_prompt_path(self, path: str) -> None:
        """设置 Prompt 文件路径，启用热加载。"""
        self._prompt_path = path
        self._prompt_mtime = os.path.getmtime(path) if os.path.exists(path) else 0.0

    def set_dict_path(self, path: str) -> None:
        """设置替换词典文件路径，启用热加载。"""
        self._dict_path = path
        self._dict_mtime = os.path.getmtime(path) if os.path.exists(path) else 0.0

    def _check_prompt_reload(self) -> None:
        """检查 Prompt 文件是否更新，自动热加载。"""
        if not self._prompt_path:
            return
        now = time.monotonic()
        if now - self._last_reload_check < self._reload_interval:
            return
        self._last_reload_check = now
        try:
            mtime = os.path.getmtime(self._prompt_path)
            if mtime != self._prompt_mtime:
                with open(self._prompt_path, encoding="utf-8") as f:
                    self._prompt = f.read()
                self._prompt_mtime = mtime
                print(f"[Iris] 🔄 Prompt 已热加载 ({len(self._prompt)} 字)",
                      file=sys.stderr)
        except Exception:
            pass

    def _check_dict_reload(self) -> None:
        """检查替换词典文件是否更新，自动热加载重建 Aho-Corasick 自动机。"""
        if not self._dict_path:
            return
        now = time.monotonic()
        if now - self._last_reload_check < self._reload_interval:
            return
        self._last_reload_check = now
        try:
            mtime = os.path.getmtime(self._dict_path)
            if mtime != self._dict_mtime:
                with open(self._dict_path, encoding="utf-8") as f:
                    data = json.load(f)
                replace_map = data.get("replace_map", {})
                self._automaton = _AhoCorasick(replace_map)
                self._dict_mtime = mtime
                print(f"[Iris] 🔄 替换词典已热加载 ({len(replace_map)} 条规则)",
                      file=sys.stderr)
        except Exception:
            pass

    @property
    def mode(self) -> str:
        return self._mode

    def correct_fast(self, text: str) -> Tuple[str, List[str]]:
        """Step 1：替换词典匹配，毫秒级。

        Returns:
            (corrected_text, applied_rules)
        """
        return self._automaton.replace_all(text)

    def _correct_llm(self, text: str, dict_applied: List[str]) -> Tuple[str, List[str], int]:
        """Step 2：LLM 校正。

        Returns:
            (corrected_text, llm_specific_applied_rules, time_ms)
        """
        if self._provider is None or not self._prompt:
            return text, [], 0

        t_start = time.monotonic()
        try:
            from iris.llm import LLMRequest

            response = self._provider.generate(
                LLMRequest(
                    prompt=self._prompt + "\n\n输入：" + text,
                    route_context={
                        "task_type": "asr_correction",
                        "input_type": "text",
                    },
                    extra_body={"thinking": {"type": "disabled"}},
                ),
                temperature=0.1,
                max_tokens=2048,
            )
            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            if response and response.text and len(response.text.strip()) >= 1:
                llm_output = response.text.strip()
                if len(llm_output) > len(text) * 3:
                    print(f"[Iris] ⚠ LLM 输出疑似推理过程（{len(llm_output)}字），降级为词典结果",
                          file=sys.stderr)
                    return text, [], elapsed_ms
                return llm_output, [], elapsed_ms
        except Exception as e:
            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            print(f"[Iris] ⚠ LLM 校正失败 ({elapsed_ms}ms): {e}", file=sys.stderr)

        return text, [], 0

    def correct_full(self, text: str) -> Tuple[str, List[str]]:
        """Step 1 → Step 2：替换词典 + LLM 校正。

        用于一次性校正场景（correct_text_static）。
        """
        fast_result, applied = self.correct_fast(text)
        full_result, _, _ = self._correct_llm(fast_result, applied)
        return full_result, applied

    def _record(self, raw: str, fast: str, full: str, applied: List[str],
                llm_time_ms: int = 0) -> None:
        """写入反馈日志，包含 LLM 与词典的差异追踪和耗时。"""
        llm_changes = _diff_changes(fast, full) if full != fast else []
        all_corrections = applied + [f"[LLM] {c}" for c in llm_changes]

        record = AsrCorrection(
            timestamp=datetime.now(timezone.utc).isoformat(),
            raw_text=raw.strip(),
            fast_corrected=fast.strip(),
            full_corrected=full.strip(),
            mode=self._mode,
            corrections_applied=all_corrections,
            llm_time_ms=llm_time_ms,
        )

        if self._feedback_path:
            _append_feedback_jsonl(record, self._feedback_path)

        if self._on_corrected:
            self._on_corrected(record)

    def run_forever(self) -> None:
        """主循环：剪贴板监听 + 校正。"""
        print(f"[Iris] ASR 校正引擎已启动 (mode={self._mode})", file=sys.stderr)
        if self._hotkey_mask or self._hotkey_keycode:
            print(f"[Iris] vocotype 热键已检测: mask={self._hotkey_mask} key={self._hotkey_keycode}",
                  file=sys.stderr)
        else:
            print("[Iris] 未检测到 vocotype 热键配置，将仅通过文本特征判定", file=sys.stderr)
        patterns = self._automaton.list_patterns()
        print(f"[Iris] 替换词典已加载 ({len(patterns)} 条规则)",
              file=sys.stderr)
        print("[Iris] 监听剪贴板... (Ctrl+C 退出)", file=sys.stderr)

        try:
            while True:
                self._tick()
                time.sleep(_POLL_INTERVAL)
        except KeyboardInterrupt:
            print("\n[Iris] 校正引擎已停止", file=sys.stderr)

    def _tick(self) -> None:
        """单次轮询周期。"""
        # 0. 热加载检查（Prompt + 替换词典）
        self._check_prompt_reload()
        self._check_dict_reload()

        # 1. 检查热键状态
        current_mods = _check_modifiers()
        hotkey_just_pressed = (
            self._hotkey_mask > 0
            and (current_mods & self._hotkey_mask) == self._hotkey_mask
            and (self._last_modifiers & self._hotkey_mask) != self._hotkey_mask
        )

        if hotkey_just_pressed:
            self._listen_window_start = time.monotonic()

        self._last_modifiers = current_mods

        # 2. 监听窗口超时检查
        in_listen_window = (
            self._listen_window_start > 0
            and (time.monotonic() - self._listen_window_start) < _LISTEN_WINDOW_SEC
        )

        # 3. 读取剪贴板
        current_text = _read_clipboard()
        if not current_text:
            return
        if current_text == self._last_text:
            return
        if current_text == self._last_corrected:
            return

        self._last_text = current_text

        # 4. 判定是否为 vocotype ASR 输出
        if not _is_asr_text(current_text):
            # 不在监听窗口内 → 忽略
            if not in_listen_window:
                return
            # 在监听窗口内但不满足 ASR 特征 → 忽略
            return

        # 5. 执行校正
        self._last_text = current_text
        t_correct_start = time.monotonic()

        # Step 1: 替换词典（始终执行）
        fast_result, dict_applied = self.correct_fast(current_text)

        # Step 2: LLM 校正（仅 full 模式）
        llm_time_ms = 0
        if self._mode == "full":
            full_result, _, llm_time_ms = self._correct_llm(fast_result, dict_applied)
        else:
            full_result = fast_result

        total_ms = int((time.monotonic() - t_correct_start) * 1000)

        # 6. 输出：删掉原始文本 → 粘贴校正版
        if full_result != current_text:
            self._last_corrected = full_result
            _replace_text_in_place(full_result, len(current_text))

            llm_diff = _diff_changes(fast_result, full_result)
            if dict_applied:
                parts = [f"词典({total_ms}ms): {', '.join(dict_applied)}"]
                if llm_diff:
                    parts.append(f"LLM: {', '.join(llm_diff)}")
                print(f"[Iris] ✅ {' | '.join(parts)}", file=sys.stderr)
            elif llm_diff:
                print(f"[Iris] 🤖 LLM ({total_ms}ms): {', '.join(llm_diff)}", file=sys.stderr)
            elif full_result != fast_result:
                print(f"[Iris] ✏️ 润色 ({total_ms}ms)", file=sys.stderr)

        # 7. 记录反馈
        self._record(current_text, fast_result, full_result, dict_applied, llm_time_ms)

        # 8. 重置监听窗口（一次热键仅触发一次校正）
        self._listen_window_start = 0.0


# ═══════════════════════════════════════════════════════════════════
# 反馈工具
# ═══════════════════════════════════════════════════════════════════

def _diff_changes(before: str, after: str) -> List[str]:
    """对比校正前后的文本差异，词级比较。

    使用正则分词（中文单字 + 英文单词 + 标点），
    避免字符级 diff 拆分太碎（如 数→速 而非 数率→速率）。
    """
    import difflib, re

    def _tokenize(text: str) -> List[str]:
        tokens: List[str] = []
        i = 0
        while i < len(text):
            ch = text[i]
            if "一" <= ch <= "鿿":
                # 中文字符，每个单独作为 token
                tokens.append(ch)
                i += 1
            elif ch.isalpha():
                # 英文单词，连续字母作为一个 token
                j = i
                while j < len(text) and text[j].isalpha():
                    j += 1
                tokens.append(text[i:j])
                i = j
            elif ch.isspace():
                j = i
                while j < len(text) and text[j].isspace():
                    j += 1
                tokens.append(text[i:j])
                i = j
            else:
                # 标点/数字，单独处理
                tokens.append(ch)
                i += 1
        return tokens

    tokens_before = _tokenize(before)
    tokens_after = _tokenize(after)

    changes: List[str] = []
    matcher = difflib.SequenceMatcher(None, tokens_before, tokens_after)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old_str = "".join(tokens_before[i1:i2]).strip()
        new_str = "".join(tokens_after[j1:j2]).strip()
        if tag == "replace":
            if old_str and new_str:
                changes.append(f"{old_str}→{new_str}")
            elif new_str:
                changes.append(f"⊕{new_str}")
        elif tag == "insert":
            if new_str:
                changes.append(f"⊕{new_str}")

    return changes[:8]  # 最多展示 8 处修改


def _append_feedback_jsonl(record: AsrCorrection, path: str) -> None:
    """追加一条校正记录到 JSONL。"""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "timestamp": record.timestamp,
                "raw_text": record.raw_text,
                "fast_corrected": record.fast_corrected,
                "full_corrected": record.full_corrected,
                "mode": record.mode,
                "corrections_applied": record.corrections_applied,
                "llm_time_ms": record.llm_time_ms,
            },
            ensure_ascii=False,
        )
        with open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def correct_text_static(
    text: str,
    replace_dict: Dict[str, str],
    llm_prompt: str = "",
    provider=None,
) -> Tuple[str, List[str]]:
    """静态校正函数 — 用于非守护进程场景（测试、一次性校正）。

    不做剪贴板交互，纯文本 → 文本。

    Args:
        text: 待校正文本
        replace_dict: 替换词典
        llm_prompt: LLM Prompt（可选）
        provider: Iris LLM Provider（可选）

    Returns:
        (corrected_text, applied_rules)
    """
    automaton = _AhoCorasick(replace_dict)
    result, applied = automaton.replace_all(text)

    if provider and llm_prompt:
        try:
            from iris.llm import LLMRequest

            response = provider.generate(
                LLMRequest(
                    prompt=llm_prompt + "\n\n输入：" + result,
                    route_context={
                        "task_type": "asr_correction",
                        "input_type": "text",
                    },
                    extra_body={"thinking": {"type": "disabled"}},
                ),
                temperature=0.1,
                max_tokens=2048,
            )
            if response and response.text:
                return response.text.strip(), applied
        except Exception:
            pass

    return result, applied

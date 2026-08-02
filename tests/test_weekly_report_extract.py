#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""extract-weekly-reports 扫描/过滤逻辑单元测试。

覆盖 v3.11.10 修复：
- scan_triage：query 非空走 search 路径（--query + 不带 folder），为空走 folder 路径
- scan_mode 配置默认 search、非法值兜底 search
- _prefilter_summaries：白名单 + 关键词预筛，空白名单透传
- EmailFilter：排除「发件人已撤回邮件」通知、同一发件人保留最新一封
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

# ── 以文件路径加载 scripts/extract_weekly_reports.py（非包，无法直接 import）──
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "extract_weekly_reports.py"
_spec = importlib.util.spec_from_file_location("extract_weekly_reports", _SCRIPT)
ewr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ewr)


WHITELIST = [
    {"name": "张三", "email": "zhangsan@example.com"},
    {"name": "李四", "email": "lisi@example.com"},
]
KEYWORDS = ["周报", "周会", "week report", "weekly"]


def _make_email(email: str, subject: str, date: str) -> dict:
    return {
        "from": {"mail_address": email, "name": ""},
        "subject": subject,
        "date_formatted": date,
    }


class _StubConfig:
    """_prefilter_summaries 只用到这两个属性，用桩替代真实配置（避免读磁盘配置文件）。"""

    def __init__(self, whitelist, keywords):
        self.sender_whitelist = whitelist
        self.subject_keywords = keywords


# ── scan_triage：search vs folder 路径 ────────────────────


def _patch_run(scanner, calls):
    def fake_run(cmd_args, timeout=90):
        calls.append(cmd_args)
        return {"messages": [], "has_more": False, "page_token": ""}

    scanner._run_lark = fake_run  # type: ignore[assignment]


def test_scan_triage_search_path_adds_query_and_drops_folder():
    scanner = ewr.LarkMailScanner()
    calls: list = []
    _patch_run(scanner, calls)

    scanner.scan_triage(days_back=7, query="周报")

    assert calls, "应调用 _run_lark"
    args = calls[0]
    assert "--query" in args
    assert args[args.index("--query") + 1] == "周报"
    filt = json.loads(args[args.index("--filter") + 1])
    assert "folder" not in filt and "folder_id" not in filt, "search 路径不应带文件夹约束"
    assert "time_range" in filt


def test_scan_triage_folder_path_no_query():
    scanner = ewr.LarkMailScanner()
    calls: list = []
    _patch_run(scanner, calls)

    scanner.scan_triage(days_back=7, folder="INBOX")

    args = calls[0]
    assert "--query" not in args
    filt = json.loads(args[args.index("--filter") + 1])
    assert filt.get("folder") == "INBOX"
    assert "time_range" in filt


# ── scan_mode 配置 ────────────────────────────────────────


def _config_with_raw(raw: dict):
    cfg = ewr.ExtractWeeklyReportsConfig.__new__(ewr.ExtractWeeklyReportsConfig)
    cfg._cli = None
    cfg._raw = raw
    return cfg


@pytest.mark.parametrize(
    "raw,expected",
    [
        ({"scan": {"mode": "search"}}, "search"),
        ({"scan": {"mode": "folder"}}, "folder"),
        ({"scan": {}}, "search"),            # 缺省 → search
        ({}, "search"),                       # 无 scan 段 → search
        ({"scan": {"mode": "bogus"}}, "search"),  # 非法值 → search
    ],
)
def test_scan_mode_resolution(raw, expected):
    assert _config_with_raw(raw).scan_mode == expected


# ── _prefilter_summaries ──────────────────────────────────


def test_prefilter_keeps_whitelist_with_keyword():
    cfg = _StubConfig(WHITELIST, KEYWORDS)
    summaries = [
        {"from": "张三 <zhangsan@example.com>", "subject": "后端技术周报-2026/07/06"},
        {"from": "李四 <lisi@example.com>", "subject": "【周报】算法团队"},
        {"from": "路人 <stranger@example.com>", "subject": "周报 外部"},   # 非白名单
        {"from": "张三 <zhangsan@example.com>", "subject": "报销单据"},   # 无关键词
    ]
    out = ewr._prefilter_summaries(summaries, cfg)
    subjects = {s["subject"] for s in out}
    assert subjects == {"后端技术周报-2026/07/06", "【周报】算法团队"}


def test_prefilter_empty_whitelist_passthrough():
    cfg = _StubConfig([], KEYWORDS)
    summaries = [{"from": "x <a@b.com>", "subject": "hi"}]
    assert ewr._prefilter_summaries(summaries, cfg) == summaries


# ── EmailFilter：撤回通知 + 同人保留最新 ──────────────────


def test_is_recall_notice():
    f = ewr.EmailFilter(WHITELIST, KEYWORDS)
    assert f.is_recall_notice("发件人已撤回邮件：后端技术周报-2026/07/06")
    assert not f.is_recall_notice("后端技术周报-2026/07/06")
    assert not f.is_recall_notice("")


def test_filter_excludes_recall_and_keeps_report():
    f = ewr.EmailFilter(WHITELIST, KEYWORDS)
    emails = [
        _make_email("zhangsan@example.com", "发件人已撤回邮件：后端技术周报-2026/07/06", "2026-07-06 14:24"),
        _make_email("zhangsan@example.com", "后端技术周报-2026/07/06", "2026-07-06 14:49"),
    ]
    out = f.filter_emails(emails)
    assert len(out) == 1
    assert out[0]["subject"] == "后端技术周报-2026/07/06"


def test_filter_keeps_latest_per_sender():
    f = ewr.EmailFilter(WHITELIST, KEYWORDS)
    emails = [
        _make_email("zhangsan@example.com", "后端技术周报-2026/07/06", "2026-07-06 14:33"),
        _make_email("zhangsan@example.com", "后端技术周报-2026/07/06", "2026-07-06 14:49"),
    ]
    out = f.filter_emails(emails)
    assert len(out) == 1
    assert out[0]["date_formatted"] == "2026-07-06 14:49"


def test_filter_multiple_senders_and_drops_nonmatching():
    f = ewr.EmailFilter(WHITELIST, KEYWORDS)
    emails = [
        _make_email("zhangsan@example.com", "后端技术周报", "2026-07-06 14:49"),
        _make_email("lisi@example.com", "【周报】算法团队李四", "2026-07-03 15:34"),
        _make_email("stranger@example.com", "周报 外部", "2026-07-05 00:00"),   # 非白名单
        _make_email("zhangsan@example.com", "普通邮件无关键词", "2026-07-07 00:00"),  # 无关键词
    ]
    out = f.filter_emails(emails)
    assert sorted(e["sender_name"] for e in out) == ["张三", "李四"]

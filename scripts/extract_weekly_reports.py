#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
周报邮件提取脚本：从飞书邮箱扫描白名单成员的周报邮件，
经 AI 结构化处理后生成 Markdown 文件。

用法：
    python scripts/extract_weekly_reports.py run --pretty
    python scripts/extract_weekly_reports.py status
    python scripts/extract_weekly_reports.py sender-add --name "姓名" --email "name@example.com"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ── 项目根路径 ──────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# ── 配置路径 ────────────────────────────────────────────
CONFIG_PATH = PROJECT_ROOT / "config" / "weekly_report.json"
PROCESSED_STATE_PATH = PROJECT_ROOT / "data" / "weekly_report_processed.json"
PROMPT_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "prompt" / "weekly_report_extract.md"


def _emit(payload: Any, pretty: bool = False) -> None:
    """统一输出：pretty 模式下格式化打印，否则输出 JSON。"""
    if pretty:
        if isinstance(payload, dict):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif isinstance(payload, list):
            for item in payload:
                print(item)
        else:
            print(payload)
    else:
        print(json.dumps(payload, ensure_ascii=False))


# ─────────────────────────────────────────────────────────
# 配置管理
# ─────────────────────────────────────────────────────────


class ExtractWeeklyReportsConfig:
    """周报提取配置，支持 CLI 参数覆盖配置文件。"""

    def __init__(self, cli_args: Optional[argparse.Namespace] = None):
        self._cli = cli_args
        self._raw = self._load_config()

    def _load_config(self) -> dict:
        if not CONFIG_PATH.exists():
            raise FileNotFoundError(f"配置文件不存在: {CONFIG_PATH}\n请先运行 init-config")
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    # ── scan ─────────────────────────────────────────

    @property
    def scan_folders(self) -> List[str]:
        folders = self._raw.get("scan", {}).get("folders", ["INBOX"])
        if isinstance(folders, str):
            folders = [folders]
        return folders

    @property
    def scan_mode(self) -> str:
        """扫描模式：'search'（跨文件夹关键词搜索，默认）或 'folder'（按文件夹 list，旧行为）。

        search 模式解决 IMPORTANT 标签周报散落在 inbox/priority list 首屏之外被漏扫的问题。
        """
        mode = self._raw.get("scan", {}).get("mode", "search")
        return mode if mode in ("search", "folder") else "search"

    # ── filters ───────────────────────────────────────

    @property
    def subject_keywords(self) -> List[str]:
        return self._raw.get("filters", {}).get("subject_keywords", ["周报", "周会"])

    @property
    def sender_whitelist(self) -> List[Dict]:
        return self._raw.get("filters", {}).get("sender_whitelist", [])

    @property
    def date_range_days(self) -> int:
        if self._cli and getattr(self._cli, "days", None):
            return self._cli.days
        return self._raw.get("filters", {}).get("date_range_days", 7)

    @property
    def date_from(self) -> Optional[str]:
        if self._cli and getattr(self._cli, "date_from", None):
            return self._cli.date_from
        return self._raw.get("filters", {}).get("date_from")

    # ── output ────────────────────────────────────────

    @property
    def output_dir(self) -> str:
        if self._cli and getattr(self._cli, "output", None):
            return self._cli.output
        return self._raw.get("output", {}).get("dir", "./output")

    @property
    def filename_format(self) -> str:
        return self._raw.get("output", {}).get("filename_format", "周报-w{week}-{name}-{date}.md")

    @property
    def file_overwrite(self) -> bool:
        return self._raw.get("output", {}).get("file_overwrite", False)


# ─────────────────────────────────────────────────────────
# 邮件过滤
# ─────────────────────────────────────────────────────────

# 飞书「发件人已撤回邮件：...」通知邮件的主题前缀，需排除（否则会生成空/垃圾报告）
RECALL_SUBJECT_PREFIX = "发件人已撤回邮件"


class EmailFilter:
    """根据白名单和主题关键词过滤周报邮件（移植自 _weekly-reports-from-email/filter.py）。"""

    def __init__(self, sender_whitelist: List[Dict], subject_keywords: List[str]):
        self.subject_keywords = [kw.lower() for kw in subject_keywords]

        # 构建邮箱→姓名映射
        self._email_to_name: Dict[str, str] = {}
        for sender in sender_whitelist:
            email = sender.get("email", "").lower()
            name = sender.get("name", "")
            if email:
                self._email_to_name[email] = name or email.split("@")[0]

        self._whitelist_emails = set(self._email_to_name.keys())

    def is_whitelisted_sender(self, from_addr: Dict) -> bool:
        """检查发件人是否在白名单中。

        lark-mail 返回的 from 结构：{"mail_address": "...", "name": "..."}
        兼容原项目结构：{"email": "...", "name": "..."}
        """
        email = (from_addr.get("mail_address") or from_addr.get("email") or "").lower()
        return email in self._whitelist_emails

    def has_weekly_report_keyword(self, subject: str) -> bool:
        if not subject:
            return False
        subject_lower = subject.lower()
        return any(kw in subject_lower for kw in self.subject_keywords)

    @staticmethod
    def is_recall_notice(subject: str) -> bool:
        """是否为「发件人已撤回邮件」通知。"""
        return bool(subject) and subject.lstrip().startswith(RECALL_SUBJECT_PREFIX)

    def get_sender_name(self, from_addr: Dict) -> str:
        """获取发件人的规范姓名。"""
        email = (from_addr.get("mail_address") or from_addr.get("email") or "").lower()
        name = from_addr.get("name", "")

        if email in self._email_to_name:
            return self._email_to_name[email]
        if name:
            return name
        if email:
            return email.split("@")[0]
        return "Unknown"

    @staticmethod
    def _sort_date(email_data: Dict) -> str:
        """取可比较的日期字符串用于「保留最新」（YYYY-MM-DD... 字典序即时间序）。"""
        return (
            email_data.get("date_formatted")
            or email_data.get("date")
            or ""
        )

    def filter_emails(self, emails: List[Dict]) -> List[Dict]:
        """过滤邮件列表，保留白名单发件人的周报邮件。

        - 排除「发件人已撤回邮件」通知
        - 同一发件人若发了多封（重复/更正），仅保留日期最新的一封
        """
        candidates = []
        for email_data in emails:
            from_addr = email_data.get("from", {})
            subject = email_data.get("subject", "")

            if not self.is_whitelisted_sender(from_addr):
                continue
            if self.is_recall_notice(subject):
                continue
            if not self.has_weekly_report_keyword(subject):
                continue

            email_data["sender_name"] = self.get_sender_name(from_addr)
            candidates.append(email_data)

        # 同一发件人保留最新一封（防止同一周期内重复提交覆盖）
        latest_by_sender: Dict[str, Dict] = {}
        for em in candidates:
            sender = em.get("sender_name", "")
            prev = latest_by_sender.get(sender)
            if prev is None or self._sort_date(em) >= self._sort_date(prev):
                latest_by_sender[sender] = em

        return list(latest_by_sender.values())


# ─────────────────────────────────────────────────────────
# 飞书邮箱扫描
# ─────────────────────────────────────────────────────────


class LarkMailScanner:
    """通过 lark-cli mail API 扫描飞书邮箱，替代原项目的 IMAP 客户端。"""

    LARK_CLI = "lark-cli"

    # 文件夹名→ID 缓存（首次使用时通过 API 拉取）
    _folder_name_to_id: Optional[Dict[str, str]] = None

    # ── 文件夹名解析 ──────────────────────────────────

    @classmethod
    def _resolve_folder(cls, folder: str) -> str:
        """将文件夹名解析为 ID（数字 ID 直接返回）。

        首次调用时从 API 拉取文件夹列表，缓存 name→id 映射。
        支持 "父文件夹/子文件夹" 格式的名称。
        """
        # 数字 ID 直接返回
        if folder.isdigit():
            return folder
        # 系统文件夹名直接返回
        if folder.upper() in ("INBOX", "SENT", "DRAFT", "TRASH", "SPAM", "ARCHIVED"):
            return folder

        # 懒加载文件夹映射
        if cls._folder_name_to_id is None:
            cls._folder_name_to_id = cls._build_folder_map()

        # 精确匹配
        if folder in cls._folder_name_to_id:
            return cls._folder_name_to_id[folder]

        # 后缀匹配："关键周报/团队TL" → 匹配到 "其他文件夹/关键周报/团队TL"
        suffix = f"/{folder}"
        for path, fid in cls._folder_name_to_id.items():
            if path.endswith(suffix) or path == folder:
                return fid

        # parent/child 逐段解析
        if "/" in folder:
            parts = folder.split("/")
            resolved_parts = []
            for part in parts:
                resolved = cls._folder_name_to_id.get(part, part)
                resolved_parts.append(resolved)
            return "/".join(resolved_parts)

        return folder

    @classmethod
    def _build_folder_map(cls) -> Dict[str, str]:
        """从飞书 API 拉取所有文件夹，构建 名称→ID 和 路径→ID 的映射。"""
        print("   📂 正在获取文件夹列表...")
        try:
            result = subprocess.run(
                [cls.LARK_CLI, "mail", "user_mailbox.folders", "list",
                 "--params", '{"user_mailbox_id":"me"}',
                 "--as", "user", "--format", "json"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                print(f"   ⚠️ 获取文件夹列表失败，将使用原始名称")
                return {}

            data = json.loads(result.stdout) if result.stdout.strip() else {}
            items = data.get("data", {}).get("items", [])

            # 构建 id→{name, parent} 索引
            id_to_info: Dict[str, Dict] = {}
            for item in items:
                fid = item.get("id", "")
                name = item.get("name", "")
                parent_id = item.get("parent_folder_id", "0")
                if fid and name:
                    id_to_info[fid] = {"name": name, "parent": parent_id}

            # 构建名称→ID 映射（含完整路径）
            mapping: Dict[str, str] = {}
            for fid, info in id_to_info.items():
                # 纯名称
                mapping[info["name"]] = fid
                # 完整路径 "父文件夹/子文件夹"
                path = cls._build_folder_path(info["name"], info["parent"], id_to_info)
                if path and path != info["name"]:
                    mapping[path] = fid

            print(f"   ✅ 获取到 {len(items)} 个文件夹，映射 {len(mapping)} 条路径")
            return mapping

        except Exception as e:
            print(f"   ⚠️ 获取文件夹列表异常: {e}")
            return {}

    @staticmethod
    def _build_folder_path(name: str, parent_id: str, id_to_info: Dict[str, Dict]) -> str:
        """递归构建文件夹的完整路径。"""
        if parent_id == "0" or parent_id not in id_to_info:
            return name
        parent = id_to_info[parent_id]
        parent_path = LarkMailScanner._build_folder_path(
            parent["name"], parent["parent"], id_to_info
        )
        return f"{parent_path}/{name}"

    # ── 扫描方法 ─────────────────────────────────────

    @staticmethod
    def _run_lark(args: List[str], timeout: int = 90) -> dict:
        """执行 lark-cli 命令并解析 JSON 输出。"""
        cmd = [LarkMailScanner.LARK_CLI] + args
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            if result.returncode != 0:
                stderr_summary = (result.stderr or "")[:200]
                raise RuntimeError(
                    f"lark-cli 返回非零退出码 {result.returncode}: {stderr_summary}"
                )
            return json.loads(result.stdout) if result.stdout.strip() else {}
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"lark-cli 命令超时 ({timeout}s): {' '.join(cmd)}")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"lark-cli 输出解析失败: {e}")

    def scan_triage(
        self,
        date_from: Optional[str] = None,
        days_back: int = 7,
        max_results: int = 200,
        folder: str = "INBOX",
        query: Optional[str] = None,
    ) -> List[Dict]:
        """通过 +triage 扫描邮件摘要。

        Args:
            date_from: 扫描结束日期 YYYY-MM-DD，None 表示今天
            days_back: 往前推的天数
            max_results: 最大返回数
            folder: 邮件文件夹，默认 INBOX（支持 SENT/DRAFT/TRASH/SPAM/ARCHIVED 等系统文件夹和自定义文件夹）
            query: 全文搜索关键词。非空时走 search 路径（跨全文件夹，忽略 folder），
                   用于捞取被归档到 priority/自定义文件夹的周报（list 路径会漏）

        Returns:
            邮件摘要列表 [{message_id, subject, from, date, folder, labels, thread_id}]
        """
        self._folder = folder
        # 计算时间窗口
        if date_from:
            end_date = datetime.strptime(date_from, "%Y-%m-%d")
        else:
            end_date = datetime.now()
        end_date = end_date.replace(hour=23, minute=59, second=59)
        start_date = end_date - timedelta(days=days_back)
        start_date = start_date.replace(hour=0, minute=0, second=0)

        start_str = start_date.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        end_str = end_date.strftime("%Y-%m-%dT%H:%M:%S+08:00")

        use_search = bool(query)

        # 构建 filter
        # search 路径：只带 time_range（跨全文件夹）；folder 路径：带 folder + time_range
        # 注意：time_range 与 folder_id 不兼容，自定义文件夹由代码端过滤
        use_client_filter = False
        filter_obj: Dict[str, Any] = {}
        if use_search:
            filter_obj["time_range"] = {"start_time": start_str, "end_time": end_str}
        else:
            # 解析文件夹名→ID（支持中文名称自动映射）
            resolved_folder = self._resolve_folder(folder)
            use_client_filter = resolved_folder.isdigit()
            if use_client_filter:
                filter_obj["folder_id"] = resolved_folder
            else:
                filter_obj["folder"] = resolved_folder
                filter_obj["time_range"] = {"start_time": start_str, "end_time": end_str}

        triage_filter = json.dumps(filter_obj)

        if use_search:
            print(f"🔍 搜索「{query}」: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")
        else:
            print(f"📬 扫描: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")

        # 翻页拉取，确保不遗漏
        all_messages: List[Dict] = []
        page_token: Optional[str] = None
        page_count = 0
        max_pages = 10  # 安全上限

        while page_count < max_pages:
            page_count += 1
            cmd_args = [
                "mail", "+triage",
                "--as", "user",
                "--filter", triage_filter,
                "--max", str(max_results),
                "--format", "json",
            ]
            if use_search:
                cmd_args += ["--query", query]
            if page_token:
                cmd_args += ["--page-token", page_token]

            result = self._run_lark(cmd_args, timeout=90)
            messages = result.get("messages", [])
            all_messages.extend(messages)

            has_more = result.get("has_more", False)
            page_token = result.get("page_token", "")

            # 如果本页未满载，说明已到末尾
            if len(messages) < max_results:
                break
            if not has_more or not page_token:
                break

        # 自定义文件夹：代码端按时间窗口过滤
        if use_client_filter:
            filtered: List[Dict] = []
            for msg in all_messages:
                msg_date = msg.get("date", "")
                if msg_date:
                    try:
                        # 格式: "2026-05-30T15:05:15Z" 或 "2026-05-31 16:01"
                        dt_str = msg_date.replace("Z", "+00:00").replace(" ", "T")
                        if "T" not in dt_str:
                            continue
                        # 手动解析 ISO 格式
                        dt = datetime.fromisoformat(dt_str)
                        dt_naive = dt.replace(tzinfo=None) if dt.tzinfo else dt
                        if start_date <= dt_naive <= end_date:
                            filtered.append(msg)
                    except (ValueError, TypeError):
                        filtered.append(msg)  # 日期解析失败，保留
                else:
                    filtered.append(msg)  # 无日期，保留
            before = len(all_messages)
            all_messages = filtered
            if before > 0:
                print(f"   获取到 {before} 封（代码端过滤后 {len(all_messages)} 封，{page_count} 页）")
                return all_messages

        print(f"   获取到 {len(all_messages)} 封邮件摘要 ({page_count} 页)")
        return all_messages

    def fetch_message(self, message_id: str) -> Dict:
        """获取单封邮件的完整内容。

        Returns:
            邮件详情字典，关键字段：
            - message_id, thread_id, subject
            - head_from: {mail_address, name}
            - to, cc: [{mail_address, name}]
            - date_formatted: "YYYY-MM-DD HH:MM"
            - body_plain_text: 纯文本正文
            - body_html: HTML 正文（可能为空）
            - attachments: [{id, filename, content_type, is_inline}]
            - folder_id, label_ids
        """
        result = self._run_lark([
            "mail", "+message",
            "--message-id", message_id,
            "--as", "user",
            "--html=false",
            "--format", "json",
        ])

        if not result.get("ok"):
            error_msg = result.get("error", {}).get("message", "未知错误")
            raise RuntimeError(f"获取邮件失败 {message_id}: {error_msg}")

        # 统一 date 字段：优先 date_formatted，否则 internal_date
        data = result.get("data", {})
        if "date" not in data:
            date_str = data.get("date_formatted", "")
            if date_str:
                try:
                    data["date"] = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
                except ValueError:
                    data["date"] = datetime.now()
            elif data.get("internal_date"):
                try:
                    ts = int(data["internal_date"]) / 1000
                    data["date"] = datetime.fromtimestamp(ts)
                except Exception:
                    data["date"] = datetime.now()
        return data


def _prefilter_summaries(
    summaries: List[Dict], config: ExtractWeeklyReportsConfig
) -> List[Dict]:
    """按白名单发件人 + 主题关键词预筛 summary，减少完整正文拉取次数。

    summary 的 from 是字符串（"姓名 <email>"），故按 email 子串匹配。
    预筛结果是 EmailFilter 结果的超集（不排除撤回通知），不会丢掉合法周报。
    无白名单时不预筛，保持兼容。
    """
    whitelist_emails = {
        s.get("email", "").lower() for s in config.sender_whitelist if s.get("email")
    }
    if not whitelist_emails:
        return summaries
    keywords = [kw.lower() for kw in config.subject_keywords]

    kept: List[Dict] = []
    for s in summaries:
        from_str = (s.get("from") or "").lower()
        subject_lower = (s.get("subject") or "").lower()
        if not any(email in from_str for email in whitelist_emails):
            continue
        if not any(kw in subject_lower for kw in keywords):
            continue
        kept.append(s)
    return kept


def scan_mailbox(config: ExtractWeeklyReportsConfig) -> List[Dict]:
    """扫描邮箱并返回邮件完整内容列表（对外主函数）。

    默认 search 模式：按 subject_keywords 跨全文件夹关键词搜索，避免带 IMPORTANT 标签的周报
    散落在 priority/自定义文件夹时被 folder list 路径漏扫。folder 模式保留旧的按文件夹 list 行为。
    """
    scanner = LarkMailScanner()
    all_summaries: List[Dict] = []

    if config.scan_mode == "search":
        # 按关键词逐个搜索（跨全文件夹），合并去重
        for kw in config.subject_keywords:
            summaries = scanner.scan_triage(
                date_from=config.date_from,
                days_back=config.date_range_days,
                max_results=200,
                query=kw,
            )
            all_summaries.extend(summaries)
    else:
        # 旧行为：遍历配置的文件夹 list
        for folder in config.scan_folders:
            print(f"\n📁 文件夹: {folder}")
            summaries = scanner.scan_triage(
                date_from=config.date_from,
                days_back=config.date_range_days,
                max_results=200,
                folder=folder,
            )
            all_summaries.extend(summaries)

    # 按日期降序排列，去重（同一封邮件可能命中多个关键词/文件夹）
    seen_ids: set = set()
    unique_summaries: List[Dict] = []
    for s in sorted(all_summaries, key=lambda x: x.get("date", ""), reverse=True):
        mid = s.get("message_id", "")
        if mid and mid not in seen_ids:
            seen_ids.add(mid)
            unique_summaries.append(s)

    print(f"\n   总计获取 {len(all_summaries)} 封（去重后 {len(unique_summaries)} 封）")

    # 白名单预筛：fetch 完整正文前先裁掉无关邮件，把 message GET 次数降到白名单周报数量级
    summaries = _prefilter_summaries(unique_summaries, config)
    if len(summaries) != len(unique_summaries):
        print(f"   白名单预筛后待取: {len(summaries)} 封")

    if not summaries:
        return []

    # Step 2: 获取完整内容（逐封）
    emails = []
    for i, summary in enumerate(summaries, 1):
        msg_id = summary.get("message_id", "")
        subject = summary.get("subject", "")[:40]
        print(f"   [{i}/{len(summaries)}] 获取: {subject}...")

        try:
            full = scanner.fetch_message(msg_id)
            # 合并 triage 中的 labels 信息
            full["labels"] = summary.get("labels", "")
            # 统一 from 字段：head_from → from
            if "head_from" in full and "from" not in full:
                full["from"] = full["head_from"]
            emails.append(full)
        except Exception as e:
            print(f"      ⚠️ 获取失败: {e}")

    print(f"   成功获取 {len(emails)}/{len(summaries)} 封邮件完整内容")
    return emails


# ─────────────────────────────────────────────────────────
# 去重管理（基于 message_id + 内容哈希，内容变化自动重新提取）
# ─────────────────────────────────────────────────────────

import hashlib


def _load_processed_state() -> dict:
    """加载完整的去重状态。"""
    if not PROCESSED_STATE_PATH.exists():
        return {"processed_ids": {}}
    try:
        return json.loads(PROCESSED_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"processed_ids": {}}


def _save_processed_state(state: dict) -> None:
    """保存去重状态。"""
    PROCESSED_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["last_updated"] = datetime.now().isoformat()
    PROCESSED_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _content_hash(email_data: Dict) -> str:
    """计算邮件内容的 MD5 哈希（用于检测内容变化）。"""
    extracted = email_data.get("extracted", {})
    content = extracted.get("content", "")
    # 包含发件人和主题，确保同一封邮件不同发件人不会误匹配
    fingerprint = f"{email_data.get('sender_name','')}|{email_data.get('subject','')}|{content}"
    return hashlib.md5(fingerprint.encode("utf-8")).hexdigest()


def dedup_emails(emails: List[Dict], force: bool = False) -> Tuple[List[Dict], int]:
    """过滤已处理且内容未变化的邮件。

    策略：
    - 新 message_id → 始终处理
    - 已知 message_id + 内容不变 → 跳过
    - 已知 message_id + 内容变化 → 重新处理（如飞书文档更新）
    - --force → 跳过所有去重

    Returns:
        (需处理的邮件列表, 跳过的数量)
    """
    state = _load_processed_state()
    processed = state.get("processed_ids", {})

    if force:
        return emails, 0

    new_emails = []
    skipped = 0
    updated_count = 0

    for em in emails:
        msg_id = em.get("message_id", "")
        if not msg_id:
            new_emails.append(em)
            continue

        prev = processed.get(msg_id)
        if prev is None:
            # 新品
            new_emails.append(em)
            continue

        # 已知 message_id：比较内容哈希
        current_hash = _content_hash(em)
        prev_hash = prev if isinstance(prev, str) else prev.get("content_hash", "")

        if current_hash == prev_hash:
            skipped += 1
        else:
            # 内容已变化（如飞书文档更新）
            updated_count += 1
            new_emails.append(em)

    if skipped:
        print(f"   🔄 跳过（内容未变）: {skipped} 封")
    if updated_count:
        print(f"   🔁 内容已变化，重新提取: {updated_count} 封")
    return new_emails, skipped


def mark_processed(msg_id: str, email_data: Dict) -> None:
    """标记单封邮件为已处理（含内容哈希）。"""
    state = _load_processed_state()
    processed = state.setdefault("processed_ids", {})
    processed[msg_id] = {
        "processed_at": datetime.now().isoformat(),
        "content_hash": _content_hash(email_data),
    }
    _save_processed_state(state)


# ─────────────────────────────────────────────────────────
# 内容提取（移植自 _weekly-reports-from-email/extractor.py）
# ─────────────────────────────────────────────────────────


class EmailExtractor:
    """提取邮件正文：HTML→纯文本、内容清理、判断是否需要高级模型。"""

    @staticmethod
    def html_to_text(html_content: str) -> str:
        """使用 BeautifulSoup 将 HTML 转为纯文本。"""
        if not html_content:
            return ""

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, "html.parser")
            for tag in soup(["script", "style", "meta", "link", "head"]):
                tag.decompose()
            text = soup.get_text()
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            return "\n\n".join(lines)
        except Exception:
            # 降级：正则移除标签
            import re
            text = re.sub(r"<[^>]+>", "", html_content)
            text = text.replace("&nbsp;", " ").replace("&amp;", "&")
            text = text.replace("&lt;", "<").replace("&gt;", ">")
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            return "\n\n".join(lines)

    @staticmethod
    def clean_text(text: str) -> str:
        """清理文本：移除控制字符、统一换行、压缩空白。"""
        if not text:
            return ""
        import re
        text = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]", "", text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"\n{3,}", "\n\n", text)
        lines = [line.strip() for line in text.split("\n")]
        result = []
        prev_empty = False
        for line in lines:
            if line:
                result.append(line)
                prev_empty = False
            elif not prev_empty:
                result.append("")
                prev_empty = True
        return "\n".join(result).strip()

    @classmethod
    def extract(cls, email_data: Dict) -> Dict:
        """提取邮件核心内容。

        lark-mail 返回字段：body_plain_text, body_html, attachments
        兼容原项目字段：body_text

        Returns:
            {content, content_source, has_images, has_attachments,
             attachment_count, needs_advanced_model}
        """
        body_text = email_data.get("body_plain_text") or email_data.get("body_text", "")
        body_html = email_data.get("body_html", "")
        attachments = email_data.get("attachments", [])

        has_image_attachments = any(
            att.get("content_type", "").startswith("image/")
            or att.get("is_inline", False)
            for att in attachments
        )

        # 优先纯文本（长度 > 50），否则从 HTML 提取
        content = ""
        content_source = ""
        if body_text and len(body_text.strip()) > 50:
            content = body_text
            content_source = "text"
        elif body_html:
            content = cls.html_to_text(body_html)
            content_source = "html"
        elif body_text:
            content = body_text
            content_source = "text_short"

        content = cls.clean_text(content)

        # 判断是否需要高级模型
        needs_advanced = False
        if not content or len(content.strip()) < 100:
            needs_advanced = True
        if has_image_attachments:
            needs_advanced = True

        return {
            "content": content,
            "content_source": content_source,
            "has_images": has_image_attachments,
            "has_attachments": len(attachments) > 0,
            "attachment_count": len(attachments),
            "needs_advanced_model": needs_advanced,
        }


# ─────────────────────────────────────────────────────────
# 飞书文档内容拉取（处理「仅一个链接」类型的周报邮件）
# ─────────────────────────────────────────────────────────


class FeishuDocFetcher:
    """检测邮件中的飞书文档链接，拉取文档正文内容。"""

    # 飞书文档 URL 模式：/docx/, /wiki/, /docs/
    FEISHU_DOC_URL_RE = re.compile(
        r"https?://[a-zA-Z0-9.-]+\.feishu\.cn/(docx|wiki|docs)/([a-zA-Z0-9_-]+)",
        re.IGNORECASE,
    )

    @classmethod
    def extract_feishu_url(cls, text: str) -> Optional[str]:
        """从文本中提取第一个飞书文档 URL。"""
        match = cls.FEISHU_DOC_URL_RE.search(text)
        return match.group(0) if match else None

    @classmethod
    def is_link_only_email(cls, content: str) -> bool:
        """判断邮件内容是否「仅一个飞书链接」（或内容极少，链接是主体）。

        判断条件：
        1. 内容 ≤ 500 字符
        2. 包含飞书文档链接
        """
        if not content:
            return False
        cleaned = content.strip()
        if len(cleaned) > 500:
            return False
        return cls.extract_feishu_url(cleaned) is not None

    @classmethod
    def fetch_doc_content(cls, url: str, timeout: int = 60) -> Optional[str]:
        """通过 lark-cli 拉取飞书文档内容并转为可读文本。

        Returns:
            文档的文本内容（Markdown 或 XML 转文本），失败返回 None
        """
        print(f"      📄 拉取飞书文档: {url[:60]}...")
        try:
            result = subprocess.run(
                [
                    "lark-cli", "docs", "+fetch",
                    "--api-version", "v2",
                    "--doc", url,
                    "--as", "user",
                    "--format", "json",
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                stderr_summary = (result.stderr or "")[:200]
                print(f"      ⚠️ 飞书文档拉取失败: {stderr_summary}")
                return None

            data = json.loads(result.stdout) if result.stdout.strip() else {}
            if not data.get("ok"):
                err_msg = data.get("error", {}).get("message", "未知错误")
                print(f"      ⚠️ 飞书文档 API 错误: {err_msg}")
                return None

            doc_data = data.get("data", {})
            title = doc_data.get("title", "")

            # v2 API 返回两种格式：
            # 1. Markdown: data.markdown
            # 2. XML (docx): data.document.content
            markdown = doc_data.get("markdown", "")
            xml_content = doc_data.get("document", {}).get("content", "")

            if markdown:
                text = markdown
            elif xml_content:
                # XML → 纯文本（用 BeautifulSoup 解析）
                text = cls._xml_to_text(xml_content)
            else:
                print(f"      ⚠️ 飞书文档内容为空")
                return None

            if title and not markdown:
                text = f"# {title}\n\n{text}"
            elif title and markdown and not markdown.startswith("#"):
                text = f"# {title}\n\n{text}"

            print(f"      ✅ 拉取成功: {title or '(无标题)'} ({len(text)} 字符)")
            return text

        except subprocess.TimeoutExpired:
            print(f"      ⚠️ 飞书文档拉取超时")
            return None
        except json.JSONDecodeError as e:
            print(f"      ⚠️ 飞书文档输出解析失败: {e}")
            return None
        except Exception as e:
            print(f"      ⚠️ 飞书文档拉取异常: {e}")
            return None

    @classmethod
    def _xml_to_text(cls, xml_content: str) -> str:
        """将飞书 Docx XML 转为可读文本。

        Docx XML 使用 HTML-like 标签（h1/h2/p/ul/li 等），直接复用 EmailExtractor.html_to_text。
        """
        if not xml_content:
            return ""
        # Docx XML 的标签和 HTML 基本一致，直接用 html_to_text
        return EmailExtractor.html_to_text(xml_content)


# ─────────────────────────────────────────────────────────
# AI 处理（接入 Iris LLM Provider，替代原项目的独立 API 调用）
# ─────────────────────────────────────────────────────────


class AIReportProcessor:
    """使用 Iris 的 LLM Provider 对周报内容进行结构化提取。

    替代 _weekly-reports-from-email/ai_processor.py，
    不再直接调 requests，而是走 Iris 的 EnvironmentConfiguredLLMProvider。
    """

    def __init__(self, provider: Any, prompt_template: str):
        self._provider = provider
        self._template = prompt_template

    def _build_prompt(
        self, content: str, subject: str, sender_name: str, has_images: bool = False
    ) -> str:
        """用模板构建 prompt。"""
        truncated = content[:8000] if content else ""
        if has_images:
            truncated = content[:4000] if content else ""

        return self._template.format(
            sender_name=sender_name,
            subject=subject,
            content=truncated or "[邮件正文内容为空或极少，主要内容可能在图片中]",
        )

    def _call_llm(self, prompt: str, use_advanced: bool = False) -> Optional[str]:
        """通过 Iris LLM Provider 调用模型。"""
        from iris.llm import LLMRequest

        route_context = {
            "input_type": "image" if use_advanced else "text",
            "task_type": "extraction",
            "complexity": "advanced" if use_advanced else "standard",
            "use_case": "weekly_report_extraction",
        }

        request = LLMRequest(
            prompt=prompt,
            route_context=route_context,
        )

        try:
            response = self._provider.generate(request)
            return response.text.strip() if response.text else None
        except Exception as e:
            print(f"      LLM 调用失败: {e}")
            return None

    def process_email(self, email_data: Dict) -> Dict:
        """处理单封邮件：选择合适的模型，提取结构化周报。"""
        extracted = email_data.get("extracted", {})
        content = extracted.get("content", "")
        needs_advanced = extracted.get("needs_advanced_model", False)
        has_images = extracted.get("has_images", False)
        subject = email_data.get("subject", "")
        sender_name = email_data.get("sender_name", "Unknown")

        print(f"      AI 处理: {sender_name} - {subject[:30]}...")

        result = None
        model_used = "base"

        if needs_advanced:
            print(f"         使用 adv_model...")
            prompt = self._build_prompt(content, subject, sender_name, has_images=True)
            result = self._call_llm(prompt, use_advanced=True)
            model_used = "advanced"
            # 高级模型失败时回退基础模型
            if not result and content:
                print(f"         adv_model 失败，回退 base_model...")
                prompt = self._build_prompt(content, subject, sender_name)
                result = self._call_llm(prompt, use_advanced=False)
                model_used = "base"
        else:
            print(f"         使用 base_model...")
            prompt = self._build_prompt(content, subject, sender_name)
            result = self._call_llm(prompt, use_advanced=False)

        email_data["ai_processed"] = result is not None
        email_data["ai_content"] = result if result else content
        email_data["ai_model_used"] = model_used

        if result:
            print(f"         AI 处理成功 ({model_used})")
        else:
            print(f"         AI 处理失败，使用原始内容")

        return email_data


# ─────────────────────────────────────────────────────────
# Markdown 生成（移植自 _weekly-reports-from-email/markdown_generator.py）
# ─────────────────────────────────────────────────────────


class WeeklyReportMarkdownGenerator:
    """生成格式化的周报 Markdown 文件。"""

    def __init__(self, output_dir: str, filename_format: str, file_overwrite: bool = False):
        self.output_dir = os.path.expanduser(output_dir)
        self.filename_format = filename_format
        self.file_overwrite = file_overwrite
        os.makedirs(self.output_dir, exist_ok=True)

    @staticmethod
    def get_report_week_info(email_date: datetime) -> Tuple[int, int]:
        """根据邮件发送日期判断周报所属周。

        规则：周五~周日(weekday 4-6) → 本周；周一~周四(0-3) → 上周
        """
        weekday = email_date.weekday()
        if weekday <= 3:  # 周一~周四
            report_date = email_date - timedelta(days=7)
        else:  # 周五~周日
            report_date = email_date
        iso_year, iso_week, _ = report_date.isocalendar()
        return iso_year, iso_week

    def format_filename(self, sender_name: str, email_date: datetime) -> str:
        """根据模板生成文件名。"""
        date_str = email_date.strftime("%Y%m%d")
        _, report_week = self.get_report_week_info(email_date)
        week_str = str(report_week).zfill(2)

        filename = self.filename_format.format(
            name=sender_name, date=date_str, week=week_str
        )
        if not filename.endswith(".md"):
            filename += ".md"
        filename = self._sanitize_filename(filename)
        return filename

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """清理文件名中的非法字符。"""
        import re
        filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
        filename = filename.strip(". ")
        if len(filename) > 200:
            name, ext = os.path.splitext(filename)
            filename = name[:200 - len(ext)] + ext
        return filename

    def generate_content(self, email_data: Dict) -> str:
        """生成邮件的 Markdown 内容。"""
        sender_name = email_data.get("sender_name", "Unknown")
        from_info = email_data.get("from", {})
        from_email = from_info.get("mail_address") or from_info.get("email", "")
        subject = email_data.get("subject", "")
        date = email_data.get("date")
        if isinstance(date, str):
            date = datetime.fromisoformat(date.replace("Z", "+00:00").replace("+08:00", "").replace(" ", "T"))
        elif not isinstance(date, datetime):
            date = datetime.now()
        date_str = date.strftime("%Y年%m月%d日")

        ai_content = email_data.get("ai_content", "")
        extracted = email_data.get("extracted", {})
        raw_content = extracted.get("content", "")
        content = ai_content if ai_content else raw_content

        ai_processed = email_data.get("ai_processed", False)
        ai_model = email_data.get("ai_model_used", "")
        has_images = extracted.get("has_images", False)

        lines = [
            f"# 周报 - {sender_name} - {date_str}",
            "",
            "## 邮件信息",
            "",
            f"- **发件人**: {sender_name} <{from_email}>",
            f"- **日期**: {date_str}",
            f"- **主题**: {subject}",
            "",
        ]

        if ai_processed:
            model_name = "高级模型" if ai_model == "advanced" else "基础模型"
            lines.append(f"*本内容由AI ({model_name}) 辅助整理*")
            lines.append("")

        if has_images:
            lines.append("*注意: 原邮件包含图片，部分内容可能无法完整提取*")
            lines.append("")

        lines.append("---")
        lines.append("")
        lines.append("## 周报内容")
        lines.append("")
        lines.append(content if content else "*未能提取到有效内容*")
        lines.append("")

        return "\n".join(lines)

    def save_markdown(self, email_data: Dict) -> str:
        """保存邮件为 Markdown 文件。"""
        sender_name = email_data.get("sender_name", "Unknown")
        date = email_data.get("date")
        if isinstance(date, str):
            date = datetime.fromisoformat(date.replace("Z", "+00:00").replace("+08:00", "").replace(" ", "T"))
        elif not isinstance(date, datetime):
            date = datetime.now()

        filename = self.format_filename(sender_name, date)
        filepath = os.path.join(self.output_dir, filename)

        # 文件已存在且不允许覆盖 → 自动添加序号
        if os.path.exists(filepath) and not self.file_overwrite:
            base, ext = os.path.splitext(filepath)
            counter = 1
            while os.path.exists(f"{base}_{counter}{ext}"):
                counter += 1
            filepath = f"{base}_{counter}{ext}"

        content = self.generate_content(email_data)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        return filepath


# ─────────────────────────────────────────────────────────
# 子命令
# ─────────────────────────────────────────────────────────


def cmd_run(args: argparse.Namespace) -> None:
    """执行完整周报提取流水线：扫描 → 过滤 → 提取 → AI → 生成。"""
    from iris.config.loader import load_config_bundle
    from iris.llm import EnvironmentConfiguredLLMProvider

    print("=" * 60)
    print("  Iris 周报邮件提取")
    print("=" * 60)
    print()

    # ── 加载配置 ──────────────────────────────────────
    try:
        config = ExtractWeeklyReportsConfig(args)
    except FileNotFoundError as e:
        _emit({"error": str(e)}, args.pretty)
        return

    print(f"📋 白名单: {len(config.sender_whitelist)} 人")
    print(f"📅 扫描窗口: 最近 {config.date_range_days} 天")
    print(f"📂 输出目录: {config.output_dir}")
    print()

    # ── Step 1: 初始化 LLM Provider ───────────────────
    if not args.skip_ai:
        try:
            bundle = load_config_bundle(PROJECT_ROOT)
            provider = EnvironmentConfiguredLLMProvider(bundle)
            prompt_template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
            ai_processor = AIReportProcessor(provider, prompt_template)
            print("🤖 LLM Provider 已就绪")
        except Exception as e:
            print(f"⚠️ LLM Provider 初始化失败: {e}")
            print("   将使用 --skip-ai 模式继续")
            args.skip_ai = True
    else:
        ai_processor = None
        print("⏭️  跳过 AI 处理（--skip-ai）")

    # ── Step 2: 扫描邮箱 ─────────────────────────────
    print()
    print("─" * 40)
    print("【Step 1/5】扫描邮箱...")
    emails = scan_mailbox(config)

    if not emails:
        print("\n📭 未找到任何邮件")
        return

    # ── Step 2: 过滤 ──────────────────────────────────
    print()
    print("─" * 40)
    print("【Step 2/4】过滤周报邮件...")
    email_filter = EmailFilter(config.sender_whitelist, config.subject_keywords)
    weekly_reports = email_filter.filter_emails(emails)

    if not weekly_reports:
        print("\n📭 未找到匹配的周报邮件")
        return

    print(f"   ✅ 识别到 {len(weekly_reports)} 封周报邮件:")
    for r in weekly_reports:
        print(f"      - {r.get('sender_name', '?')}: {r.get('subject', '')[:50]}")

    if args.dry_run:
        print()
        print("🔍 --dry-run 模式：以上是将要处理的邮件，未实际执行")
        return

    # ── Step 5: 提取 + AI + 生成 ──────────────────────
    print()
    print("─" * 40)
    print("【Step 3/4】提取 + 去重 + AI + 生成...")
    print()

    generator = WeeklyReportMarkdownGenerator(
        output_dir=config.output_dir,
        filename_format=config.filename_format,
        file_overwrite=config.file_overwrite,
    )

    generated_files = []
    skipped_by_dedup = 0

    for i, report in enumerate(weekly_reports, 1):
        msg_id = report.get("message_id", "")
        sender = report.get("sender_name", "Unknown")
        subject = report.get("subject", "")[:50]
        print(f"[{i}/{len(weekly_reports)}] {sender} - {subject}")

        try:
            # 提取内容
            extracted = EmailExtractor.extract(report)
            report["extracted"] = extracted

            content = extracted.get("content", "")
            content_len = len(content)
            needs_adv = extracted.get("needs_advanced_model", False)

            # ── 飞书文档链接检测与内容拉取 ─────────────────
            if FeishuDocFetcher.is_link_only_email(content):
                print(f"    检测到飞书链接型邮件，尝试拉取文档内容...")
                feishu_url = FeishuDocFetcher.extract_feishu_url(content)
                if feishu_url:
                    doc_content = FeishuDocFetcher.fetch_doc_content(feishu_url)
                    if doc_content:
                        extracted["content"] = doc_content
                        extracted["content_source"] = "feishu_doc"
                        if len(doc_content) >= 100:
                            extracted["needs_advanced_model"] = False
                        content_len = len(doc_content)
                        needs_adv = extracted.get("needs_advanced_model", False)

            print(f"    提取: {content_len} 字符{' (需要高级模型)' if needs_adv else ''}")

            # ── 内容去重检查 ─────────────────────────────
            if not args.force:
                current_hash = _content_hash(report)
                state = _load_processed_state()
                prev = state.get("processed_ids", {}).get(msg_id)
                prev_hash = prev.get("content_hash", "") if isinstance(prev, dict) else ""
                if prev and current_hash == prev_hash:
                    print(f"     ⏭️  内容未变化，跳过")
                    skipped_by_dedup += 1
                    continue
                if prev and current_hash != prev_hash:
                    print(f"     🔁 内容已变化，重新提取")

            # AI 处理
            if ai_processor and not args.skip_ai:
                report = ai_processor.process_email(report)

            # 生成 Markdown
            filepath = generator.save_markdown(report)
            generated_files.append(filepath)
            mark_processed(msg_id, report)
            print(f"    生成: {os.path.basename(filepath)}")

        except Exception as e:
            print(f"    ❌ 处理失败: {e}")

    # ── 汇总 ──────────────────────────────────────────
    print()

    # ── 汇总 ──────────────────────────────────────────
    print()
    print("=" * 60)
    print("  处理完成！")
    print("=" * 60)
    print(f"  扫描邮件: {len(emails)} 封")
    print(f"  识别周报: {len(weekly_reports)} 封")
    print(f"  成功生成: {len(generated_files)} 个文件")
    if skipped_by_dedup:
        print(f"  跳过（内容未变）: {skipped_by_dedup} 封")
    print(f"  输出目录: {config.output_dir}")
    print("=" * 60)

    if args.pretty:
        print()
        print("生成的文件:")
        for f in generated_files:
            print(f"  📄 {os.path.basename(f)}")


def cmd_status(args: argparse.Namespace) -> None:
    """显示配置和白名单摘要。"""
    if not CONFIG_PATH.exists():
        _emit({"error": "配置文件不存在，请先运行 init-config"}, args.pretty)
        return

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    filters = config.get("filters", {})
    output = config.get("output", {})

    summary = {
        "config_path": str(CONFIG_PATH),
        "whitelist_count": len(filters.get("sender_whitelist", [])),
        "whitelist": [f"{s['name']} <{s['email']}>" for s in filters.get("sender_whitelist", [])],
        "keywords": filters.get("subject_keywords", []),
        "scan_folders": config.get("scan", {}).get("folders", ["INBOX"]),
        "date_range_days": filters.get("date_range_days", 7),
        "output_dir": output.get("dir", ""),
        "filename_format": output.get("filename_format", ""),
    }

    # 去重状态
    state = _load_processed_state()
    summary["processed_count"] = len(state.get("processed_ids", {}))
    summary["last_updated"] = state.get("last_updated", "从未运行")

    _emit(summary, args.pretty)


def cmd_init_config(args: argparse.Namespace) -> None:
    """初始化配置文件。"""
    if CONFIG_PATH.exists():
        print(f"配置文件已存在: {CONFIG_PATH}")
        return

    default_config = {
        "version": "1.0",
        "scan": {
            "folders": ["INBOX"],
        },
        "filters": {
            "subject_keywords": ["周报", "周会", "week report", "weekly"],
            "sender_whitelist": [],
            "date_range_days": 7,
            "date_from": None,
        },
        "output": {
            "dir": "",
            "filename_format": "周报-w{week}-{name}-{date}.md",
            "file_overwrite": False,
        },
    }
    CONFIG_PATH.write_text(
        json.dumps(default_config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"配置文件已创建: {CONFIG_PATH}")
    print("请编辑该文件，填入白名单成员和输出目录。")


def cmd_sender_add(args: argparse.Namespace) -> None:
    """添加白名单成员。"""
    if not CONFIG_PATH.exists():
        print("配置文件不存在，请先运行 init-config")
        return

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    whitelist = config.setdefault("filters", {}).setdefault("sender_whitelist", [])

    name = args.name
    email = args.email.lower()

    # 检查是否已存在
    for sender in whitelist:
        if sender.get("email", "").lower() == email:
            print(f"成员已存在: {sender['name']} <{sender['email']}>")
            return

    whitelist.append({"name": name, "email": email})
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已添加: {name} <{email}>")


def cmd_sender_rm(args: argparse.Namespace) -> None:
    """移除白名单成员。"""
    if not CONFIG_PATH.exists():
        print("配置文件不存在，请先运行 init-config")
        return

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    whitelist = config.setdefault("filters", {}).setdefault("sender_whitelist", [])

    email = args.email.lower()
    removed = None
    new_whitelist = []
    for sender in whitelist:
        if sender.get("email", "").lower() == email:
            removed = sender
        else:
            new_whitelist.append(sender)

    if removed:
        config["filters"]["sender_whitelist"] = new_whitelist
        CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已移除: {removed['name']} <{removed['email']}>")
    else:
        print(f"未找到成员: {email}")


# ─────────────────────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="extract-weekly-reports",
        description="从飞书邮箱提取白名单成员的周报邮件，生成结构化 Markdown 文件",
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    # run
    p_run = sub.add_parser("run", help="执行周报提取流水线")
    p_run.add_argument("--days", type=int, help="扫描天数范围（覆盖配置）")
    p_run.add_argument("--date-from", type=str, help="扫描结束日期 YYYY-MM-DD")
    p_run.add_argument("--output", type=str, help="输出目录（覆盖配置）")
    p_run.add_argument("--force", action="store_true", help="强制重新处理，跳过去重")
    p_run.add_argument("--dry-run", action="store_true", help="仅列出将处理的邮件，不实际执行")
    p_run.add_argument("--skip-ai", action="store_true", help="跳过 AI 处理，使用原始提取文本")
    p_run.add_argument("--pretty", action="store_true", help="人类可读输出")

    # status
    p_status = sub.add_parser("status", help="显示配置和白名单摘要")
    p_status.add_argument("--pretty", action="store_true", help="人类可读输出")

    # init-config
    sub.add_parser("init-config", help="初始化配置文件")

    # sender-add
    p_add = sub.add_parser("sender-add", help="添加白名单成员")
    p_add.add_argument("--name", required=True, help="姓名")
    p_add.add_argument("--email", required=True, help="邮箱地址")

    # sender-rm
    p_rm = sub.add_parser("sender-rm", help="移除白名单成员")
    p_rm.add_argument("--email", required=True, help="邮箱地址")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "init-config":
        cmd_init_config(args)
    elif args.command == "sender-add":
        cmd_sender_add(args)
    elif args.command == "sender-rm":
        cmd_sender_rm(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

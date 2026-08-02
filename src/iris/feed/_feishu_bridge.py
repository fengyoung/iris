"""信息汇聚管道 — 飞书 API 桥接层。

封装 lark-cli 调用，统一错误处理与 JSON 解析。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from iris.feed._types import RawMessage

logger = logging.getLogger(__name__)

# Iris bot 通道：生产环境通过环境变量 IRIS_BOT_USER_ID 配置接收消息的用户 open_id
# （例：在 .env 中设置 IRIS_BOT_USER_ID=ou_xxxx）；未配置时跳过飞书推送。
IRIS_BOT_USER_ID = os.environ.get("IRIS_BOT_USER_ID", "")


def _run_lark_cli(args: List[str], timeout: int = 30) -> Dict[str, Any]:
    """执行 lark-cli 命令并解析 JSON 输出。"""
    cmd = ["lark-cli"] + args + ["--format", "json"]
    logger.debug("lark-cli: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if stderr:
                logger.warning("lark-cli 退出码 %d: %s", result.returncode, stderr[:200])
            return {"ok": False, "error": {"message": stderr or "lark-cli 异常退出"}}
        return json.loads(result.stdout) if result.stdout.strip() else {"ok": False}
    except subprocess.TimeoutExpired:
        logger.error("lark-cli 超时: %s", " ".join(cmd))
        return {"ok": False, "error": {"message": "lark-cli 执行超时"}}
    except json.JSONDecodeError as e:
        logger.error("lark-cli 输出解析失败: %s", e)
        return {"ok": False, "error": {"message": str(e)}}


class FeishuBridge:
    """飞书 API 桥接 — 封装 lark-cli im 子命令。"""

    # ── 群聊发现 ──────────────────────────────────────

    def list_user_chats(self, page_size: int = 50) -> List[Dict[str, Any]]:
        """列出当前用户的所有群聊。"""
        all_chats = []
        page_token = None
        while True:
            args = [
                "im", "+chat-list",
                "--page-size", str(page_size),
                "--types", "group",
            ]
            if page_token:
                args += ["--page-token", page_token]
            resp = _run_lark_cli(args)
            if not resp.get("ok"):
                logger.warning("获取群聊列表失败: %s", resp.get("error", {}).get("message", ""))
                break
            data = resp.get("data", {})
            chats = data.get("chats", [])
            all_chats.extend(chats)
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
            if not page_token:
                break
        return all_chats

    def search_chat_by_name(self, query: str) -> List[Dict[str, Any]]:
        """按名称搜索群聊。"""
        resp = _run_lark_cli([
            "im", "+chat-search",
            "--query", query,
            "--page-size", "10",
        ])
        if not resp.get("ok"):
            return []
        return resp.get("data", {}).get("chats", [])

    # ── 消息搜索 ──────────────────────────────────────

    def search_messages(
        self,
        chat_id: str,
        since: datetime,
        until: datetime,
        page_size: int = 50,
        page_token: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str], int]:
        """搜索指定群聊在时间范围内的消息。

        返回 (消息列表, 下一页 token, 总数)。
        """
        start_str = since.strftime("%Y-%m-%dT00:00:00+08:00")
        end_str = until.strftime("%Y-%m-%dT00:00:00+08:00")
        args = [
            "im", "+messages-search",
            "--chat-id", chat_id,
            "--start", start_str,
            "--end", end_str,
            "--page-size", str(page_size),
        ]
        if page_token:
            args += ["--page-token", page_token]
        resp = _run_lark_cli(args, timeout=60)
        if not resp.get("ok"):
            logger.warning("消息搜索失败: %s", resp.get("error", {}).get("message", ""))
            return [], None, 0
        data = resp.get("data", {})
        messages = data.get("messages", [])
        total = data.get("total", len(messages))
        next_token = data.get("page_token") if data.get("has_more") else None
        return messages, next_token, total

    def search_all_messages(
        self,
        chat_id: str,
        since: datetime,
        until: datetime,
        page_size: int = 50,
        max_pages: int = 40,
    ) -> List[Dict[str, Any]]:
        """分页拉取所有消息。"""
        all_msgs = []
        page_token = None
        for _ in range(max_pages):
            msgs, next_token, _ = self.search_messages(
                chat_id, since, until, page_size=page_size, page_token=page_token,
            )
            if not msgs:
                break
            all_msgs.extend(msgs)
            if not next_token:
                break
            page_token = next_token
        return all_msgs

    # ── 消息转换 ──────────────────────────────────────

    @staticmethod
    def raw_to_message(raw: Dict[str, Any]) -> RawMessage:
        """将飞书 API 原始消息转换为 RawMessage。"""
        content = raw.get("content", "") or ""
        # 检测飞书文档链接
        doc_links = []
        has_doc = False
        urls = re.findall(r'https?://[^\s]*feishu[^\s]*/(docx|wiki|sheet|base)/(\w+)', content)
        if urls:
            has_doc = True
            for m in re.finditer(r'https?://[^\s]*feishu[^\s]*/(docx|wiki|sheet|base)/\w+', content):
                doc_links.append(m.group(0))

        send_time_str = raw.get("create_time", "")
        send_time = None
        if send_time_str:
            try:
                send_time = datetime.strptime(send_time_str, "%Y-%m-%d %H:%M")
            except ValueError:
                try:
                    send_time = datetime.fromisoformat(send_time_str.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    send_time = datetime.now()

        sender = raw.get("sender", {}) or {}
        return RawMessage(
            msg_id=raw.get("message_id", ""),
            chat_id=raw.get("chat_id", ""),
            chat_name=raw.get("chat_name", ""),
            chat_type=raw.get("chat_type", "group"),
            sender_id=sender.get("id", ""),
            sender_name=sender.get("name", ""),
            content=content,
            raw_content=raw,
            msg_type=raw.get("msg_type", "text"),
            send_time=send_time or datetime.now(),
            has_doc_link=has_doc,
            doc_links=doc_links,
        )

    # ── 消息发送 ──────────────────────────────────────

    def send_to_user(self, user_id: str, text: str) -> bool:
        """以 bot 身份发送单聊消息。"""
        resp = _run_lark_cli([
            "im", "+messages-send",
            "--as", "bot",
            "--user-id", user_id,
            "--text", text,
        ])
        ok = resp.get("ok", False)
        if not ok:
            logger.error("发送消息失败: %s", resp.get("error", {}).get("message", ""))
        return ok

    def send_markdown_to_user(self, user_id: str, markdown: str) -> bool:
        """以 bot 身份发送 Markdown 消息。"""
        resp = _run_lark_cli([
            "im", "+messages-send",
            "--as", "bot",
            "--user-id", user_id,
            "--markdown", markdown,
        ])
        ok = resp.get("ok", False)
        if not ok:
            logger.error("发送 Markdown 失败: %s", resp.get("error", {}).get("message", ""))
        return ok

    @staticmethod
    def get_display_name(raw: Dict[str, Any]) -> str:
        """从群聊信息中提取展示名称。"""
        name = raw.get("name", "")
        if name:
            return name
        return raw.get("chat_id", "")[:16] + "…"

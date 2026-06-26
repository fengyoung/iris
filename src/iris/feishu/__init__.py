"""飞书知识库集成。"""
from .client import FeishuClient, FeishuClientError, WikiNodeMeta
from .doc_convert import FeishuDocConverter, FeishuDocConvertError
from .chat_digest import ChatDigester, ChatDigestError
from ._shared import (
    resolve_source_root, resolve_source_sub_dir, resolve_pic_dir,
    resolve_dedup_path, load_dedup_index, save_dedup_index,
    upsert_dedup_item, sanitize_title, extract_date, now_iso,
)

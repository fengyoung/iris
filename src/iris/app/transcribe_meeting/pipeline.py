"""录音转会议纪要流水线 — 适配新 LLM-WIKI 结构。"""

from __future__ import annotations

import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from iris.config.loader import ConfigBundle
from iris.llm import LLMService, LLMProviderError

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = "你是一个专业的会议纪要提取专家，擅长从语音转写文本中提取结构化会议纪要。你会仔细校正 ASR 误识别，准确提取信息。注意：直接输出会议纪要正文，不要输出任何前缀说明、开场白或打招呼内容。"


class TranscribeMeetingPipeline:
    def __init__(self, bundle: ConfigBundle) -> None:
        self._bundle = bundle
        self._llm = LLMService(bundle)
        self._wiki_root = Path(bundle.wiki["wiki_root"]).resolve() if bundle.wiki else Path()
        self._temp_dir = bundle.root / bundle.app["paths"]["temp_dir"]

    def run(self, audio_path: str = "", *, transcript_path: Optional[str] = None,
            output_path: Optional[str] = None, whisper_model: str = "base",
            force_retranscribe: bool = False, to_source: bool = False,
            model: Optional[str] = None) -> Dict[str, Any]:
        has_audio = bool(audio_path)
        has_text = transcript_path is not None
        if not has_audio and not has_text:
            raise ValueError("必须指定 --audio-file 或 --transcript-file")
        if has_audio and has_text:
            raise ValueError("只能选其一")

        if has_audio:
            source = Path(audio_path).resolve()
            if not source.exists():
                raise FileNotFoundError(f"文件不存在: {source}")
            stem = source.stem
            source_type = "audio"
        else:
            source = Path(transcript_path)
            # 仅文件名 → 在配置的转写目录查找
            if not source.is_absolute() and not source.exists():
                default_dir = self._get_transcript_search_dir()
                if default_dir:
                    candidate = default_dir / source.name
                    if candidate.exists():
                        source = candidate
            source = source.resolve()
            if not source.exists():
                raise FileNotFoundError(f"转写文件不存在: {source}")
            stem = source.stem
            if stem.endswith("_raw"):
                stem = stem[:-4]
            source_type = "text"

        date_part = stem[:8] if len(stem) >= 8 and stem[:8].isdigit() else ""
        date_part = date_part if date_part else time.strftime("%Y%m%d")
        meeting_type, meeting_topic = self._parse_filename(stem, date_part)
        print(f"[0/3] 识别会议类型={meeting_type}, 主题={meeting_topic}", file=sys.stderr)

        # 任务埋点：文件解析成功后开始（参数错误属瞬时失败，不产生任务记录）
        from iris.taskpanel.reporter import TaskReporter
        with TaskReporter("transcribe-meeting", command="transcribe-meeting") as _tr:
            _tr.report_phase("parse", f"类型: {meeting_type} / {meeting_topic}", progress=0.2)

            # Step 1: 获取转写文本
            if has_audio:
                audio = source
                transcript_save_path = self._temp_dir / f"{stem}_raw.txt"
                if not force_retranscribe and transcript_save_path.exists():
                    raw_text = transcript_save_path.read_text(encoding="utf-8")
                    word_count = len(raw_text)
                    print(f"[1/3] 跳过 Whisper，使用已有转写（{word_count} 字）", file=sys.stderr)
                else:
                    print(f"[1/3] Whisper 转写中（model={whisper_model}）...", file=sys.stderr)
                    word_count = self._transcribe(audio, transcript_save_path, whisper_model)
                    print(f"     完成：{word_count} 字 → {transcript_save_path.name}", file=sys.stderr)
                raw_transcript = transcript_save_path.read_text(encoding="utf-8")
            else:
                raw_transcript = source.read_text(encoding="utf-8")
                word_count = len(raw_transcript)
                print(f"[1/3] 跳过 Whisper，直接使用转写文本（{word_count} 字）", file=sys.stderr)
            _tr.report_phase("transcribe", f"转写完成（{word_count} 字）", progress=0.5)

            # Step 2: Wiki 上下文（适配新结构）
            print("[2/3] 检索 Wiki 上下文...", file=sys.stderr)
            wiki_context, page_count = self._load_wiki_context()
            print(f"     完成：加载 {page_count} 个 Wiki 页面", file=sys.stderr)
            _tr.report_phase("wiki_context", f"加载 {page_count} 个 Wiki 页面", progress=0.7)

            # 计算会议日期和时长
            meeting_date = self._format_meeting_date(date_part)
            duration = self._calc_duration(raw_transcript)

            # Step 3: LLM 生成会议纪要
            print("[3/3] base_model 生成会议纪要...", file=sys.stderr)
            _tr.report_phase("llm_minutes", "LLM 生成会议纪要", progress=0.85)
            source_filename = source.name  # 来源文件，供输出标识和未来排重
            minutes = self._call_llm(raw_transcript, wiki_context, meeting_type, meeting_topic,
                                     source_filename=source_filename,
                                     meeting_date=meeting_date, duration=duration,
                                     model=model)

            # Step 3b: 路由判定（--to-source 模式）
            route_result = None
            if to_source and not output_path:
                route_result = self._classify_target(raw_transcript, meeting_type, meeting_topic)
                output_path = str(self._resolve_routed_output(route_result, stem))
                route_name = route_result.get("route", "05-会议纪要")
                print(f"     📂 路由归档: {route_name} ← {route_result.get('reason', '')}", file=sys.stderr)

            # ── 注入 frontmatter 元数据 ──────────────────────
            try:
                from iris.core.frontmatter import inject_frontmatter
                _fm_fields = {
                    "title": f"会议纪要 - {meeting_topic}" if meeting_topic else f"会议纪要 - {meeting_type}",
                    "date": meeting_date,
                    "type": "会议纪要",
                    "meeting_type": meeting_type or "",
                    "duration": duration or "",
                    "source": source_filename,
                    "generated": time.strftime("%Y-%m-%d"),
                    "route": route_result.get("route", "") if route_result else "",
                }
                # 尝试从 LLM 输出中提取参会人员
                _participants = self._extract_participants(minutes)
                if _participants:
                    _fm_fields["participants"] = _participants
                minutes = inject_frontmatter(minutes, _fm_fields)
            except Exception:
                pass  # frontmatter 注入失败不应阻塞纪要生成

            if output_path:
                out = Path(output_path).resolve()
            else:
                out = self._temp_dir / f"{stem}.md"
            from iris.core.write_guard import safe_write_text
            safe_write_text(out, minutes, self._bundle, allow_existing_outside=True)
            print(f"     完成 → {out.name}", file=sys.stderr)
            _tr.report_phase("done", f"输出: {out.name}", progress=1.0)

            reported_model = model if model else self._llm.get_provider().get_active_model_config("base_model")["model"]
            result = {"audio_file": str(source) if has_audio else "", "transcript_file": str(source) if has_text else "",
                      "source_type": source_type, "word_count": word_count, "wiki_pages_loaded": page_count,
                      "output_file": str(out), "model": reported_model}
            if route_result:
                result["route"] = route_result.get("route", "")
                result["route_reason"] = route_result.get("reason", "")
        return result

    # ── 日期与时长计算 ──────────────────────────────────────────

    @staticmethod
    def _format_meeting_date(date_part: str) -> str:
        """将 8 位日期 YYYYMMDD 格式化为 YYYY-MM-DD，失败返回空字符串。"""
        if len(date_part) == 8 and date_part.isdigit():
            return f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:8]}"
        return ""

    @staticmethod
    def _calc_duration(raw_transcript: str) -> str:
        """从转写文本中提取时间戳计算时长。

        支持格式：`说话人  HH:MM:SS` 或 `说话人  MM:SS`。
        返回 "X小时X分" 或空字符串。
        """
        pattern = re.compile(r'\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b')
        timestamps = []
        for m in pattern.finditer(raw_transcript):
            h = int(m.group(1))
            mi = int(m.group(2))
            s = int(m.group(3)) if m.group(3) else 0
            timestamps.append(h * 3600 + mi * 60 + s)

        if len(timestamps) < 2:
            return ""

        # 取有效时间范围（过滤掉可能的噪音时间戳）
        first_ts = timestamps[0]
        last_ts = timestamps[-1]

        # 如果最后的时间戳异常大（> 24小时），回退到合理范围内
        if last_ts > 86400:
            for ts in reversed(timestamps):
                if ts < 86400:
                    last_ts = ts
                    break

        if first_ts >= last_ts:
            return ""

        total_seconds = last_ts - first_ts
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60

        if hours > 0:
            return f"{hours}小时{minutes}分"
        return f"{minutes}分"

    @staticmethod
    def _extract_participants(minutes: str) -> str:
        """从 LLM 会议纪要输出中提取参会人员列表。

        匹配 ``## 参会人员`` 节后的文本行，提取逗号/顿号分隔的姓名。
        返回顿号分隔的姓名串，无法提取时返回空字符串。
        """
        m = re.search(r'##\s*参会人员\s*\n+(.*?)(?:\n##|\n#|\Z)', minutes, re.DOTALL)
        if not m:
            return ""
        block = m.group(1).strip()
        # 移除列表标记 "- " 或 "* "
        block = re.sub(r'^[-*]\s+', '', block, flags=re.MULTILINE)
        # 按逗号、顿号、换行拆分
        parts = re.split(r'[,，、\n]+', block)
        names = [p.strip() for p in parts if p.strip() and len(p.strip()) <= 10]
        return "、".join(names) if names else ""

    # ── 纪要路由 ────────────────────────────────────────────────

    def _load_routing_config(self) -> Dict[str, Any]:
        """从 ConfigBundle 读取路由目标描述，不存在时返回空字典。

        配置来源：config/meeting_routes.json（gitignored），
        内容为各 SOURCE 子目录的用途描述、关键词和命名规范。
        """
        if self._bundle.meeting_routes:
            return self._bundle.meeting_routes.get("route_targets", {})
        return {}

    def _build_routing_prompt_section(self, targets: Dict[str, Any]) -> str:
        """构建 LLM 路由判定的 prompt 段（不含硬编码的公司信息）。"""
        if not targets:
            return ""
        lines = ["## 可用归档目录", ""]
        for dir_name, info in targets.items():
            desc = info.get("description", "")
            naming = info.get("naming", "")
            kw = ", ".join(info.get("keywords", []))
            lines.append(f"- {dir_name}")
            if desc:
                lines.append(f"  用途：{desc}")
            if naming:
                lines.append(f"  命名格式：{naming}")
            if kw:
                lines.append(f"  常见关键词：{kw}")
            lines.append("")
        return "\n".join(lines)

    def _classify_target(self, raw_transcript: str,
                         meeting_type: str, meeting_topic: str) -> Dict[str, str]:
        """轻量调用 LLM 判定路由目标目录和文件名。

        返回 {"route": 目录名, "reason": 理由, "filename": 文件名}
        无配置或 LLM 失败时返回默认值（05-会议纪要）。
        """
        targets = self._load_routing_config()
        if not targets:
            return {"route": "05-会议纪要", "reason": "默认路由（未配置路由规则）", "filename": ""}

        route_section = self._build_routing_prompt_section(targets)
        transcript_excerpt = raw_transcript[:1500]

        prompt = f"""你是会议纪要归档路由专家。请根据转写内容判断归档目录。

{route_section}

## 会议信息
类型：{meeting_type or "未知"}
主题：{meeting_topic or "未知"}

## 转写内容（前1500字）
{transcript_excerpt}

请选择最合适的归档目录，并生成符合命名格式的文件名（不含 .md 后缀）。

判定依据：
- 参与人数是首要信号：1对1或双人讨论 → 优先归入「讨论思考」目录，即使有决策和待办
- 多人（≥3人）正式会议，有明确决策/待办/计划 → 会议纪要目录
- 产出正式方案/技术结论 → 方案报告目录
- 外部学习资料 → 参考资料目录

直接输出以下格式（不含多余内容）：
ROUTE: <目录名>
REASON: <一句话理由>
FILENAME: <文件名>"""

        text = self._llm.generate(prompt, route_context={"input_type": "text"}, temperature=0).text
        return self._parse_route_response(text)

    @staticmethod
    def _parse_route_response(text: str) -> Dict[str, str]:
        """解析 LLM 返回的 ROUTE/REASON/FILENAME。支持英文和中文冒号。"""
        result = {"route": "05-会议纪要", "reason": "LLM 解析失败，使用默认路由", "filename": ""}
        for line in text.strip().split("\n"):
            line = line.strip()
            for prefix, key in [("ROUTE：", "route"), ("ROUTE:", "route"),
                                 ("REASON：", "reason"), ("REASON:", "reason"),
                                 ("FILENAME：", "filename"), ("FILENAME:", "filename")]:
                if line.startswith(prefix):
                    val = line[len(prefix):].strip().strip('"').strip("'")
                    if val:
                        result[key] = val
                    break
        return result

    def _resolve_routed_output(self, route_result: Dict[str, str], input_stem: str) -> Path:
        """根据路由结果生成输出文件路径（含归档子目录）。"""
        route = route_result.get("route", "05-会议纪要")
        filename = route_result.get("filename", "")
        if not filename:
            filename = f"{input_stem}.md"
        elif not filename.endswith(".md"):
            filename = f"{filename}.md"

        data_source = self._bundle.data_source
        sources = data_source.get("sources", {})
        for cfg in sources.values():
            if cfg.get("enabled") and cfg.get("path"):
                src_root = Path(cfg["path"]).resolve()
                if src_root.exists():
                    from iris.utils.paths import resolve_source_archive_path
                    return resolve_source_archive_path(src_root, route, filename)
        return self._temp_dir / filename

    def _get_transcript_search_dir(self) -> Path | None:
        """获取转写文件默认搜索目录（OS环境变量 > .env 文件）。"""
        import os
        # 首先检查 OS 环境变量
        env_dir = os.environ.get("IRIS_MEETING_TRANS_DIR", "")
        if not env_dir:
            # 回退到 .env 文件
            from iris.config.loader import load_env_file
            env = load_env_file(self._bundle.root / ".env")
            env_dir = env.get("IRIS_MEETING_TRANS_DIR", "")
        if env_dir:
            p = Path(env_dir).expanduser().resolve()
            if p.exists():
                return p
        return None

    def _transcribe(self, audio: Path, transcript_path: Path, model_name: str) -> int:
        import whisper
        try:
            import torch
            device = "mps" if torch.backends.mps.is_available() else "cpu"
        except ImportError:
            logger.debug("torch 未安装，使用 CPU 设备")
            device = "cpu"
        model = whisper.load_model(model_name, device=device)
        result = model.transcribe(str(audio), language="zh")
        text = result.get("text", "").strip()
        word_count = len(text)
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(text, encoding="utf-8")
        return word_count

    def _parse_filename(self, stem: str, date_part: str) -> tuple[str, str]:
        if not date_part:
            return "会议", stem
        rest = stem[len(date_part):]
        parts = rest.strip("-").split("-", 1) if rest else []
        if len(parts) == 2:
            return parts[0].strip(), parts[1].strip()
        elif len(parts) == 1:
            return parts[0].strip(), ""
        return "会议", ""

    def _load_wiki_context(self) -> tuple[str, int]:
        """从新 LLM-WIKI 结构加载页面上下文。"""
        from iris.wiki.context_loader import WikiContextLoader
        if not self._wiki_root.exists():
            return "# Wiki 上下文\n\n（Wiki 目录不存在）", 0
        loader = WikiContextLoader(self._wiki_root)
        pages = loader.load_pages()
        ctx = loader.load_context(max_chars_per_page=3000)
        return ctx, len(pages)

    def _call_llm(self, raw_transcript: str, wiki_context: str,
                  meeting_type: str = "", meeting_topic: str = "",
                  source_filename: str = "", meeting_date: str = "",
                  duration: str = "", model: Optional[str] = None) -> str:
        gen_date = time.strftime("%Y-%m-%d")
        meeting_date_display = meeting_date or gen_date  # fallback：无文件名日期时用当天
        type_label = meeting_type or "会议"
        topic_label = meeting_topic or ""
        title = f"会议纪要 - {topic_label}" if topic_label else f"会议纪要 - {type_label}"
        header_lines = [f"# {title}",
                        f"日期：{meeting_date_display}",
                        f"类型：{type_label}"]
        if duration:
            header_lines.append(f"时长：{duration}")
        if source_filename:
            header_lines.append(f"来源：{source_filename}")
        header = "\n".join(header_lines)
        prompt = f"""{SYSTEM_PROMPT}

以下是一次{type_label}的语音转写文本（Whisper ASR 结果，包含同音/近音误识别）。

## 背景知识（Wiki 上下文）

请使用以下 Wiki 知识校正人名、术语和项目名称：

{wiki_context}

## 任务

1. **ASR 校正**：基于 Wiki 知识校正人名和术语
2. **信息提取**：从校正后的内容中提取 5-7 个主题的会议纪要
3. **结构化输出**：按议题分组，保留关键数据指标、决策、风险/问题
4. **待办事项**：输出清晰的责任人+截止时间

## 输出格式（必须严格遵守）

{header}

## 参会人员
（根据上下文列出）

### 1. [主题标题]
**概述：**
**关键数据/进展：**
**决策/结论：**
**风险/问题：**

## 待办事项
| 序号 | 事项 | 负责人 | 截止时间 |

---
*生成说明：基于 {meeting_date_display} 录音转写生成*
*记录人：Iris，纪要日期：{gen_date}*

## 原始转写文本

{raw_transcript}"""
        try:
            minutes = self._llm.generate(prompt, route_context={"input_type": "text"},
                                         temperature=0.1, max_tokens=16384,
                                         force_model=model).text
        except LLMProviderError as exc:
            import logging as _logging
            _logging.getLogger(__name__).error("纪要生成 LLM 调用失败: %s", exc)
            return (
                f"<!-- LLM 生成失败: {exc} -->\n\n"
                f"{header}\n\n"
                "（LLM 不可用，原始转写见下）\n\n"
                f"## 原始转写文本\n\n{raw_transcript}"
            )
        # 后处理：确保尾注存在
        minutes = self._ensure_footer(minutes, meeting_date_display, gen_date)
        return minutes

    @staticmethod
    def _ensure_footer(text: str, meeting_date: str, gen_date: str) -> str:
        """确保纪要末尾包含生成说明尾注，缺少则自动追加。"""
        if "生成说明" in text:
            return text
        footer = (f"\n\n---\n"
                   f"*生成说明：基于 {meeting_date} 录音转写生成*\n"
                   f"*记录人：Iris，纪要日期：{gen_date}*")
        return text.rstrip() + footer

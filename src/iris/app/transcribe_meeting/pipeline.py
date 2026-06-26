"""录音转会议纪要流水线 — 适配新 LLM-WIKI 结构。"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from iris.config.loader import ConfigBundle
from iris.llm import EnvironmentConfiguredLLMProvider, LLMRequest

SYSTEM_PROMPT = "你是一个专业的会议纪要提取专家，擅长从语音转写文本中提取结构化会议纪要。你会仔细校正 ASR 误识别，准确提取信息。注意：直接输出会议纪要正文，不要输出任何前缀说明、开场白或打招呼内容。"


class TranscribeMeetingPipeline:
    def __init__(self, bundle: ConfigBundle) -> None:
        self._bundle = bundle
        self._provider = EnvironmentConfiguredLLMProvider(bundle)
        self._wiki_root = Path(bundle.wiki["wiki_root"]).resolve() if bundle.wiki else Path()
        self._temp_dir = bundle.root / bundle.app["paths"]["temp_dir"]

    def run(self, audio_path: str = "", *, transcript_path: Optional[str] = None,
            output_path: Optional[str] = None, whisper_model: str = "base",
            force_retranscribe: bool = False) -> Dict[str, Any]:
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
        date = date_part if date_part else time.strftime("%Y%m%d")
        meeting_type, meeting_topic = self._parse_filename(stem, date_part)
        print(f"[0/3] 识别会议类型={meeting_type}, 主题={meeting_topic}", file=sys.stderr)

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

        # Step 2: Wiki 上下文（适配新结构）
        print(f"[2/3] 检索 Wiki 上下文...", file=sys.stderr)
        wiki_context, page_count = self._load_wiki_context()
        print(f"     完成：加载 {page_count} 个 Wiki 页面", file=sys.stderr)

        # Step 3: LLM 生成会议纪要
        print(f"[3/3] base_model 生成会议纪要...", file=sys.stderr)
        minutes = self._call_llm(raw_transcript, wiki_context, meeting_type, meeting_topic)

        if output_path:
            out = Path(output_path).resolve()
        else:
            out = self._temp_dir / f"{stem}.md"
        from iris.core.write_guard import safe_write_text
        safe_write_text(out, minutes, self._bundle, allow_existing_outside=True)
        print(f"     完成 → {out.name}", file=sys.stderr)

        return {"audio_file": str(source) if has_audio else "", "transcript_file": str(source) if has_text else "",
                "source_type": source_type, "word_count": word_count, "wiki_pages_loaded": page_count,
                "output_file": str(out), "model": self._provider.get_active_model_config("base_model")["model"]}

    def _resolve_source_dir(self) -> Path:
        """解析 SOURCE/05-会议纪要/ 输出目录。"""
        data_source = self._bundle.data_source
        sources = data_source.get("sources", {})
        for cfg in sources.values():
            if cfg.get("enabled") and cfg.get("path"):
                src_root = Path(cfg["path"]).resolve()
                if src_root.exists():
                    meeting_dir = src_root / "05-会议纪要"
                    return meeting_dir
        return self._temp_dir

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
        model = whisper.load_model(model_name)
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
        fragments: List[str] = []
        count = 0
        if not self._wiki_root.exists():
            return "# Wiki 上下文\n\n（Wiki 目录不存在）", 0

        # 加载 01-领域 页面
        domain_dir = self._wiki_root / "01-领域"
        if domain_dir.exists():
            for f in sorted(domain_dir.iterdir()):
                if f.name.endswith(".md"):
                    content = self._read_wiki_page(f)
                    if content:
                        name = f.stem.replace("领域-", "")
                        fragments.append(f"## 领域：{name}\n{content}")
                        count += 1

        # 加载 02-概念 页面（含 ASR 校正表）
        concept_dir = self._wiki_root / "02-概念"
        if concept_dir.exists():
            for f in sorted(concept_dir.iterdir()):
                if f.name.endswith(".md"):
                    content = self._read_wiki_page(f)
                    if content:
                        name = f.stem.replace("概念-", "")
                        fragments.append(f"## 概念：{name}\n{content}")
                        count += 1

        # 加载 03-项目 页面
        proj_dir = self._wiki_root / "03-项目"
        if proj_dir.exists():
            for f in sorted(proj_dir.iterdir()):
                if f.name.endswith(".md"):
                    content = self._read_wiki_page(f)
                    if content:
                        name = f.stem.replace("项目-", "")
                        fragments.append(f"## 项目：{name}\n{content}")
                        count += 1

        # 加载 04-人物 页面
        person_dir = self._wiki_root / "04-人物"
        if person_dir.exists():
            for f in sorted(person_dir.iterdir()):
                if f.name.endswith(".md"):
                    content = self._read_wiki_page(f)
                    if content:
                        name = f.stem.replace("人物-", "")
                        fragments.append(f"## 人物：{name}\n{content}")
                        count += 1

        return "\n\n".join(fragments), count

    @staticmethod
    def _read_wiki_page(path: Path) -> str:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                text = parts[2].strip()
        max_chars = 3000
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n...（截断）"
        return text

    def _call_llm(self, raw_transcript: str, wiki_context: str,
                  meeting_type: str = "", meeting_topic: str = "") -> str:
        date_str = time.strftime("%Y-%m-%d")
        type_label = meeting_type or "会议"
        topic_label = meeting_topic or ""
        title = f"会议纪要 - {topic_label}" if topic_label else f"会议纪要 - {type_label}"
        header = f"# {title}\n日期：{date_str}\n类型：{type_label}" if topic_label else f"# {title}\n日期：{date_str}"
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

## 输出格式

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
*生成说明：基于 {date_str} 录音转写生成*
*记录人：Iris，纪要日期：{date_str}*

## 原始转写文本

{raw_transcript}"""
        response = self._provider.generate(LLMRequest(prompt=prompt, route_context={"input_type": "text"}), temperature=0.1, max_tokens=16384)
        return response.text

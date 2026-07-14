"""视频文件适配器 — 抽取关键帧 + 音轨转写，供多模态流水线使用。

设计（对齐 pdf_adapter / docx_adapter 的适配器约定）：
  - 关键帧采样：调用 ffmpeg 均匀抽取 N 帧 → base64 → EncodedImage，供 adv_model 视觉理解
  - 音轨转写：ffmpeg 抽音轨 → Whisper 转写为文字（复用 transcribe_meeting 的设备探测逻辑）
  - 优雅降级：
      * ffmpeg 缺失 → 抛 VideoAdapterError（调用方可降级为"暂不支持"）
      * whisper 缺失 / 无音轨 → transcript 为空 + 记 error，仍返回已抽取的帧
  - 临时文件统一放入 tempfile 目录，处理结束后清理

用法:
    adapter = VideoAdapter()
    content = adapter.process(video_path, max_frames=6)
    # content.frames     → 采样帧（EncodedImage 列表）
    # content.transcript → 音轨转写文本（可能为空）
"""

from __future__ import annotations

import base64
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from iris.complex_input.detector import EncodedImage

logger = logging.getLogger(__name__)

# 默认最大采样帧数（避免 token 爆炸）
_DEFAULT_MAX_FRAMES = 6
# 转写文本最大字符数（超长截断以避免超出上下文窗口）
_DEFAULT_MAX_TRANSCRIPT_CHARS = 6000
# Whisper 默认模型（与 transcribe_meeting 保持一致的轻量级默认）
_DEFAULT_WHISPER_MODEL = "small"
# 单帧渲染宽度（像素），控制图片体积
_FRAME_WIDTH = 768


class VideoAdapterError(RuntimeError):
    """视频适配器相关错误（ffmpeg 缺失 / 文件无法打开等致命错误）。"""


@dataclass
class VideoContent:
    """单个视频文件的处理结果。"""

    path: str
    transcript: str = ""                              # 音轨转写文本（可能为空）
    frames: List[EncodedImage] = field(default_factory=list)  # 均匀采样的关键帧
    duration_sec: float = 0.0
    frame_count: int = 0
    has_audio: bool = False
    error: Optional[str] = None                       # 非致命错误（如转写失败）


class VideoAdapter:
    """视频文件处理器：抽帧 + 音轨转写。"""

    def __init__(self):
        self._check_dependency()

    @staticmethod
    def _check_dependency() -> None:
        """检查 ffmpeg / ffprobe 是否可用（致命依赖）。"""
        if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
            raise VideoAdapterError(
                "ffmpeg / ffprobe 未安装，无法处理视频。请先安装 ffmpeg（macOS: brew install ffmpeg）"
            )

    # ── 公开 API ──────────────────────────────────────────────

    def process(
        self,
        video_path: str | Path,
        *,
        max_frames: int = _DEFAULT_MAX_FRAMES,
        max_transcript_chars: int = _DEFAULT_MAX_TRANSCRIPT_CHARS,
        whisper_model: str = _DEFAULT_WHISPER_MODEL,
    ) -> VideoContent:
        """处理视频：均匀采样关键帧 + 转写音轨。

        Args:
            video_path: 视频文件路径
            max_frames: 最大采样帧数
            max_transcript_chars: 转写文本最大字符数
            whisper_model: Whisper 模型名（缺失依赖时静默降级）

        Returns:
            VideoContent 包含采样帧、转写文本与元信息
        """
        path = Path(video_path).resolve()
        if not path.exists():
            raise VideoAdapterError(f"视频文件不存在: {path}")

        duration = self._probe_duration(path)
        has_audio = self._probe_has_audio(path)

        errors: List[str] = []
        with tempfile.TemporaryDirectory(prefix="iris_video_") as tmp_dir:
            tmp = Path(tmp_dir)

            # 1. 采样关键帧
            frames: List[EncodedImage] = []
            try:
                frames = self._extract_frames(path, tmp, max_frames=max_frames, duration=duration)
            except Exception as exc:  # noqa: BLE001 - 抽帧失败不应中断转写
                errors.append(f"抽帧失败: {exc}")
                logger.warning("视频抽帧失败 %s: %s", path.name, exc)

            # 2. 音轨转写（可选，依赖缺失时降级）
            transcript = ""
            if has_audio:
                try:
                    transcript = self._transcribe_audio(
                        path, tmp, whisper_model=whisper_model, max_chars=max_transcript_chars
                    )
                except Exception as exc:  # noqa: BLE001 - 转写失败仍返回帧
                    errors.append(f"转写失败: {exc}")
                    logger.warning("视频转写失败 %s: %s", path.name, exc)

        return VideoContent(
            path=str(path),
            transcript=transcript,
            frames=frames,
            duration_sec=duration,
            frame_count=len(frames),
            has_audio=has_audio,
            error="; ".join(errors) if errors else None,
        )

    # ── ffprobe 探测 ──────────────────────────────────────────

    def _probe_duration(self, path: Path) -> float:
        """用 ffprobe 获取视频时长（秒），失败返回 0。"""
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, timeout=30,
            )
            return float(out.stdout.strip()) if out.stdout.strip() else 0.0
        except Exception:
            return 0.0

    def _probe_has_audio(self, path: Path) -> bool:
        """用 ffprobe 判断视频是否含音轨。"""
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a",
                 "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
                capture_output=True, text=True, timeout=30,
            )
            return bool(out.stdout.strip())
        except Exception:
            return False

    # ── 抽帧 ──────────────────────────────────────────────────

    def _extract_frames(
        self, path: Path, tmp: Path, *, max_frames: int, duration: float
    ) -> List[EncodedImage]:
        """均匀采样 max_frames 帧并编码为 EncodedImage。"""
        if max_frames <= 0:
            return []

        # 时长可用时按均匀时间点截帧；否则回退到 fps 抽帧
        timestamps: List[float] = []
        if duration > 0:
            # 避开首尾，取 [0.5, ..., n-0.5] / n 的时间点
            timestamps = [duration * (i + 0.5) / max_frames for i in range(max_frames)]

        frames: List[EncodedImage] = []
        if timestamps:
            for idx, ts in enumerate(timestamps):
                out_file = tmp / f"frame_{idx:03d}.jpg"
                cmd = [
                    "ffmpeg", "-v", "error", "-ss", f"{ts:.3f}", "-i", str(path),
                    "-frames:v", "1", "-vf", f"scale={_FRAME_WIDTH}:-1", "-y", str(out_file),
                ]
                subprocess.run(cmd, capture_output=True, timeout=60)
                enc = self._encode_frame(out_file)
                if enc:
                    frames.append(enc)
        else:
            # 无时长信息：用单条 ffmpeg 命令抽取，交由 fps 过滤器均匀取帧
            pattern = tmp / "frame_%03d.jpg"
            cmd = [
                "ffmpeg", "-v", "error", "-i", str(path),
                "-vf", f"fps=1,scale={_FRAME_WIDTH}:-1", "-frames:v", str(max_frames),
                "-y", str(pattern),
            ]
            subprocess.run(cmd, capture_output=True, timeout=120)
            for out_file in sorted(tmp.glob("frame_*.jpg"))[:max_frames]:
                enc = self._encode_frame(out_file)
                if enc:
                    frames.append(enc)

        return frames

    @staticmethod
    def _encode_frame(frame_path: Path) -> Optional[EncodedImage]:
        """将帧图片文件编码为 base64 data URL。"""
        if not frame_path.exists() or frame_path.stat().st_size == 0:
            return None
        try:
            data = frame_path.read_bytes()
            b64 = base64.b64encode(data).decode("ascii")
            return EncodedImage(
                path=str(frame_path),
                mime_type="image/jpeg",
                data_url=f"data:image/jpeg;base64,{b64}",
            )
        except Exception:
            return None

    # ── 音轨转写 ──────────────────────────────────────────────

    def _transcribe_audio(
        self, path: Path, tmp: Path, *, whisper_model: str, max_chars: int
    ) -> str:
        """抽取音轨 → Whisper 转写。whisper 未安装时抛异常（由调用方捕获降级）。"""
        import whisper  # 延迟导入：缺失时抛 ImportError → 上层记为非致命 error

        # 1. ffmpeg 抽取 16k 单声道 wav（Whisper 首选格式）
        audio_file = tmp / "audio.wav"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-vn",
             "-ac", "1", "-ar", "16000", "-y", str(audio_file)],
            capture_output=True, timeout=300,
        )
        if not audio_file.exists() or audio_file.stat().st_size == 0:
            raise VideoAdapterError("音轨抽取失败（ffmpeg 未产出音频）")

        # 2. 设备探测（复用 transcribe_meeting 的 mps/cpu 逻辑）
        try:
            import torch
            device = "mps" if torch.backends.mps.is_available() else "cpu"
        except Exception:
            device = "cpu"

        model = whisper.load_model(whisper_model, device=device)
        result = model.transcribe(str(audio_file), language="zh")
        text = (result.get("text") or "").strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n...（转写文字已截断）"
        return text

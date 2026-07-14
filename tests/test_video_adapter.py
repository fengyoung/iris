"""complex_input/video_adapter.py 测试。

全程 mock ffmpeg（shutil.which / subprocess）与 whisper，不依赖真实二进制。
覆盖：依赖检查、抽帧、音轨转写降级、error 传播。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iris.complex_input.video_adapter import (
    VideoAdapter,
    VideoAdapterError,
    VideoContent,
)


# ── 依赖检查 ────────────────────────────────────────────────

class TestDependencyCheck:
    def test_missing_ffmpeg_raises(self):
        with patch("iris.complex_input.video_adapter.shutil.which", return_value=None):
            with pytest.raises(VideoAdapterError, match="ffmpeg"):
                VideoAdapter()

    def test_present_ffmpeg_ok(self):
        with patch("iris.complex_input.video_adapter.shutil.which", return_value="/usr/bin/ffmpeg"):
            adapter = VideoAdapter()
            assert isinstance(adapter, VideoAdapter)


# ── process：文件不存在 ─────────────────────────────────────

class TestProcessMissingFile:
    def test_missing_file_raises(self):
        with patch("iris.complex_input.video_adapter.shutil.which", return_value="/usr/bin/ffmpeg"):
            adapter = VideoAdapter()
            with pytest.raises(VideoAdapterError, match="不存在"):
                adapter.process("/nonexistent/clip.mp4")


# ── process：抽帧 + 转写（全 mock） ──────────────────────────

def _make_adapter():
    with patch("iris.complex_input.video_adapter.shutil.which", return_value="/usr/bin/ffmpeg"):
        return VideoAdapter()


class TestProcessFramesAndTranscript:
    def _fake_video(self, tmp_path) -> Path:
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"fake video bytes")
        return f

    def test_frames_extracted_and_transcript(self, tmp_path):
        adapter = _make_adapter()
        video = self._fake_video(tmp_path)

        # mock 探测：时长 12s，含音轨
        with patch.object(adapter, "_probe_duration", return_value=12.0), \
             patch.object(adapter, "_probe_has_audio", return_value=True), \
             patch.object(adapter, "_extract_frames") as mock_frames, \
             patch.object(adapter, "_transcribe_audio", return_value="转写文本"):
            from iris.complex_input.detector import EncodedImage
            mock_frames.return_value = [
                EncodedImage(path="f0.jpg", mime_type="image/jpeg", data_url="data:image/jpeg;base64,a")
            ]
            content = adapter.process(video, max_frames=3)

        assert isinstance(content, VideoContent)
        assert content.frame_count == 1
        assert content.transcript == "转写文本"
        assert content.has_audio is True
        assert content.duration_sec == 12.0
        assert content.error is None

    def test_no_audio_skips_transcription(self, tmp_path):
        adapter = _make_adapter()
        video = self._fake_video(tmp_path)
        with patch.object(adapter, "_probe_duration", return_value=5.0), \
             patch.object(adapter, "_probe_has_audio", return_value=False), \
             patch.object(adapter, "_extract_frames", return_value=[]), \
             patch.object(adapter, "_transcribe_audio") as mock_trans:
            content = adapter.process(video)
        mock_trans.assert_not_called()
        assert content.transcript == ""
        assert content.has_audio is False

    def test_transcription_failure_recorded_but_frames_kept(self, tmp_path):
        adapter = _make_adapter()
        video = self._fake_video(tmp_path)
        from iris.complex_input.detector import EncodedImage
        with patch.object(adapter, "_probe_duration", return_value=8.0), \
             patch.object(adapter, "_probe_has_audio", return_value=True), \
             patch.object(adapter, "_extract_frames", return_value=[
                 EncodedImage(path="f0.jpg", mime_type="image/jpeg", data_url="data:image/jpeg;base64,a")]), \
             patch.object(adapter, "_transcribe_audio", side_effect=ImportError("no whisper")):
            content = adapter.process(video)
        assert content.frame_count == 1
        assert content.transcript == ""
        assert content.error is not None and "转写失败" in content.error

    def test_frame_extraction_failure_recorded(self, tmp_path):
        adapter = _make_adapter()
        video = self._fake_video(tmp_path)
        with patch.object(adapter, "_probe_duration", return_value=8.0), \
             patch.object(adapter, "_probe_has_audio", return_value=False), \
             patch.object(adapter, "_extract_frames", side_effect=RuntimeError("ffmpeg crashed")):
            content = adapter.process(video)
        assert content.frame_count == 0
        assert content.error is not None and "抽帧失败" in content.error


# ── ffprobe 探测（mock subprocess） ─────────────────────────

class TestProbe:
    def test_probe_duration_parses_float(self, tmp_path):
        adapter = _make_adapter()
        fake = MagicMock(stdout="12.5\n")
        with patch("iris.complex_input.video_adapter.subprocess.run", return_value=fake):
            assert adapter._probe_duration(Path("/x.mp4")) == 12.5

    def test_probe_duration_handles_error(self, tmp_path):
        adapter = _make_adapter()
        with patch("iris.complex_input.video_adapter.subprocess.run", side_effect=OSError("boom")):
            assert adapter._probe_duration(Path("/x.mp4")) == 0.0

    def test_probe_has_audio_true(self):
        adapter = _make_adapter()
        fake = MagicMock(stdout="0\n")
        with patch("iris.complex_input.video_adapter.subprocess.run", return_value=fake):
            assert adapter._probe_has_audio(Path("/x.mp4")) is True

    def test_probe_has_audio_false_when_empty(self):
        adapter = _make_adapter()
        fake = MagicMock(stdout="")
        with patch("iris.complex_input.video_adapter.subprocess.run", return_value=fake):
            assert adapter._probe_has_audio(Path("/x.mp4")) is False


# ── _encode_frame ───────────────────────────────────────────

class TestEncodeFrame:
    def test_encodes_existing_file(self, tmp_path):
        f = tmp_path / "frame.jpg"
        f.write_bytes(b"\xff\xd8\xff\xe0jpegdata")
        enc = VideoAdapter._encode_frame(f)
        assert enc is not None
        assert enc.mime_type == "image/jpeg"
        assert enc.data_url.startswith("data:image/jpeg;base64,")

    def test_returns_none_for_missing(self, tmp_path):
        assert VideoAdapter._encode_frame(tmp_path / "nope.jpg") is None

    def test_returns_none_for_empty(self, tmp_path):
        f = tmp_path / "empty.jpg"
        f.write_bytes(b"")
        assert VideoAdapter._encode_frame(f) is None


# ── _extract_frames（mock subprocess + 落盘） ────────────────

class TestExtractFrames:
    def test_uniform_sampling_by_timestamp(self, tmp_path):
        adapter = _make_adapter()
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")

        # 让每次 ffmpeg 调用"生成"一个帧文件
        created = {"n": 0}

        def fake_run(cmd, **kwargs):
            # cmd 中最后一个参数是输出路径
            out = Path(cmd[-1])
            out.write_bytes(b"\xff\xd8jpeg")
            created["n"] += 1
            return MagicMock()

        with patch("iris.complex_input.video_adapter.subprocess.run", side_effect=fake_run):
            frames = adapter._extract_frames(video, tmp_path, max_frames=3, duration=9.0)

        assert created["n"] == 3
        assert len(frames) == 3

    def test_zero_frames_returns_empty(self, tmp_path):
        adapter = _make_adapter()
        frames = adapter._extract_frames(tmp_path / "c.mp4", tmp_path, max_frames=0, duration=10.0)
        assert frames == []


# ── _transcribe_audio（mock whisper） ───────────────────────

class TestTranscribeAudio:
    def test_uses_whisper_and_truncates(self, tmp_path, monkeypatch):
        adapter = _make_adapter()
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")

        # 伪造 ffmpeg 生成 audio.wav
        def fake_run(cmd, **kwargs):
            # 找到输出 wav 路径（cmd 中以 .wav 结尾的项）
            for part in cmd:
                if str(part).endswith("audio.wav"):
                    Path(part).write_bytes(b"RIFFfakeaudio")
            return MagicMock()

        # 伪造 whisper 模块
        fake_whisper = types.ModuleType("whisper")
        fake_model = MagicMock()
        fake_model.transcribe.return_value = {"text": "很长的转写" * 100}
        fake_whisper.load_model = MagicMock(return_value=fake_model)
        monkeypatch.setitem(sys.modules, "whisper", fake_whisper)

        with patch("iris.complex_input.video_adapter.subprocess.run", side_effect=fake_run):
            text = adapter._transcribe_audio(video, tmp_path, whisper_model="tiny", max_chars=50)

        assert "截断" in text
        assert len(text) <= 50 + 20  # 截断后加省略提示

    def test_raises_when_audio_not_produced(self, tmp_path, monkeypatch):
        adapter = _make_adapter()
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")

        fake_whisper = types.ModuleType("whisper")
        fake_whisper.load_model = MagicMock()
        monkeypatch.setitem(sys.modules, "whisper", fake_whisper)

        # ffmpeg 不产出 wav
        with patch("iris.complex_input.video_adapter.subprocess.run", return_value=MagicMock()):
            with pytest.raises(VideoAdapterError, match="音轨抽取失败"):
                adapter._transcribe_audio(video, tmp_path, whisper_model="tiny", max_chars=100)

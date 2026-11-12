"""Writeback safety regressions; all clipboard and keyboard effects are mocked."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
import subprocess

import pytest

from iris.wiki.asr import _clipboard_io as clipboard
from iris.wiki.asr import _keyboard_writer as writer
from iris.wiki.asr.corrector import AsrCorrector


@pytest.mark.parametrize("failure", [
    subprocess.CompletedProcess("pbcopy", 1),
    OSError("pbcopy unavailable"),
    subprocess.TimeoutExpired("pbcopy", 3),
    subprocess.CalledProcessError(1, "pbcopy"),
])
def test_pbcopy_failure_never_deletes(failure):
    kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
    with patch.object(clipboard.subprocess, "run", **kwargs), \
         patch.object(clipboard, "_read_clipboard", return_value="raw"), \
         patch.object(clipboard, "_delete_and_paste") as delete, \
         patch.object(clipboard.time, "sleep"):
        assert clipboard._replace_text_in_place("corrected", "raw") is False
    delete.assert_not_called()


@pytest.mark.parametrize("mode", ["fast", "full"])
@pytest.mark.parametrize("raises", [False, True])
def test_failure_latches_across_fast_and_llm_paths(mode, raises):
    with patch("iris.wiki.asr.corrector._load_vocotype_hotkey", return_value=(0, 0)):
        engine = AsrCorrector({"raw": "corrected"}, mode=mode)
    engine._last_corrected = "previous"
    try:
        with patch("iris.wiki.asr.corrector._replace_text_in_place",
                   side_effect=OSError("partial") if raises else None,
                   return_value=False) as replace, \
             patch("iris.wiki.asr.corrector._log") as log:
            if mode == "fast":
                engine._apply_dict("raw")
            else:
                engine._write_back_llm("corrected", "raw")
            assert engine._last_corrected == "previous"
            engine._write_back_llm("later", "raw")
            engine._mode = "fast"
            engine._apply_dict("raw")
            replace.assert_called_once_with("corrected", "raw")
            warnings = " ".join(str(call.args[0]) for call in log.call_args_list)
            assert "自动写回已禁用" in warnings
            assert "手动检查" in warnings
            assert "显式重启" in warnings
            assert "保留原始转写" not in warnings
    finally:
        engine._llm_executor.shutdown(wait=True)


def test_partial_native_timeout_disables_later_deletion():
    cg, cf = Mock(), Mock()
    cg.CGEventCreateKeyboardEvent.side_effect = [11, 12, 13, 14]
    with patch("iris.wiki.asr.corrector._load_vocotype_hotkey", return_value=(0, 0)):
        engine = AsrCorrector({}, mode="full")
    try:
        with patch.object(writer, "_CG", cg), patch.object(writer, "_CF", cf), \
             patch.object(writer.time, "monotonic", side_effect=[0, 0, 0, 1.1, 1.1]), \
             patch("iris.wiki.asr.corrector._replace_text_in_place",
                   side_effect=lambda corrected, raw: writer._delete_and_paste(len(raw))) as replace:
            engine._write_back_llm("corrected", "raw")
            engine._write_back_llm("corrected", "raw")
        replace.assert_called_once()
        assert [call.args[1] for call in cg.CGEventPost.call_args_list] == [11, 12]
        assert engine._writeback_disabled
        assert engine._last_corrected == ""
    finally:
        engine._llm_executor.shutdown(wait=True)


def test_success_keeps_writeback_enabled_and_new_engine_resets_latch():
    with patch("iris.wiki.asr.corrector._load_vocotype_hotkey", return_value=(0, 0)):
        engine = AsrCorrector({})
        restarted = AsrCorrector({})
    try:
        with patch("iris.wiki.asr.corrector._replace_text_in_place",
                   side_effect=[True, False, True]) as replace:
            assert engine._write_back("first", "raw")
            assert engine._last_corrected == "first"
            assert not engine._writeback_disabled
            assert not engine._write_back("second", "raw")
            assert engine._last_corrected == "first"
            assert not engine._write_back("third", "raw")
            assert restarted._write_back("third", "raw")
            assert replace.call_count == 3
    finally:
        engine._llm_executor.shutdown(wait=True)
        restarted._llm_executor.shutdown(wait=True)


def test_concurrent_failures_only_attempt_one_writeback():
    with patch("iris.wiki.asr.corrector._load_vocotype_hotkey", return_value=(0, 0)):
        engine = AsrCorrector({})
    try:
        with patch("iris.wiki.asr.corrector._replace_text_in_place", return_value=False) as replace:
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: engine._write_back("corrected", "raw"), range(8)))
        assert results == [False] * 8
        replace.assert_called_once()
    finally:
        engine._llm_executor.shutdown(wait=True)

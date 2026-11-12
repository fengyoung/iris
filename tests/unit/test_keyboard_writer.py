"""原生退格顺序、预算和资源释放测试，不发送真实键盘事件。"""
from unittest.mock import Mock, patch

import pytest

from iris.wiki.asr import _keyboard_writer as writer


@pytest.mark.parametrize("count", [1, 20, 140, 500, 2000])
def test_exact_deletions_then_paste_within_budget(count):
    cg, cf = Mock(), Mock()
    cg.CGEventCreateKeyboardEvent.side_effect = [11, 12, 13, 14]
    clock = [0.0]
    posted = []

    def post(tap, event):
        posted.append(event)
        clock[0] += 0.00001

    def sleep(seconds):
        clock[0] += seconds

    cg.CGEventPost.side_effect = post
    with patch.object(writer, "_CG", cg), patch.object(writer, "_CF", cf), \
         patch.object(writer.time, "monotonic", side_effect=lambda: clock[0]), \
         patch.object(writer.time, "sleep", side_effect=sleep):
        assert writer._delete_and_paste(count)
    assert posted == [11, 12] * count + [13, 14]
    assert clock[0] < 1.0
    assert cf.CFRelease.call_count == 4


def test_stalled_sender_stops_without_paste_or_retry():
    cg, cf = Mock(), Mock()
    cg.CGEventCreateKeyboardEvent.side_effect = [11, 12, 13, 14]
    with patch.object(writer, "_CG", cg), patch.object(writer, "_CF", cf), \
         patch.object(writer.time, "monotonic", side_effect=[0, 0, 0, 1.1, 1.1]):
        assert not writer._delete_and_paste(500)
    assert [call.args[1] for call in cg.CGEventPost.call_args_list] == [11, 12]
    assert cf.CFRelease.call_count == 4


def test_permission_denied_sends_nothing():
    cg = Mock()
    cg.CGPreflightPostEventAccess.return_value = False
    with patch.object(writer, "_CG", cg), patch.object(writer, "_CF", Mock()):
        assert not writer._delete_and_paste(500)
    cg.CGEventPost.assert_not_called()


def test_allocation_failure_releases_events_without_deleting():
    cg, cf = Mock(), Mock()
    cg.CGEventCreateKeyboardEvent.side_effect = [11, None]
    with patch.object(writer, "_CG", cg), patch.object(writer, "_CF", cf):
        assert not writer._delete_and_paste(500)
    cg.CGEventPost.assert_not_called()
    cf.CFRelease.assert_called_once_with(11)

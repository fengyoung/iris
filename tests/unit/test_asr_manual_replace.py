"""手动替换映射（data/asr_manual_replace.json）— 加载、指纹、合并、部署。

设计要点：
  - ``mis_asr`` 由 LLM 每次重新生成，手改 ``data/asr_replace_dict.json`` 会在下次
    ``--deploy`` 时被整体覆盖；人工确认的人名纠正必须单独存一份并在部署时合并。
  - 手动映射也要进输入指纹：不进指纹就会出现「改完了 daily-start 认为输入没变、
    跳过部署」——用户改文件的意图恰恰是让它生效。
"""

import json
import logging

from iris.app.cli._handlers import _wiki
from iris.wiki.asr.version import (
    MANUAL_REPLACE_FILE,
    compute_input_fingerprint,
    load_manual_replace_map,
)

LOGGER = logging.getLogger("test-asr-manual-replace")


def _write(tmp_path, payload):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / MANUAL_REPLACE_FILE
    if isinstance(payload, str):
        target.write_text(payload, encoding="utf-8")
    else:
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return data_dir


class TestLoadManualReplaceMap:
    def test_reads_flat_shape(self, tmp_path):
        data_dir = _write(tmp_path, {"玉斌": "煜彬", "于斌": "煜彬"})
        assert load_manual_replace_map(data_dir) == {"玉斌": "煜彬", "于斌": "煜彬"}

    def test_reads_replace_map_shape(self, tmp_path):
        """与 data/asr_replace_dict.json 同构，便于直接复制条目。"""
        data_dir = _write(tmp_path, {"replace_map": {"志成": "智诚"}})
        assert load_manual_replace_map(data_dir) == {"志成": "智诚"}

    def test_underscore_keys_are_comments(self, tmp_path):
        data_dir = _write(tmp_path, {"_comment": "说明文字", "玉斌": "煜彬"})
        assert load_manual_replace_map(data_dir) == {"玉斌": "煜彬"}

    def test_missing_file_returns_empty(self, tmp_path):
        assert load_manual_replace_map(tmp_path / "nope") == {}

    def test_corrupt_json_returns_empty(self, tmp_path):
        data_dir = _write(tmp_path, "{不是合法 JSON")
        assert load_manual_replace_map(data_dir) == {}

    def test_non_object_top_level_returns_empty(self, tmp_path):
        data_dir = _write(tmp_path, ["玉斌", "煜彬"])
        assert load_manual_replace_map(data_dir) == {}

    def test_drops_bad_entries(self, tmp_path):
        """非字符串值、空值、误识别与正确写法相同，都不该进映射。"""
        data_dir = _write(tmp_path, {
            "好的": "正确",
            "数组值": ["不该保留"],
            "对象值": {"a": 1},
            "空串": "",
            "自身相等": "自身相等",
            "空白": "   ",
        })
        assert load_manual_replace_map(data_dir) == {"好的": "正确"}


class TestFingerprintIncludesManualReplace:
    def test_adding_mapping_changes_fingerprint(self, tmp_path):
        before = compute_input_fingerprint([], tmp_path / "data")
        data_dir = _write(tmp_path, {"玉斌": "煜彬"})
        assert compute_input_fingerprint([], data_dir) != before

    def test_key_order_does_not_change_fingerprint(self, tmp_path):
        """调换顺序不该触发一次十几轮 LLM 调用的重建。"""
        a = _write(tmp_path / "a", {"玉斌": "煜彬", "于斌": "煜彬"})
        b = _write(tmp_path / "b", {"于斌": "煜彬", "玉斌": "煜彬"})
        assert compute_input_fingerprint([], a) == compute_input_fingerprint([], b)

    def test_changing_target_changes_fingerprint(self, tmp_path):
        a = _write(tmp_path / "a", {"玉斌": "煜彬"})
        b = _write(tmp_path / "b", {"玉斌": "郭煜彬"})
        assert compute_input_fingerprint([], a) != compute_input_fingerprint([], b)

    def test_missing_file_matches_empty_map(self, tmp_path):
        empty = _write(tmp_path / "a", {"replace_map": {}})
        assert compute_input_fingerprint([], tmp_path / "none") == \
            compute_input_fingerprint([], empty)


class TestMergeManualReplaceMap:
    def _merge(self, tmp_path, manual, generated):
        data_dir = _write(tmp_path, manual)
        deployed = []
        merged = _wiki._merge_manual_replace_map(
            generated, deployed, LOGGER, data_dir=data_dir)
        return merged, deployed

    def test_adds_manual_only_key(self, tmp_path):
        merged, deployed = self._merge(tmp_path, {"玉斌": "煜彬"}, {"万淘": "万涛"})
        assert merged == {"万淘": "万涛", "玉斌": "煜彬"}
        assert any("手动替换映射 +1" in d for d in deployed)

    def test_manual_overrides_generated(self, tmp_path):
        """人工确认的目标优先于 LLM 生成的目标。"""
        merged, _ = self._merge(tmp_path, {"玉斌": "煜彬"}, {"玉斌": "于滨"})
        assert merged["玉斌"] == "煜彬"

    def test_skips_cross_conflict(self, tmp_path):
        """误识别词恰好是另一个术语的正确写法时跳过，避免改掉真实姓名。

        生成结果里 ``刘杨 → 刘洋``（刘洋是真人），若手动再配 ``刘洋 → 刘扬``，
        替换就会把真实姓名刘洋改掉，必须拦下。
        """
        merged, deployed = self._merge(tmp_path, {"刘洋": "刘扬"}, {"刘杨": "刘洋"})
        assert "刘洋" not in merged
        assert merged == {"刘杨": "刘洋"}
        assert any("跳过 1" in d for d in deployed)

    def test_skips_dangerous_mapping(self, tmp_path):
        """单字高频词会在大面积文本里误伤。"""
        merged, deployed = self._merge(tmp_path, {"是": "是的"}, {"万淘": "万涛"})
        assert "是" not in merged
        assert any("跳过 1" in d for d in deployed)

    def test_empty_manual_returns_copy(self, tmp_path):
        generated = {"万淘": "万涛"}
        merged, deployed = self._merge(tmp_path, {}, generated)
        assert merged == generated
        assert merged is not generated
        assert deployed == []


class TestDeployWritesMergedDict:
    def test_manual_mappings_land_in_data_dict(self, tmp_path, monkeypatch):
        """部署后 data/asr_replace_dict.json 必须是「生成结果 + 手动映射」。"""
        voco = tmp_path / "VocoType"
        voco.mkdir()
        monkeypatch.setenv("IRIS_VOCOTYPE_DIR", str(voco))
        monkeypatch.setattr(_wiki, "_merge_manual_hotwords",
                            lambda h, d, l, **k: list(h))

        data_dir = _write(tmp_path, {"replace_map": {"玉斌": "煜彬", "志成": "智诚"}})
        generated = tmp_path / "asr-replace-dict-x.json"
        generated.write_text(json.dumps({"replace_map": {"万淘": "万涛"}}), encoding="utf-8")

        _wiki._deploy_asr_to_vocotype(
            ["词"], "hw.txt", str(generated), "", "", LOGGER,
            bak_dir=tmp_path / "bak", data_dir=data_dir)

        written = json.loads(
            (data_dir / "asr_replace_dict.json").read_text(encoding="utf-8"))
        assert written["replace_map"] == {
            "万淘": "万涛", "玉斌": "煜彬", "志成": "智诚"}


class TestChannelsAreIndependent:
    def test_manual_replace_does_not_consume_hotword_budget(self, tmp_path):
        """替换映射与热词配额是两条独立通道，加映射不该挤占热词槽位。"""
        data_dir = _write(tmp_path, {"玉斌": "煜彬", "志成": "智诚"})
        assert _wiki._resolve_hotword_budget(data_dir) == (
            _wiki.HOTWORD_LIMIT - _wiki._FEEDBACK_HOTWORD_HEADROOM)

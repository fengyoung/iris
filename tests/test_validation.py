"""测试 iris.utils.validation 安全类型转换与校验工具。"""

import pytest
from iris.utils.validation import (
    ValidationError,
    safe_int,
    safe_float,
    safe_parse_json,
    validate_required_keys,
    safe_get_str,
    safe_get_list,
)


class TestSafeInt:
    def test_valid_integer(self):
        assert safe_int("42", 0) == 42
        assert safe_int(42, 0) == 42

    def test_invalid_returns_default(self):
        assert safe_int("abc", 10) == 10
        assert safe_int(None, 5) == 5

    def test_min_bound(self):
        assert safe_int("-5", 0, min_val=0) == 0
        assert safe_int("10", 0, min_val=5) == 10

    def test_max_bound(self):
        assert safe_int("100", 0, max_val=50) == 50
        assert safe_int("20", 0, max_val=50) == 20

    def test_both_bounds(self):
        assert safe_int("5", 0, min_val=1, max_val=10) == 5
        assert safe_int("0", 0, min_val=1, max_val=10) == 1
        assert safe_int("20", 0, min_val=1, max_val=10) == 10


class TestSafeFloat:
    def test_valid_float(self):
        assert safe_float("3.14", 0.0) == 3.14
        assert safe_float(3.14, 0.0) == 3.14
        assert safe_float("42", 0.0) == 42.0

    def test_invalid_returns_default(self):
        assert safe_float("abc", 1.0) == 1.0
        assert safe_float(None, 0.5) == 0.5

    def test_min_bound(self):
        assert safe_float("-1.0", 0.0, min_val=0.0) == 0.0

    def test_max_bound(self):
        assert safe_float("10.0", 0.0, max_val=5.0) == 5.0


class TestSafeParseJson:
    def test_valid_json_dict(self):
        assert safe_parse_json('{"a": 1}') == {"a": 1}

    def test_valid_json_not_dict_returns_fallback(self):
        assert safe_parse_json("[1, 2, 3]") == {}
        assert safe_parse_json('"hello"') == {}

    def test_invalid_json_returns_fallback(self):
        assert safe_parse_json("{bad}") == {}
        assert safe_parse_json("", fallback={"ok": True}) == {"ok": True}

    def test_custom_fallback(self):
        fb = {"error": True}
        assert safe_parse_json("bad", fallback=fb) == fb

    def test_none_input(self):
        assert safe_parse_json(None, fallback={"a": 1}) == {"a": 1}


class TestValidateRequiredKeys:
    def test_all_present(self):
        validate_required_keys({"a": 1, "b": 2}, ["a", "b"])  # no raise

    def test_missing_key_raises(self):
        with pytest.raises(ValidationError, match="缺少必填字段"):
            validate_required_keys({"a": 1}, ["a", "b"])

    def test_none_value_counts_as_missing(self):
        with pytest.raises(ValidationError):
            validate_required_keys({"a": None}, ["a"])

    def test_custom_label(self):
        with pytest.raises(ValidationError, match="配置"):
            validate_required_keys({}, ["x"], label="配置")


class TestSafeGetStr:
    def test_existing_key(self):
        assert safe_get_str({"name": "Alice"}, "name") == "Alice"

    def test_missing_key_returns_default(self):
        assert safe_get_str({}, "name", "unknown") == "unknown"

    def test_none_value_returns_default(self):
        assert safe_get_str({"name": None}, "name", "N/A") == "N/A"

    def test_strips_whitespace(self):
        assert safe_get_str({"x": "  hello  "}, "x") == "hello"


class TestSafeGetList:
    def test_existing_list(self):
        assert safe_get_list({"items": [1, 2]}, "items") == [1, 2]

    def test_missing_key_returns_empty(self):
        assert safe_get_list({}, "items") == []

    def test_non_list_returns_empty(self):
        assert safe_get_list({"items": "not a list"}, "items") == []

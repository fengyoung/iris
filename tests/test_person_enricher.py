"""测试人物信息丰富器 — wiki/person_enricher.py。"""

from __future__ import annotations

import pytest

from iris.wiki.person_enricher import EnrichResult, EnrichSummary


class TestEnrichResult:
    def test_construct_updated(self):
        r = EnrichResult(name="张三", status="updated", department="技术部", email="a@b.com")
        assert r.name == "张三"
        assert r.status == "updated"
        assert r.department == "技术部"

    def test_construct_not_found(self):
        r = EnrichResult(name="李四", status="not_found", message="通讯录中未找到")
        assert r.status == "not_found"
        assert r.department == ""

    def test_defaults(self):
        r = EnrichResult(name="王五", status="skipped")
        assert r.department == ""
        assert r.email == ""
        assert r.message == ""


class TestEnrichSummary:
    def test_defaults(self):
        s = EnrichSummary()
        assert s.total == 0
        assert s.details == []

    def test_accumulate(self):
        s = EnrichSummary(total=3, updated=2, not_found=1, errors=0)
        assert s.updated == 2
        assert s.not_found == 1
        assert s.total == 3

    def test_details_list(self):
        r1 = EnrichResult(name="A", status="updated")
        r2 = EnrichResult(name="B", status="not_found")
        s = EnrichSummary(total=2, updated=1, not_found=1, details=[r1, r2])
        assert len(s.details) == 2
        assert s.details[0].name == "A"

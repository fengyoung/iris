"""generate_sbom 脚本单元测试。

核心防回归点：SBOM 里 iris 自身的版本必须取自 `pyproject.toml`，而不是已安装
元数据。editable 安装的 `.dist-info` 冻结在安装那一刻，曾导致 v3.39.0 的 SBOM
静默声明成 3.27.0（无任何失败信号）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iris.core.script_loader import load_script_module

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def sbom_mod():
    """加载 scripts/generate_sbom.py。"""
    return load_script_module("generate_sbom.py", _REPO_ROOT)


class _FakeDist:
    """伪造 importlib.metadata 的 Distribution。"""

    def __init__(self, name: str, version: str, license_: str | None = None):
        self.metadata = {"Name": name, "License": license_} if license_ else {"Name": name}
        self.version = version


class TestReadProductVersion:
    """read_product_version 测试。"""

    def test_reads_version_from_pyproject(self, sbom_mod, tmp_path: Path):
        """从 project.version 读出版本号。"""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "iris"\nversion = "9.8.7"\n', encoding="utf-8")

        assert sbom_mod.read_product_version(pyproject) == "9.8.7"

    def test_reads_real_repo_pyproject(self, sbom_mod):
        """真实仓库的 pyproject.toml 可读出非空版本号。"""
        version = sbom_mod.read_product_version(_REPO_ROOT / "pyproject.toml")
        assert version
        assert version[0].isdigit()

    def test_raises_when_version_missing(self, sbom_mod, tmp_path: Path):
        """缺 project.version 时抛 ValueError，不静默兜底。"""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "iris"\n', encoding="utf-8")

        with pytest.raises(ValueError, match="缺少 project.version"):
            sbom_mod.read_product_version(pyproject)

    def test_raises_when_version_empty(self, sbom_mod, tmp_path: Path):
        """空字符串版本号同样抛错。"""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = ""\n', encoding="utf-8")

        with pytest.raises(ValueError, match="缺少 project.version"):
            sbom_mod.read_product_version(pyproject)

    def test_raises_on_missing_file(self, sbom_mod, tmp_path: Path):
        """文件不存在时抛 OSError（由 main 捕获转返回码）。"""
        with pytest.raises(OSError):
            sbom_mod.read_product_version(tmp_path / "nonexistent.toml")


class TestBuildSbomVersionSource:
    """build_sbom 版本来源测试（本文件的核心防回归点）。"""

    @pytest.fixture
    def pyproject(self, tmp_path: Path) -> Path:
        p = tmp_path / "pyproject.toml"
        p.write_text('[project]\nname = "iris"\nversion = "9.8.7"\n', encoding="utf-8")
        return p

    def test_iris_version_from_pyproject_not_stale_metadata(
        self, sbom_mod, monkeypatch, pyproject: Path
    ):
        """已安装元数据与 pyproject 冲突时，iris 版本取 pyproject。

        这是 v3.39.0 实际踩到的 bug：editable 的 .dist-info 停在 3.27.0，
        SBOM 就声明 3.27.0。此处刻意让两者不一致来守卫。
        """
        monkeypatch.setattr(
            sbom_mod.metadata,
            "distributions",
            lambda: [_FakeDist("iris", "3.27.0")],
        )

        sbom = sbom_mod.build_sbom(pyproject)
        iris_pkgs = [p for p in sbom["packages"] if p["name"] == "iris"]

        assert len(iris_pkgs) == 1
        assert iris_pkgs[0]["versionInfo"] == "9.8.7"

    def test_spdxid_and_describes_use_pyproject_version(
        self, sbom_mod, monkeypatch, pyproject: Path
    ):
        """SPDXID 与 documentDescribes 也须用 pyproject 版本，保持内部一致。"""
        monkeypatch.setattr(
            sbom_mod.metadata,
            "distributions",
            lambda: [_FakeDist("iris", "3.27.0")],
        )

        sbom = sbom_mod.build_sbom(pyproject)
        iris_pkg = next(p for p in sbom["packages"] if p["name"] == "iris")

        # 既有 SPDXID 生成是逐字符 "-".join，故 iris-9.8.7 → i-r-i-s---9---8---7。
        # 形态怪但确定且唯一，此处如实固定，避免改版本来源时连带改了 ID 格式。
        assert iris_pkg["SPDXID"] == "SPDXRef-Package-i-r-i-s---9---8---7"
        assert "2-7" not in iris_pkg["SPDXID"]  # 陈旧版本 3.27.0 的痕迹
        assert sbom["documentDescribes"] == [iris_pkg["SPDXID"]]

    def test_third_party_versions_still_from_metadata(
        self, sbom_mod, monkeypatch, pyproject: Path
    ):
        """第三方依赖仍取已安装元数据，不受 pyproject 影响。"""
        monkeypatch.setattr(
            sbom_mod.metadata,
            "distributions",
            lambda: [_FakeDist("iris", "3.27.0"), _FakeDist("requests", "2.34.2", "Apache-2.0")],
        )

        sbom = sbom_mod.build_sbom(pyproject)
        requests_pkg = next(p for p in sbom["packages"] if p["name"] == "requests")

        assert requests_pkg["versionInfo"] == "2.34.2"
        assert requests_pkg["licenseDeclared"] == "Apache-2.0"

    def test_case_insensitive_iris_match(self, sbom_mod, monkeypatch, pyproject: Path):
        """发行名大小写不影响 iris 的识别（Iris / IRIS 同样命中）。"""
        monkeypatch.setattr(
            sbom_mod.metadata,
            "distributions",
            lambda: [_FakeDist("Iris", "3.27.0")],
        )

        sbom = sbom_mod.build_sbom(pyproject)

        assert sbom["packages"][0]["versionInfo"] == "9.8.7"

    def test_sbom_shape_unchanged(self, sbom_mod, monkeypatch, pyproject: Path):
        """SPDX 骨架字段保持不变（防止改版本来源时误动文档结构）。"""
        monkeypatch.setattr(
            sbom_mod.metadata,
            "distributions",
            lambda: [_FakeDist("iris", "3.27.0")],
        )

        sbom = sbom_mod.build_sbom(pyproject)

        assert sbom["spdxVersion"] == "SPDX-2.3"
        assert sbom["dataLicense"] == "CC0-1.0"
        assert sbom["SPDXID"] == "SPDXRef-DOCUMENT"
        assert sbom["creationInfo"]["creators"] == ["Tool: iris.generate_sbom"]

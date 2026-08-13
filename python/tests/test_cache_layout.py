# tests/test_cache_layout.py
#
# Where the toolchain caches itself, and what it cleans up.
#
# From a Windows 11 ARM report: ~/.pymcu/tools held the toolchain twice, under
# win32-arm64 and win32-x86_64, 226 MB each, with identical binaries. The cache
# key named the interpreter's architecture, but Windows and macOS get a single
# upstream build for every architecture -- so two Pythons of different
# architectures on one machine stored the same bytes twice.

import os
from pathlib import Path
from unittest.mock import patch

import pytest

import pymcu_avr_toolchain as tc

KEY = "1!7.3.0.post2"


def _seed(dirpath: Path, version: str = KEY) -> Path:
    """Write a cache that _cache_is_complete() accepts."""
    (dirpath / "bin").mkdir(parents=True, exist_ok=True)
    (dirpath / "bin" / "avr-gcc").write_text("binary")
    (dirpath / ".seeded_from_wheel").write_text(version)
    return dirpath


class TestPayloadKey:
    @pytest.mark.parametrize(("machine", "plat"), [
        ("arm64", "darwin"), ("x86_64", "darwin"),
    ])
    def test_macos_shares_one_key(self, machine, plat):
        with patch("platform.machine", return_value=machine), \
             patch("sys.platform", plat):
            assert tc._payload_key() == "darwin-x86_64"

    @pytest.mark.parametrize("machine", ["ARM64", "AMD64"])
    def test_windows_shares_one_key(self, machine):
        with patch("platform.machine", return_value=machine), \
             patch("sys.platform", "win32"):
            assert tc._payload_key() == "win32-x86_64"

    def test_linux_keeps_its_architecture(self):
        # Linux really does ship per-architecture builds.
        with patch("sys.platform", "linux"):
            with patch("platform.machine", return_value="x86_64"):
                assert tc._payload_key() == "linux-x86_64"
            with patch("platform.machine", return_value="aarch64"):
                assert tc._payload_key() == "linux-arm64"

    def test_the_key_describes_the_bytes_stored(self):
        # On Apple Silicon the binaries are genuinely x86_64 (Rosetta runs
        # them), so the directory name says so instead of claiming arm64.
        with patch("platform.machine", return_value="arm64"), \
             patch("sys.platform", "darwin"):
            assert tc._platform_key() == "darwin-arm64"      # interpreter
            assert tc._payload_key() == "darwin-x86_64"      # contents

    def test_no_legacy_dir_when_the_keys_agree(self):
        with patch("platform.machine", return_value="x86_64"), \
             patch("sys.platform", "linux"):
            assert tc._legacy_cache_dirs() == []


class TestLegacyAdoption:
    def test_a_cache_under_the_old_key_is_moved_not_copied(self, tmp_path):
        with patch.dict(os.environ, {"PYMCU_TOOLS_DIR": str(tmp_path)}), \
             patch("platform.machine", return_value="arm64"), \
             patch("sys.platform", "darwin"):
            legacy = _seed(tmp_path / "darwin-arm64" / "pymcu-avr-toolchain" / KEY)
            new = tmp_path / "darwin-x86_64" / "pymcu-avr-toolchain" / KEY

            adopted = tc._adopt_legacy_cache(
                new, new / "bin", new / ".seeded_from_wheel", KEY
            )

        assert adopted is True
        assert (new / "bin" / "avr-gcc").read_text() == "binary"
        # "Without leaving a second copy" is the point of the exercise.
        assert not legacy.exists()
        assert not (tmp_path / "darwin-arm64").exists()

    def test_an_incomplete_legacy_cache_is_ignored(self, tmp_path):
        with patch.dict(os.environ, {"PYMCU_TOOLS_DIR": str(tmp_path)}), \
             patch("platform.machine", return_value="arm64"), \
             patch("sys.platform", "darwin"):
            # No sentinel: a half-written cache must not be adopted.
            legacy = tmp_path / "darwin-arm64" / "pymcu-avr-toolchain" / KEY
            (legacy / "bin").mkdir(parents=True)
            new = tmp_path / "darwin-x86_64" / "pymcu-avr-toolchain" / KEY

            assert tc._adopt_legacy_cache(
                new, new / "bin", new / ".seeded_from_wheel", KEY
            ) is False

    def test_a_stale_version_under_the_old_key_is_not_adopted(self, tmp_path):
        with patch.dict(os.environ, {"PYMCU_TOOLS_DIR": str(tmp_path)}), \
             patch("platform.machine", return_value="arm64"), \
             patch("sys.platform", "darwin"):
            _seed(tmp_path / "darwin-arm64" / "pymcu-avr-toolchain" / KEY, version="0.0.1")
            new = tmp_path / "darwin-x86_64" / "pymcu-avr-toolchain" / KEY

            assert tc._adopt_legacy_cache(
                new, new / "bin", new / ".seeded_from_wheel", KEY
            ) is False


class TestPruning:
    def _versions(self, root: Path, names: list[str]) -> Path:
        versions_dir = root / "pymcu-avr-toolchain"
        for i, name in enumerate(names):
            _seed(versions_dir / name)
            os.utime(versions_dir / name, (1000 + i * 100, 1000 + i * 100))
        return versions_dir

    def test_keeps_the_two_newest(self, tmp_path):
        # Two, not one: a project pinned to the previous release keeps working
        # after another project pulls a newer one.
        versions = self._versions(tmp_path, ["v1", "v2", "v3", "v4"])
        tc._prune_old_versions(versions)
        assert sorted(p.name for p in versions.iterdir()) == ["v3", "v4"]

    def test_leaves_a_short_history_alone(self, tmp_path):
        versions = self._versions(tmp_path, ["v1", "v2"])
        tc._prune_old_versions(versions)
        assert len(list(versions.iterdir())) == 2

    def test_survives_a_missing_directory(self, tmp_path):
        tc._prune_old_versions(tmp_path / "nope")   # must not raise

    def test_honours_an_explicit_keep_count(self, tmp_path):
        versions = self._versions(tmp_path, ["v1", "v2", "v3"])
        tc._prune_old_versions(versions, keep=1)
        assert [p.name for p in versions.iterdir()] == ["v3"]

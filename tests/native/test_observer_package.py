"""Portable checks for the native observer artifact boundary."""

import importlib
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest


def builder():
    try:
        return importlib.import_module("scripts.build_native_observer")
    except ModuleNotFoundError:
        pytest.fail("native observer package helper is missing")


@pytest.mark.parametrize("profile", ["net48", "net8.0-windows", "net10.0-windows"])
def test_package_contains_only_owned_artifacts_and_manual_load_manifest(tmp_path, profile):
    api = builder()
    output = tmp_path / "output"
    output.mkdir()
    for name in ("ContextObserver.Host.dll", "ContextObserver.Core.dll"):
        (output / name).write_bytes(b"synthetic own assembly")
    if profile != "net48":
        (output / "ContextObserver.Host.deps.json").write_text("{}")
    (output / "AcDbMgd.dll").write_bytes(b"must not redistribute")
    (output / "ContextObserver.Host.pdb").write_bytes(b"must not redistribute")
    archive = api.package(profile, output, tmp_path / "artifacts")
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        assert not any("AcDb" in name or name.endswith(".pdb") for name in names)
        assert len(names) == (4 if profile == "net48" else 5)
        manifest = ElementTree.fromstring(  # noqa: S314 - own generated synthetic XML only
            bundle.read(next(n for n in names if n.endswith(".xml")))
        )
        entry = manifest.find("Components/ComponentEntry")
        assert entry is not None
        for flag in (
            "LoadOnAutoCADStartup",
            "LoadOnAppearance",
            "LoadOnProxy",
            "LoadOnCommandInvocation",
        ):
            assert entry.get(flag) == "False"
        assert manifest.find(".//Commands") is None
        assert manifest.find(".//SystemVariables") is None
        assert manifest.find(".//RegistryEntries") is None
        metadata = json.loads(bundle.read(next(n for n in names if n.endswith("build.json"))))
        assert metadata["profile"] == profile
        assert metadata["sdk"] == "10.0.401"
        assert metadata["host_execution"] == "unverified"
        assert set(metadata["sha256"]) == {
            Path(n).name for n in names if n.endswith((".dll", ".deps.json"))
        }


def test_package_missing_required_owned_assembly_fails_without_archive(tmp_path):
    api = builder()
    with pytest.raises(FileNotFoundError):
        api.package("net48", tmp_path, tmp_path / "artifacts")
    assert not list(tmp_path.glob("artifacts/*.zip"))


def test_packages_are_byte_reproducible_and_sdk_selection_is_pinned(tmp_path):
    api = builder()
    for name in ("ContextObserver.Host.dll", "ContextObserver.Core.dll"):
        (tmp_path / name).write_bytes(b"fixed own DLL")
    first = api.package("net48", tmp_path, tmp_path / "first")
    second = api.package("net48", tmp_path, tmp_path / "second")
    assert first.read_bytes() == second.read_bytes()
    configuration = json.loads((api.ROOT / "global.json").read_text())
    assert configuration["sdk"] == {
        "version": "10.0.401",
        "rollForward": "disable",
        "allowPrerelease": False,
    }

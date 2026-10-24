"""Interactive, opt-in observer checks on the existing leased disposable drawing."""

from __future__ import annotations

import json
import os
from importlib import import_module
from pathlib import Path
from typing import Any

from autocad_mcp.adapter.native_revision import NativeRevisionError, RevisionWitness
from autocad_mcp.adapter.windows_native_revision import WindowsNativeRevisionSource


def require_transition(before: RevisionWitness, after: RevisionWitness, rule: str) -> None:
    if rule == "stable":
        assert before == after, "unchanged COM getters invalidated the witness"
    elif rule == "restart":
        assert before.bridge_id != after.bridge_id, "bridge incarnation did not change"
        assert before.session_id != after.session_id, "process restart reused document lifetime"
    else:
        assert before.bridge_id == after.bridge_id, "bridge changed unexpectedly"
        assert after.epoch > before.epoch, "covered change did not advance the epoch"
        if rule == "reopen":
            assert before.session_id != after.session_id, "reopened lifetime was reused"
        elif rule == "advance":
            assert before.session_id == after.session_id, "same lifetime changed identity"
        else:
            raise ValueError("unknown evidence rule")


def _runtime(pid: int) -> dict[str, Any]:
    import win32api  # type: ignore[import-untyped]
    import win32process  # type: ignore[import-untyped]

    process = win32api.OpenProcess(0x0400 | 0x0010, False, pid)
    try:
        for module in win32process.EnumProcessModules(process):
            path = win32process.GetModuleFileNameEx(process, module)
            if Path(path).name.casefold() in {"clr.dll", "coreclr.dll"}:
                version = win32api.GetFileVersionInfo(path, "\\")
                high, low = version["FileVersionMS"], version["FileVersionLS"]
                return {
                    "module": Path(path).name.casefold(),
                    "version": [high >> 16, high & 65535, low >> 16, low & 65535],
                }
    finally:
        process.Close()
    raise AssertionError("host managed runtime was not observed")


def run(session: Any) -> None:  # noqa: C901 - fixed operator sequence and guarded cleanup
    release = os.environ.get("AUTOCAD_MCP_NATIVE_RELEASE")
    profile = os.environ.get("AUTOCAD_MCP_NATIVE_PROFILE")
    assert release in {str(year) for year in range(2021, 2027)}, "explicit release required"
    assert profile in {"net48", "net8.0-windows", "net10.0-windows"}, "explicit profile required"
    assert os.environ.get("AUTOCAD_MCP_NATIVE_OPERATOR") == "YES", "interactive opt-in required"
    smoke = import_module("tests.windows.test_autocad_2026_smoke")
    _operator_started_autocad = smoke._operator_started_autocad
    _process_executable_for_window = smoke._process_executable_for_window
    source = WindowsNativeRevisionSource()
    guard, lease = session.guard, session.lease
    records: list[dict[str, object]] = []
    opened = False
    outcome = "failed"
    last_count = 0

    def sample() -> tuple[RevisionWitness, int]:
        nonlocal last_count
        lease.assert_owned()
        with _operator_started_autocad() as attached:
            current: Any = attached
            pid, executable = _process_executable_for_window(int(current.HWND))
            assert os.path.normcase(str(executable.resolve())) == os.path.normcase(
                str(session.installation_path.resolve())
            ), "attached process differs from lease"
            caption = str(current.Caption)
            assert (
                release in caption and "autocad lt" not in caption.casefold()
            ), "wrong release/product"
            observed = _runtime(pid)
            expected_major = {"net48": 4, "net8.0-windows": 8, "net10.0-windows": 10}[profile]
            assert (
                observed["version"][0] == expected_major
            ), "selected profile differs from observed runtime"
            document = current.ActiveDocument
            guard.assert_active_full_name(str(document.FullName))
            hwnd = int(document.HWND)
            before = source.witness(hwnd)
            # Reacquire after the first witness; COM proxy identity is not lifetime identity.
            document = current.ActiveDocument
            guard.assert_active_full_name(str(document.FullName))
            assert int(document.HWND) == hwnd, "active document changed during observation"
            for _ in range(10):
                last_count = int(document.ModelSpace.Count)
                _ = document.GetVariable("DBMOD"), document.Name
            after = source.witness(hwnd)
            guard.assert_active_full_name(str(current.ActiveDocument.FullName))
            lease.assert_owned()
            require_transition(before, after, "stable")
            records.append(
                {
                    "event": "sample",
                    "runtime": observed,
                    "release": release,
                    "profile_selected": profile,
                    "getters_stable": True,
                }
            )
            return after, hwnd

    def pause(message: str) -> None:
        assert (
            input(message + " Type DONE to sample: ").strip() == "DONE"
        ), "operator case not observed"

    try:
        lease.assert_owned()
        with _operator_started_autocad() as attached:
            current: Any = attached
            pid, executable = _process_executable_for_window(int(current.HWND))
            assert (
                executable.resolve() == session.installation_path.resolve()
            ), "wrong leased process"
            guard.assert_active_full_name(
                str(current.Documents.Open(str(guard.copy_path), True).FullName)
            )
            opened = True
        previous, hwnd = sample()
        repeated, _ = sample()
        require_transition(previous, repeated, "stable")
        baseline_count = last_count
        for name, prompt in (
            ("change", "Add one entity in memory to the disposable copy; do not save."),
            ("revert", "Undo that addition so entity count matches the initial drawing; do not save."),
            ("undo", "Perform an Undo on this disposable copy."),
            ("redo", "Perform a Redo on this disposable copy."),
            ("modeless_write", "Complete a modeless external write on this disposable copy."),
            ("cancel_close", "Begin closing this modified disposable copy, then cancel close."),
        ):
            pause(prompt)
            after, _ = sample()
            require_transition(previous, after, "advance")
            if name in {"change", "undo"}:
                assert last_count != baseline_count, "expected changed entity count was not observed"
            elif name in {"revert", "redo"}:
                assert last_count == baseline_count, "expected reverted entity count was not observed"
            records.append({"event": name, "epoch_advanced": True, "same_lifetime": True})
            previous = after
        for busy_case, message in (
            ("modal", "Leave a modal command/dialog open while returning to this terminal."),
            ("modeless_lock", "Hold a modeless external write lock on this disposable copy."),
        ):
            pause(message)
            try:
                source.witness(hwnd)
            except NativeRevisionError as error:
                assert error.code == "BUSY", "protected state did not fail busy"
                records.append({"event": busy_case, "code": "BUSY"})
            else:
                raise AssertionError("protected state unexpectedly produced a witness")
            pause("Release that state and return AutoCAD to idle.")
        previous, hwnd = sample()
        pause("Close without saving, then reopen the same disposable copy; do not open the source.")
        after, reopened_hwnd = sample()
        require_transition(previous, after, "reopen")
        window_reused = hwnd == reopened_hwnd
        records.append({"event": "reopen", "new_lifetime": True, "window_reused": window_reused})
        previous, hwnd = after, reopened_hwnd
        pause("Stop the bridge or close AutoCAD. Keep the disposable copy unsaved.")
        try:
            source.witness(hwnd)
        except NativeRevisionError as error:
            assert error.code in {"UNAVAILABLE", "TIMEOUT", "UNTRUSTED_PEER"}
            records.append({"event": "bridge_loss", "code": error.code})
        else:
            raise AssertionError("lost bridge unexpectedly produced a witness")
        pause(
            "Restart the leased AutoCAD installation, NETLOAD the selected profile, "
            "and reopen the disposable copy."
        )
        after, _ = sample()
        require_transition(previous, after, "restart")
        records.append({"event": "restart", "new_bridge": True})
        assert window_reused, "window reuse was not observed; acceptance remains incomplete"
        outcome = "observed"
    finally:
        try:
            if opened:
                with _operator_started_autocad() as attached:
                    current = attached
                    lease.assert_owned()
                    guard.assert_active_full_name(str(current.ActiveDocument.FullName))
                    guard.close_without_save(current.ActiveDocument.Close)
        finally:
            evidence = guard.finalize(preserve=True, reason="native observer operator evidence")
            payload = {
                "outcome": outcome,
                "release": release,
                "profile_selected": profile,
                "source_sha256_before": evidence.source_sha256_before,
                "source_sha256_after": evidence.source_sha256_after,
                "copy_sha256_before": evidence.copy_sha256_before,
                "copy_sha256_after": evidence.copy_sha256_after,
                "records": records,
            }
            assert (
                evidence.copy_sha256_before == evidence.copy_sha256_after
            ), "copy disk bytes changed"
            print("AUTOCAD_MCP_NATIVE_EVIDENCE=" + json.dumps(payload, sort_keys=True))  # noqa: T201 - redacted opt-in evidence

"""Guarded operator evidence; real AutoCAD execution remains opt-in."""

import importlib
from dataclasses import replace

import pytest
from autocad_mcp.adapter.native_revision import RevisionWitness


def observer():
    try:
        return importlib.import_module("tests.windows.native_observer_operator")
    except ModuleNotFoundError:
        pytest.fail("native operator evidence helper is missing")


@pytest.mark.parametrize("rule", ["stable", "advance", "reopen", "restart"])
def test_transition_gate_rejects_unchanged_witness_for_required_history(rule):
    api = observer()
    original = RevisionWitness(
        "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222", 1, None
    )
    if rule == "stable":
        api.require_transition(original, original, rule)
    else:
        with pytest.raises(AssertionError):
            api.require_transition(original, original, rule)


def test_transition_gate_requires_matching_lifetime_and_monotonic_epoch():
    api = observer()
    original = RevisionWitness(
        "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222", 4, None
    )
    api.require_transition(original, replace(original, epoch=5), "advance")
    api.require_transition(
        original,
        replace(original, session_id="33333333-3333-4333-8333-333333333333", epoch=5),
        "reopen",
    )
    api.require_transition(
        original,
        replace(
            original,
            bridge_id="44444444-4444-4444-8444-444444444444",
            session_id="33333333-3333-4333-8333-333333333333",
            epoch=1,
        ),
        "restart",
    )
    with pytest.raises(AssertionError):
        api.require_transition(
            original,
            replace(original, session_id="33333333-3333-4333-8333-333333333333", epoch=5),
            "advance",
        )
    with pytest.raises(AssertionError):
        api.require_transition(original, replace(original, epoch=3), "advance")


@pytest.mark.autocad
def test_operator_native_observer_evidence(autocad_smoke_session):
    try:
        observer().run(autocad_smoke_session)
    except Exception as error:
        pytest.fail(f"native observer operator check failed: {type(error).__name__}", pytrace=False)


def test_process_restart_requires_a_new_document_lifetime():
    api = observer()
    original = RevisionWitness(
        "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222", 4, None
    )
    with pytest.raises(AssertionError):
        api.require_transition(
            original, replace(original, bridge_id="44444444-4444-4444-8444-444444444444"), "restart"
        )

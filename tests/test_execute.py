"""execute(): dispatch, parameter handling, and failure containment.

execute() runs on the device reader thread for every keypress, so its contract
is strict: the right helper is called with the right arguments, deck-side
actions are delegated to the context, and nothing ever raises out of it — an
exception here would kill the reader thread and silently deaden the deck.
"""
from __future__ import annotations

import pytest

from fifine_deck import actions
from fifine_deck.model import Action


class Ctx:
    """An ActionContext that records what the engine asked the deck to do."""

    def __init__(self):
        self.calls = []

    def switch_profile(self, profile_id): self.calls.append(("switch_profile", profile_id))
    def next_profile(self): self.calls.append(("next_profile",))
    def prev_profile(self): self.calls.append(("prev_profile",))
    def goto_page(self, index): self.calls.append(("goto_page", index))
    def next_page(self): self.calls.append(("next_page",))
    def prev_page(self): self.calls.append(("prev_page",))
    def set_brightness(self, percent): self.calls.append(("set_brightness", percent))
    def adjust_brightness(self, delta): self.calls.append(("adjust_brightness", delta))
    def sleep_screen(self): self.calls.append(("sleep_screen",))


@pytest.fixture
def rec(monkeypatch):
    """Replace every OS-touching helper with a recorder, so tests never spawn
    processes, type keystrokes, or change the machine's volume."""
    calls = []

    def recorder(name):
        def fn(*a, **k):
            calls.append((name, a, k))
        return fn

    for name in ("_popen_detached", "_send_hotkey", "_type_text",
                 "_media", "_volume", "_close_app"):
        monkeypatch.setattr(actions, name, recorder(name))
    return calls


# -- OS-side actions --------------------------------------------------------

@pytest.mark.parametrize("t", ["launch_app", "run_command"])
def test_launch_and_run_spawn_detached_on_host(rec, t):
    actions.execute(Action(t, {"command": "gimp"}))
    assert rec == [("_popen_detached", ("gimp",), {"shell": True, "host": True})]


@pytest.mark.parametrize("t", ["launch_app", "run_command", "open_url"])
def test_blank_param_is_a_noop(rec, t):
    """An unconfigured key must do nothing rather than spawn an empty shell."""
    actions.execute(Action(t, {"command": "   ", "url": "  "}))
    assert rec == []


def test_open_url_uses_xdg_open(rec):
    actions.execute(Action("open_url", {"url": "https://example.com"}))
    assert rec == [("_popen_detached", (["xdg-open", "https://example.com"],), {})]


def test_hotkey_and_text(rec):
    actions.execute(Action("hotkey", {"keys": "ctrl+shift+m"}))
    actions.execute(Action("text", {"text": "hello"}))
    assert rec == [("_send_hotkey", ("ctrl+shift+m",), {}),
                   ("_type_text", ("hello",), {})]


def test_media_and_volume_defaults(rec):
    """Params are optional; the documented defaults must be used."""
    actions.execute(Action("media", {}))
    actions.execute(Action("volume", {}))
    assert rec == [("_media", ("play-pause",), {}),
                   ("_volume", ("up", "5"), {})]


def test_close_app(rec):
    actions.execute(Action("close_app", {"target": "firefox"}))
    assert rec == [("_close_app", ("firefox",), {})]


def test_close_app_blank_target_kills_nothing(monkeypatch):
    """Guarded inside _close_app, not execute() — a blank target must never
    reach pkill, which would match every process."""
    ran = []
    monkeypatch.setattr(actions, "_run", lambda *a, **k: ran.append(a))
    actions._close_app("   ")
    assert ran == []


# -- password ---------------------------------------------------------------

def test_password_literal_is_typed(rec):
    actions.execute(Action("password", {"password": "hunter2"}))
    assert rec == [("_type_text", ("hunter2",), {})]


def test_password_resolves_secret_id(rec, monkeypatch):
    from fifine_deck import secret_store
    monkeypatch.setattr(secret_store, "get", lambda sid: "from-keyring" if sid == "s1" else "")
    actions.execute(Action("password", {"secret_id": "s1"}))
    assert rec == [("_type_text", ("from-keyring",), {})]


def test_password_missing_secret_types_nothing_rather_than_none(rec, monkeypatch):
    """A failed keyring lookup must not type the string 'None'."""
    from fifine_deck import secret_store
    monkeypatch.setattr(secret_store, "get", lambda sid: None)
    actions.execute(Action("password", {"secret_id": "gone"}))
    assert rec == [("_type_text", ("",), {})]


# -- deck-side actions are delegated to the context --------------------------

@pytest.mark.parametrize("t", ["next_page", "prev_page", "next_profile",
                               "prev_profile", "sleep_screen"])
def test_simple_context_actions(t):
    ctx = Ctx()
    actions.execute(Action(t, {}), ctx)
    assert ctx.calls == [(t,)]


def test_switch_profile_passes_id():
    ctx = Ctx()
    actions.execute(Action("switch_profile", {"profile_id": "work"}), ctx)
    assert ctx.calls == [("switch_profile", "work")]


def test_goto_page_converts_to_zero_based():
    """The GUI shows 1-based page numbers; the controller wants 0-based."""
    ctx = Ctx()
    actions.execute(Action("goto_page", {"page": "3"}), ctx)
    assert ctx.calls == [("goto_page", 2)]


def test_brightness_modes():
    ctx = Ctx()
    actions.execute(Action("brightness", {"mode": "set", "value": "40"}), ctx)
    actions.execute(Action("brightness", {"mode": "up", "value": "10"}), ctx)
    actions.execute(Action("brightness", {"mode": "down", "value": "10"}), ctx)
    assert ctx.calls == [("set_brightness", 40),
                         ("adjust_brightness", 10),
                         ("adjust_brightness", -10)]


def test_brightness_direction_wins_over_sign():
    """'down' with an accidentally-negative step must still go down, not up."""
    ctx = Ctx()
    actions.execute(Action("brightness", {"mode": "down", "value": "-10"}), ctx)
    actions.execute(Action("brightness", {"mode": "up", "value": "-10"}), ctx)
    assert ctx.calls == [("adjust_brightness", -10), ("adjust_brightness", 10)]


def test_deck_action_without_context_is_ignored(rec):
    """Deck actions need a context; without one they must log, not raise."""
    actions.execute(Action("next_page", {}), None)
    assert rec == []


# -- multi ------------------------------------------------------------------

def test_multi_runs_steps_in_order(rec):
    actions.execute(Action("multi", {"steps": [
        {"action": {"type": "text", "params": {"text": "one"}}},
        {"action": {"type": "hotkey", "params": {"keys": "ctrl+s"}}},
    ]}))
    assert rec == [("_type_text", ("one",), {}),
                   ("_send_hotkey", ("ctrl+s",), {})]


def test_multi_sleeps_between_steps(rec, monkeypatch):
    slept = []
    monkeypatch.setattr(actions.time, "sleep", slept.append)
    actions.execute(Action("multi", {"steps": [
        {"action": {"type": "text", "params": {"text": "a"}}, "delay": 0.25},
        {"action": {"type": "text", "params": {"text": "b"}}, "delay": 0},
    ]}))
    assert slept == [0.25]          # a zero delay must not sleep at all


def test_multi_step_accepts_bare_action_dict(rec):
    """Steps may be {'action': {...}} or the action dict itself."""
    actions.execute(Action("multi", {"steps": [
        {"type": "text", "params": {"text": "bare"}},
    ]}))
    assert rec == [("_type_text", ("bare",), {})]


def test_multi_reaches_the_context():
    ctx = Ctx()
    actions.execute(Action("multi", {"steps": [
        {"action": {"type": "next_page", "params": {}}},
    ]}), ctx)
    assert ctx.calls == [("next_page",)]


# -- failure containment ----------------------------------------------------

def test_execute_never_raises_when_a_helper_fails(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("helper exploded")
    monkeypatch.setattr(actions, "_type_text", boom)
    actions.execute(Action("text", {"text": "x"}))          # must not raise


def test_execute_never_raises_on_garbage_params():
    ctx = Ctx()
    for action in (Action("goto_page", {"page": "not-a-number"}),
                   Action("brightness", {"mode": "set", "value": "abc"}),
                   Action("multi", {"steps": "not-a-list"}),
                   Action("totally_unknown_type", {})):
        actions.execute(action, ctx)                        # must not raise

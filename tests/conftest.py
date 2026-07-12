"""Shared test fixtures.

CRITICAL: no test may ever write the real ~/.config/fifine-control-deck config.
This autouse fixture redirects the model's config directories to a per-test tmp
location, so even a stray ensure_dirs()/save() lands in the sandbox.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    from fifine_deck import model
    cfgdir = tmp_path / "cfg"
    monkeypatch.setattr(model, "CONFIG_DIR", str(cfgdir))
    monkeypatch.setattr(model, "CONFIG_PATH", str(cfgdir / "config.json"))
    monkeypatch.setattr(model, "ICONS_DIR", str(cfgdir / "icons"))
    yield

"""Shared test fixtures.

CRITICAL: no test may ever write the real ~/.config/fifine-control-deck config.
This autouse fixture redirects the model's config directories to a per-test tmp
location, so even a stray ensure_dirs()/save() lands in the sandbox.
"""
import os

import pytest

# Must be set before Qt is imported: the GUI tests build real widgets, and
# without this they would need a display and hang or fail in CI.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole session — Qt allows only one."""
    QApplication = pytest.importorskip("PyQt6.QtWidgets").QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    # HOME and XDG_CONFIG_HOME first. Redirecting the model's paths alone left a
    # hole: anything that resolves a user path for itself — autostart_file() is
    # $XDG_CONFIG_HOME/autostart, and app.py's runtime and lock paths — still
    # read the developer's real home. Not hypothetical: the autostart CLI test
    # stat'd the real ~/.config/autostart entry, so the suite was RED on a
    # machine where the user genuinely has autostart enabled and green
    # everywhere else. It hid for hours because CI runs under a scrubbed HOME
    # and the local runs happened to set XDG_CONFIG_HOME — two different
    # accidents masking the same hole.
    #
    # Per-test isolation cannot fix this class: it only protects the tests
    # someone remembered to protect. Do it here, once, for everything.
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))

    from fifine_deck import model
    cfgdir = tmp_path / "cfg"
    monkeypatch.setattr(model, "CONFIG_DIR", str(cfgdir))
    monkeypatch.setattr(model, "CONFIG_PATH", str(cfgdir / "config.json"))
    monkeypatch.setattr(model, "ICONS_DIR", str(cfgdir / "icons"))
    # This only works because save()/load() resolve CONFIG_PATH at call time.
    # If either goes back to `path: str = CONFIG_PATH`, the default binds at
    # import and every default-path save escapes this sandbox — onto the real
    # ~/.config/fifine-control-deck. test_model.py pins that; don't remove it.
    yield

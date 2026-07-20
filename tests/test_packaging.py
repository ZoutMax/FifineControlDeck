"""Packaging invariants that nothing else would catch.

These assert facts about the files we ship — udev rule ordering, version
consistency, installer paths. Each one here has already been wrong in a
released build at least once.
"""
import glob
import os
import re
import xml.etree.ElementTree as ET

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


# -- udev ---------------------------------------------------------------------

def _rule_files():
    return sorted(glob.glob(os.path.join(ROOT, "packaging", "*-fifine-deck.rules")))


def test_exactly_one_udev_rule_is_shipped():
    assert len(_rule_files()) == 1, "two rule files would both be installed"


def test_udev_rule_sorts_before_the_uaccess_dispatcher():
    """systemd's /usr/lib/udev/rules.d/73-seat-late.rules is what acts on the
    tag:

        TAG=="uaccess|xaccess-*", ENV{MAJOR}!="", RUN{builtin}+="uaccess"

    udev applies rule files in lexical order, so ours must sort BEFORE 73. When
    it was numbered 99 the tag was set long after the dispatcher had already
    looked for it: the builtin never ran, no ACL was granted, and every user not
    in the (Debian/Ubuntu-only) plugdev group silently got no device access.
    """
    name = os.path.basename(_rule_files()[0])
    prefix = int(re.match(r"(\d+)-", name).group(1))
    assert prefix < 73, (
        f"{name} sorts after 73-seat-late.rules — TAG+=\"uaccess\" will never fire"
    )


def test_udev_rule_grants_both_uaccess_and_the_group():
    """uaccess covers users not in plugdev (and distros without it); the group
    is the fallback. Losing either narrows who can use the deck."""
    body = _read(_rule_files()[0])
    lines = [ln for ln in body.splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    assert lines, "rule file has no actual rules"
    for ln in lines:
        assert 'TAG+="uaccess"' in ln
        assert 'GROUP="plugdev"' in ln
    assert any("hidraw" in ln for ln in lines), "the deck is driven over /dev/hidraw"


def test_nothing_still_references_the_old_rule_name():
    """The rule is installed and referenced from ~10 places; a missed rename
    means shipping a file nobody installs, or installing one nobody reads."""
    name = os.path.basename(_rule_files()[0])
    stale = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in {".git", "backend", "__pycache__", "parts",
                                    "stage", "prime", "dist", ".mypy_cache",
                                    ".pytest_cache", ".ruff_cache", "htmlcov"}]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            if p.endswith((".png", ".jpg", ".so", ".deb", ".snap")):
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    lines = f.read().splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for n, line in enumerate(lines, 1):
                # Deliberately cleaning up or explaining the old name is fine;
                # actually installing or reading it is not.
                if "rm -f" in line or line.lstrip().startswith(("#", "//")):
                    continue
                for m in re.findall(r"\d+-fifine-deck\.rules", line):
                    if m != name:
                        stale.append(f"{os.path.relpath(p, ROOT)}:{n}: {m}")
    assert not stale, "stale udev rule references: " + "; ".join(stale)


# -- version consistency ------------------------------------------------------

def _snap_version():
    return re.search(r"^version:\s*'?([^'\s]+)'?", _read("snap/snapcraft.yaml"),
                     re.M).group(1)


def _deb_version():
    # Top entry, e.g. "fifine-control-deck (0.5.7ppa1) resolute; ..." — the
    # ppaN suffix is a re-upload marker, not a different upstream version.
    v = re.search(r"^fifine-control-deck \(([^)]+)\)", _read("debian/changelog"),
                  re.M).group(1)
    return re.sub(r"(ppa\d+|~.*)$", "", v)


@pytest.mark.parametrize("rel", [
    "packaging/io.github.zoutmax.FifineControlDeck.metainfo.xml",
])
def test_metainfo_advertises_the_shipped_version(rel):
    """AppStream drives what GNOME Software / Ubuntu App Center shows. Pinned
    metainfo silently mislabels every build — it was stuck at 0.5.2 while the
    project shipped 0.5.7."""
    root = ET.fromstring(_read(rel))
    releases = root.find("releases")
    assert releases is not None, "no <releases> block"
    versions = [r.get("version") for r in releases.findall("release")]
    assert versions, "no <release> entries"
    assert _deb_version() in versions, (
        f"{rel} lists {versions}, but the package ships {_deb_version()}")


def test_snap_and_deb_agree_on_the_version():
    assert _snap_version() == _deb_version()


def test_changelog_documents_the_shipped_version():
    assert f"[{_deb_version()}]" in _read("CHANGELOG.md")


# -- installer ----------------------------------------------------------------

def _build_deb_default_version():
    return re.search(r'VERSION="\$\{1:-([^}"]+)\}"', _read("packaging/build-deb.sh")).group(1)


def test_install_sh_finds_the_deb_the_build_actually_produces(tmp_path):
    """README tells users to run ./install.sh. It looked for a '_latest_'
    filename that no build path ever produced, so it always failed.

    Runs install.sh's real discovery logic against a fake build output rather
    than pattern-matching the source, so the shell globbing is what's tested.
    """
    import subprocess

    src = _read("install.sh")
    assert "_latest_" not in src, "no build path produces a '_latest_' .deb"

    produced = f"fifine-control-deck_{_deb_version()}_amd64.deb"
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / produced).write_bytes(b"")
    # An older build must not win over the current one.
    (tmp_path / "dist" / "fifine-control-deck_0.1.0_amd64.deb").write_bytes(b"")

    func = src[src.index("find_deb()"):src.index('DEB="$(find_deb)"')]
    out = subprocess.run(["bash", "-c", f"ARCH=amd64\n{func}\nfind_deb\n"],
                         cwd=tmp_path, capture_output=True, text=True)
    assert out.stdout.strip() == f"dist/{produced}", out.stderr


def test_install_sh_does_not_build_a_downgrade():
    """build-deb.sh defaults to a placeholder version, which apt would treat as
    a downgrade of a real install — install.sh must pass the real one."""
    src = _read("install.sh")
    assert "./packaging/build-deb.sh" in src
    assert "debian/changelog" in src, "version must come from the changelog"
    assert _build_deb_default_version() not in src.split("debian/changelog")[0], (
        "install.sh hardcodes build-deb.sh's placeholder version")


# -- monitor keys: the psutil dependency must exist on EVERY install path ----

def test_psutil_is_a_dependency_on_every_install_path():
    """Monitor keys need psutil. Each packaging channel installs deps its own
    way, so each one must name it — a miss ships monitors that render 'n/a'."""
    assert "python3-psutil" in _read("debian/control")
    assert "python3-psutil" in _read("packaging/build-deb.sh")
    assert re.search(r"^\s*- psutil$", _read("snap/snapcraft.yaml"), re.M)
    assert "python3-psutil" in _read("CONTRIBUTING.md")


def test_ci_gates_install_psutil():
    """Without psutil in the CI pip install, every real-sampler test skips
    silently and the gate goes green while sampling is untested."""
    for wf in ("python-package.yml", "release.yml"):
        src = _read(os.path.join(".github", "workflows", wf))
        pip_lines = [ln for ln in src.splitlines() if "pip install" in ln
                     and "PyQt6" in ln]
        assert pip_lines, wf
        assert all("psutil" in ln for ln in pip_lines), (
            f"{wf} pip install line is missing psutil")


def test_nvml_vram_support_is_actually_installable():
    """README/CHANGELOG advertise NVIDIA VRAM via NVML; pynvml must therefore
    be at least recommended (deb) / bundled (snap) and documented."""
    assert "python3-pynvml" in _read("debian/control")
    assert "python3-pynvml" in _read("packaging/build-deb.sh")
    assert re.search(r"^\s*- pynvml$", _read("snap/snapcraft.yaml"), re.M)
    assert "pynvml" in _read("README.md")


# -- release.sh safety gates (pre-0.6.0 audit round) -------------------------

def test_release_sh_gates_on_and_stages_the_changelog():
    """A tag whose CHANGELOG lacks the version section fails its own release
    CI — after being pushed. release.sh must check first and commit the file."""
    src = _read("release.sh")
    assert "CHANGELOG.md has no" in src, "missing pre-flight CHANGELOG check"
    assert "snap/snapcraft.yaml debian/changelog CHANGELOG.md" in src, (
        "CHANGELOG.md is not staged with the release commit")


def test_release_sh_refuses_a_tag_pointing_elsewhere():
    src = _read("release.sh")
    assert "points at a different commit" in src, (
        "release.sh silently reuses stale tags")


# ---------------------------------------------------------------------------
def test_deb_dependencies_are_desktop_agnostic():
    """The app must run on any Debian-family desktop (KDE, XFCE, LXQt, MATE,
    sway...), not just GNOME: Qt UI, XDG autostart, SecretService for secrets,
    playerctl for media, wpctl/pactl for audio. No desktop environment's
    packages may creep into the dependency chain."""
    with open(os.path.join(ROOT, "debian", "control")) as f:
        control = f.read().lower()
    section = control[control.index("depends:"):]
    for forbidden in ("gnome", "gtk", "kde", "kwallet", "plasma", "xfce"):
        assert forbidden not in section, f"desktop-specific dep: {forbidden}"

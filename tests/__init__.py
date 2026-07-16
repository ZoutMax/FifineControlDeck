"""Test package.

Marks tests/ as a package so suites can share helpers (e.g. MockDevice from
test_controller) via `from tests.x import y` regardless of how pytest is
invoked — with __init__.py present, pytest puts the repo root on sys.path
rather than tests/ itself.
"""

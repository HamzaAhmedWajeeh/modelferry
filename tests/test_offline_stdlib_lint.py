"""Enforce the offline.py trust-surface constraints from SPEC section 7/11.

offline.py must import only the standard library, never the modelferry package,
compile cleanly, and stay under the ~500-line review cap. Do not loosen this test;
fix offline.py instead (CLAUDE.md hard rule).
"""

import ast
import shutil
import subprocess
import sys

import pytest

from _bundle import OFFLINE_PY

MAX_LINES = 500


def _source():
    return OFFLINE_PY.read_text(encoding="utf-8")


def _import_roots(tree):
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # No relative imports allowed (level > 0).
            assert node.level == 0, "offline.py must not use relative imports"
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_imports_are_stdlib_only_and_no_modelferry():
    tree = ast.parse(_source(), filename=str(OFFLINE_PY))
    roots = _import_roots(tree)
    assert "modelferry" not in roots
    stdlib = sys.stdlib_module_names  # Python 3.10+ test runner
    non_stdlib = sorted(r for r in roots if r not in stdlib)
    assert not non_stdlib, f"offline.py imports non-stdlib modules: {non_stdlib}"


def test_no_future_annotations_import():
    # from __future__ import annotations would let tooling emit PEP 604 unions
    # that break at runtime on 3.9; forbid it explicitly (checked via AST, not a
    # substring, so the module docstring may still mention it).
    tree = ast.parse(_source(), filename=str(OFFLINE_PY))
    future_imports = [
        n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "__future__"
    ]
    assert not future_imports, "offline.py must not import from __future__"


def test_compiles_cleanly():
    compile(_source(), str(OFFLINE_PY), "exec")


def test_within_line_cap():
    lines = _source().splitlines()
    assert len(lines) <= MAX_LINES, f"offline.py is {len(lines)} lines (cap {MAX_LINES})"


def test_compiles_under_python39_if_available():
    py39 = shutil.which("python3.9")
    if not py39:
        pytest.skip("python3.9 not available on this machine")
    proc = subprocess.run(
        [py39, "-m", "py_compile", str(OFFLINE_PY)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr

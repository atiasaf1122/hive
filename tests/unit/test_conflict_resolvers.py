"""Per-filetype conflict resolvers."""
from __future__ import annotations

import json

import pytest

from backend.orchestrator.conflict_resolvers import (
    CargoTomlResolver,
    CssAppendResolver,
    ImportsResolver,
    PackageJsonResolver,
    PyProjectTomlResolver,
    RequirementsTxtResolver,
    has_conflicts,
    parse_one_conflict,
    resolve_conflict,
)


# ── parsing primitives ──────────────────────────────────────────────────────

def test_has_conflicts_detects_marker_set() -> None:
    assert has_conflicts("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> branch\n") is True
    assert has_conflicts("no markers here") is False


def test_parse_one_conflict_extracts_a_and_b() -> None:
    text = "before\n<<<<<<< HEAD\nA1\nA2\n=======\nB1\n>>>>>>> feature\nafter\n"
    block = parse_one_conflict(text)
    assert block is not None
    assert block.prefix == "before\n"
    assert block.side_a == "A1\nA2\n"
    assert block.side_b == "B1\n"
    assert block.suffix == "after\n"


# ── package.json ────────────────────────────────────────────────────────────

PACKAGE_CONFLICT = """\
<<<<<<< HEAD
{
  "name": "myapp",
  "dependencies": {
    "react": "^18.2.0",
    "zod": "^3.22.0"
  }
}
=======
{
  "name": "myapp",
  "dependencies": {
    "react": "^18.2.0",
    "axios": "^1.6.0"
  }
}
>>>>>>> feature
"""


def test_package_json_resolver_unions_deps() -> None:
    res = PackageJsonResolver().resolve("package.json", PACKAGE_CONFLICT)
    parsed = json.loads(res)
    assert parsed["dependencies"] == {
        "axios": "^1.6.0",
        "react": "^18.2.0",
        "zod": "^3.22.0",
    }


def test_package_json_resolver_only_acts_on_package_json() -> None:
    r = PackageJsonResolver()
    assert r.can_resolve("package.json", PACKAGE_CONFLICT) is True
    assert r.can_resolve("foo.json", PACKAGE_CONFLICT) is False


# ── requirements.txt ────────────────────────────────────────────────────────

REQ_CONFLICT = """\
fastapi>=0.115
uvicorn[standard]>=0.30
<<<<<<< HEAD
httpx>=0.27
pydantic>=2.7
=======
sqlalchemy>=2.0
httpx>=0.28
>>>>>>> feature
"""


def test_requirements_txt_resolver_dedupes_by_name() -> None:
    res = RequirementsTxtResolver().resolve("requirements.txt", REQ_CONFLICT)
    lines = [l for l in res.splitlines() if l.strip()]
    pkg_names = [l.split(">=", 1)[0].split("<", 1)[0].split("==", 1)[0].strip()
                 for l in lines]
    # `httpx` should appear once (we kept the first occurrence — 0.27).
    assert pkg_names.count("httpx") == 1
    assert "sqlalchemy" in pkg_names
    assert "pydantic" in pkg_names


def test_requirements_txt_resolver_matches_filename_variants() -> None:
    r = RequirementsTxtResolver()
    assert r.can_resolve("requirements.txt", REQ_CONFLICT)
    assert r.can_resolve("requirements-dev.txt", REQ_CONFLICT)
    assert not r.can_resolve("setup.py", REQ_CONFLICT)


# ── Cargo.toml ──────────────────────────────────────────────────────────────

CARGO_CONFLICT = """\
[package]
name = "hive"
version = "0.10.0"

[dependencies]
<<<<<<< HEAD
tokio = { version = "1", features = ["full"] }
serde = "1"
=======
tokio = { version = "1", features = ["full"] }
reqwest = "0.11"
>>>>>>> feature
"""


def test_cargo_toml_resolver_unions_crates() -> None:
    res = CargoTomlResolver().resolve("Cargo.toml", CARGO_CONFLICT)
    assert "tokio" in res
    assert "serde" in res
    assert "reqwest" in res
    # tokio should appear once (first match wins).
    assert res.count("tokio") == 1


def test_cargo_toml_resolver_skips_non_deps_conflicts() -> None:
    bad = """\
[package]
name = "hive"
<<<<<<< HEAD
version = "0.10.0"
=======
version = "0.11.0"
>>>>>>> branch
"""
    from backend.orchestrator.conflict_resolvers import NotResolvable
    with pytest.raises(NotResolvable):
        CargoTomlResolver().resolve("Cargo.toml", bad)


# ── pyproject.toml ──────────────────────────────────────────────────────────

PYPROJECT_CONFLICT = """\
[project]
name = "hive"
dependencies = [
<<<<<<< HEAD
    "fastapi>=0.115.0",
    "httpx>=0.27.0",
=======
    "fastapi>=0.115.0",
    "sqlalchemy>=2.0",
>>>>>>> feature
]
"""


def test_pyproject_toml_resolver_unions_deps_array() -> None:
    res = PyProjectTomlResolver().resolve("pyproject.toml", PYPROJECT_CONFLICT)
    assert "fastapi" in res
    assert "httpx" in res
    assert "sqlalchemy" in res
    # fastapi should appear once.
    assert res.count("fastapi") == 1


# ── ImportsResolver (Python, TS, Rust) ──────────────────────────────────────

PY_IMPORTS_CONFLICT = """\
<<<<<<< HEAD
import os
from pathlib import Path
=======
import os
from typing import Optional
>>>>>>> feature
"""


def test_imports_resolver_python_unions() -> None:
    r = ImportsResolver()
    assert r.can_resolve("module.py", PY_IMPORTS_CONFLICT)
    res = r.resolve("module.py", PY_IMPORTS_CONFLICT)
    assert "import os" in res
    assert res.count("import os") == 1
    assert "from pathlib import Path" in res
    assert "from typing import Optional" in res


TS_IMPORTS_CONFLICT = """\
<<<<<<< HEAD
import { useState } from "react";
import clsx from "clsx";
=======
import { useState, useEffect } from "react";
import { z } from "zod";
>>>>>>> feature
"""


def test_imports_resolver_typescript_unions() -> None:
    r = ImportsResolver()
    assert r.can_resolve("App.tsx", TS_IMPORTS_CONFLICT)
    res = r.resolve("App.tsx", TS_IMPORTS_CONFLICT)
    # Both useState lines preserved (we don't merge `from` clauses, just
    # dedupe identical lines).
    assert 'import { useState } from "react";' in res
    assert 'import { useState, useEffect } from "react";' in res
    assert 'import clsx from "clsx";' in res
    assert 'import { z } from "zod";' in res


def test_imports_resolver_rejects_non_import_conflict() -> None:
    bad = """\
<<<<<<< HEAD
def hello():
    print("a")
=======
def hello():
    print("b")
>>>>>>> branch
"""
    assert ImportsResolver().can_resolve("m.py", bad) is False


# ── CSS append ──────────────────────────────────────────────────────────────

CSS_APPEND_CONFLICT = """\
.button { color: red; }

<<<<<<< HEAD
.alert { color: orange; }
=======
.warning { color: yellow; }
>>>>>>> feature
"""


def test_css_append_resolver_keeps_both() -> None:
    r = CssAppendResolver()
    assert r.can_resolve("styles.css", CSS_APPEND_CONFLICT)
    res = r.resolve("styles.css", CSS_APPEND_CONFLICT)
    assert ".alert" in res
    assert ".warning" in res


def test_css_append_resolver_rejects_mid_file_conflict() -> None:
    mid = """\
.a { color: red; }
<<<<<<< HEAD
.alert {}
=======
.warning {}
>>>>>>> feature
.b { color: blue; }
"""
    assert CssAppendResolver().can_resolve("s.css", mid) is False


# ── dispatch ────────────────────────────────────────────────────────────────

def test_resolve_conflict_picks_package_json_resolver() -> None:
    result = resolve_conflict("package.json", PACKAGE_CONFLICT)
    assert result.resolver == "PackageJsonResolver"
    assert result.resolved is not None
    assert not has_conflicts(result.resolved)


def test_resolve_conflict_returns_unchanged_if_no_markers() -> None:
    plain = '{"name":"x"}'
    result = resolve_conflict("package.json", plain)
    assert result.resolved == plain
    assert result.resolver is None


def test_resolve_conflict_returns_none_for_unsupported_filetype() -> None:
    body = "<<<<<<< HEAD\nweird\n=======\nstuff\n>>>>>>> feature\n"
    result = resolve_conflict("some.unknown", body)
    assert result.resolved is None
    assert "escalate" in result.reason


def test_resolve_conflict_returns_none_for_non_additive_python_conflict() -> None:
    body = """\
<<<<<<< HEAD
def foo():
    return 1
=======
def foo():
    return 2
>>>>>>> feature
"""
    result = resolve_conflict("module.py", body)
    assert result.resolved is None
    assert "escalate" in result.reason

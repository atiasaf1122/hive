"""Heuristic per-filetype merge-conflict resolvers — Section 2.2.

A conflicted file in a git worktree looks like:

    <<<<<<< HEAD (or main)
    …branch A content…
    =======
    …branch B content…
    >>>>>>> feature-branch

For the file types HIVE most often sees agents step on each other in
(package.json, requirements.txt, Cargo.toml, pyproject.toml, JS/TS/
Python imports, CSS appends), the conflicts are almost always *additive*
— both branches added new entries. In those cases the right answer is
"keep both, deduped" and we can resolve them deterministically.

When the heuristics can't safely resolve a conflict, `resolve_conflict`
returns `(None, reason)` and the orchestrator hands it to the LLM resolver
(Section 2.3) or escalates to the user (Section 2.4).

Each resolver is implemented as `can_resolve(file_path, conflict_text)
→ bool` + `resolve(file_path, conflict_text) → str`. The dispatch
function `resolve_conflict(file_path, conflict_text)` walks every
resolver and returns the first successful merge.

All resolvers are pure functions of their inputs — they take and return
strings. No disk I/O. That makes tests trivial.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass


CONFLICT_RE = re.compile(
    r"^<{7}[^\n]*\n(?P<a>.*?)^={7}\n(?P<b>.*?)^>{7}[^\n]*\n",
    re.DOTALL | re.MULTILINE,
)


@dataclass
class ConflictBlock:
    prefix: str   # content before the marker
    side_a: str   # between <<<< and ====
    side_b: str   # between ==== and >>>>
    suffix: str   # content after the marker


def parse_one_conflict(text: str) -> ConflictBlock | None:
    """Find the FIRST conflict block in `text`. Returns None if absent."""
    m = CONFLICT_RE.search(text)
    if not m:
        return None
    return ConflictBlock(
        prefix=text[: m.start()],
        side_a=m.group("a"),
        side_b=m.group("b"),
        suffix=text[m.end():],
    )


def has_conflicts(text: str) -> bool:
    return CONFLICT_RE.search(text) is not None


# ── base class ──────────────────────────────────────────────────────────────


class ConflictResolver(ABC):
    """Per-filetype heuristic. `can_resolve` is cheap; `resolve` does work."""

    @abstractmethod
    def can_resolve(self, file_path: str, conflict_text: str) -> bool: ...

    @abstractmethod
    def resolve(self, file_path: str, conflict_text: str) -> str: ...

    @property
    def name(self) -> str:
        return type(self).__name__


# ── helpers ─────────────────────────────────────────────────────────────────


def _dedupe_lines_preserve_order(*lines_lists: list[str]) -> list[str]:
    """Concatenate, drop duplicates, keep the *first* occurrence's position."""
    seen = set()
    out: list[str] = []
    for lst in lines_lists:
        for line in lst:
            if line not in seen:
                seen.add(line)
                out.append(line)
    return out


def _split_to_lines(text: str) -> list[str]:
    """Split a chunk of text into lines, stripping the final \n empty piece."""
    if not text:
        return []
    return text.splitlines()


# ── package.json ────────────────────────────────────────────────────────────


class PackageJsonResolver(ConflictResolver):
    """Merge dependencies as union; for everything else, prefer side A.

    package.json conflicts are almost always two branches adding distinct
    entries to `dependencies` or `devDependencies`. We parse both sides
    as JSON, take the union of every dependency-shaped object, and pick
    side A for any scalar field that differs.
    """

    def can_resolve(self, file_path: str, conflict_text: str) -> bool:
        return file_path.endswith("package.json") and has_conflicts(conflict_text)

    def resolve(self, file_path: str, conflict_text: str) -> str:
        import json

        block = parse_one_conflict(conflict_text)
        if block is None:
            return conflict_text

        # We need *complete* package.json on each side to parse it. The
        # conflict block holds only the conflicted region. Reconstruct both
        # candidate full files.
        full_a = block.prefix + block.side_a + block.suffix
        full_b = block.prefix + block.side_b + block.suffix

        try:
            a = json.loads(full_a)
            b = json.loads(full_b)
        except json.JSONDecodeError:
            # Partial conflict — the merged result won't be valid JSON.
            raise NotResolvable("package.json conflict isn't whole-file")

        merged: dict = {}
        for key in {*a.keys(), *b.keys()}:
            av, bv = a.get(key), b.get(key)
            if key in DEPENDENCY_FIELDS and isinstance(av, dict) and isinstance(bv, dict):
                # Union; on collision prefer the higher version-y string (just
                # take A's value — deterministic and obvious to the user).
                merged_deps: dict[str, str] = dict(av)
                for pkg, ver in bv.items():
                    merged_deps.setdefault(pkg, ver)
                merged[key] = dict(sorted(merged_deps.items()))
            else:
                merged[key] = av if av is not None else bv

        out = json.dumps(merged, indent=2) + "\n"
        return out


DEPENDENCY_FIELDS = {
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
}


# ── requirements.txt ────────────────────────────────────────────────────────


class RequirementsTxtResolver(ConflictResolver):
    """Union of two requirements lists, deduped by package name (case-insensitive)."""

    def can_resolve(self, file_path: str, conflict_text: str) -> bool:
        name = file_path.rsplit("/", 1)[-1].lower()
        if not (name == "requirements.txt" or name.startswith("requirements-")):
            return False
        return has_conflicts(conflict_text)

    def resolve(self, file_path: str, conflict_text: str) -> str:
        block = parse_one_conflict(conflict_text)
        if block is None:
            return conflict_text

        a_lines = _split_to_lines(block.side_a)
        b_lines = _split_to_lines(block.side_b)

        # Track which base package names we've already taken.
        def _pkg_name(line: str) -> str:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                return ""
            # Take everything before the first <, >, =, !, ~, or space.
            return re.split(r"[<>=!~\s]", stripped, maxsplit=1)[0].lower()

        merged: list[str] = []
        seen: set[str] = set()
        for line in (*a_lines, *b_lines):
            base = _pkg_name(line)
            if base and base in seen:
                continue
            if base:
                seen.add(base)
            merged.append(line)

        body = "\n".join(merged)
        if not body.endswith("\n"):
            body += "\n"
        return block.prefix + body + block.suffix


# ── Cargo.toml ──────────────────────────────────────────────────────────────


class CargoTomlResolver(ConflictResolver):
    """Union the `[dependencies]` and `[dev-dependencies]` sections.

    Cargo's TOML is fiddly to parse correctly without a real TOML library.
    We use Python's `tomllib` (stdlib in 3.11+) to read and re-emit, but
    we keep the resolver scoped to the *common* case: the conflicting
    block is contained entirely inside `[dependencies]` or
    `[dev-dependencies]`. Outside those sections we don't claim to merge.
    """

    SECTIONS = ("[dependencies]", "[dev-dependencies]", "[build-dependencies]")

    def can_resolve(self, file_path: str, conflict_text: str) -> bool:
        return file_path.endswith("Cargo.toml") and has_conflicts(conflict_text)

    def resolve(self, file_path: str, conflict_text: str) -> str:
        block = parse_one_conflict(conflict_text)
        if block is None:
            return conflict_text

        prefix_lower = block.prefix.rstrip().splitlines()[-1].strip().lower() if block.prefix.strip() else ""
        if not any(s.lower() in prefix_lower for s in self.SECTIONS):
            raise NotResolvable(
                "Cargo.toml conflict isn't inside a [dependencies]-style section"
            )

        a_lines = _split_to_lines(block.side_a)
        b_lines = _split_to_lines(block.side_b)

        def _crate(line: str) -> str:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                return ""
            return stripped.split("=", 1)[0].strip().lower()

        merged: list[str] = []
        seen: set[str] = set()
        for line in (*a_lines, *b_lines):
            base = _crate(line)
            if base and base in seen:
                continue
            if base:
                seen.add(base)
            merged.append(line)

        body = "\n".join(merged)
        if not body.endswith("\n"):
            body += "\n"
        return block.prefix + body + block.suffix


# ── pyproject.toml — dependencies array ─────────────────────────────────────


class PyProjectTomlResolver(ConflictResolver):
    """Merge `[project.dependencies]` arrays as union.

    Modern pyproject.toml uses a `dependencies = [...]` array under
    `[project]`. We handle the common case where both branches added new
    entries to that array.
    """

    def can_resolve(self, file_path: str, conflict_text: str) -> bool:
        return file_path.endswith("pyproject.toml") and has_conflicts(conflict_text)

    def resolve(self, file_path: str, conflict_text: str) -> str:
        block = parse_one_conflict(conflict_text)
        if block is None:
            return conflict_text

        a_lines = _split_to_lines(block.side_a)
        b_lines = _split_to_lines(block.side_b)

        # Heuristic: if every non-blank line looks like an array entry
        # (`"package>=ver",` or `"package"`), do an additive merge.
        def _is_array_entry(line: str) -> bool:
            stripped = line.strip().rstrip(",")
            return stripped.startswith(("'", '"')) and stripped.endswith(("'", '"'))

        all_lines = [*a_lines, *b_lines]
        if not all(
            _is_array_entry(line) or not line.strip() or line.strip().startswith("#")
            for line in all_lines
        ):
            raise NotResolvable(
                "pyproject.toml conflict isn't a clean array-of-strings merge"
            )

        def _base_pkg(line: str) -> str:
            stripped = line.strip().rstrip(",").strip("'").strip('"')
            return re.split(r"[<>=!~\s]", stripped, maxsplit=1)[0].lower()

        merged: list[str] = []
        seen: set[str] = set()
        for line in all_lines:
            base = _base_pkg(line)
            if base and base in seen:
                continue
            if base:
                seen.add(base)
            merged.append(line)

        body = "\n".join(merged)
        if not body.endswith("\n"):
            body += "\n"
        return block.prefix + body + block.suffix


# ── Top-of-file imports (JS/TS/Python/Rust) ─────────────────────────────────


class ImportsResolver(ConflictResolver):
    """Union the import / use lines at the top of source files."""

    IMPORT_PREFIXES = {
        ".py": ("import ", "from "),
        ".js": ("import ", "const ", "require("),
        ".jsx": ("import ", "const ", "require("),
        ".ts": ("import ", "export "),  # also `export { ... } from`
        ".tsx": ("import ", "export "),
        ".rs": ("use ", "extern "),
    }

    def _ext(self, file_path: str) -> str:
        idx = file_path.rfind(".")
        return file_path[idx:].lower() if idx >= 0 else ""

    def can_resolve(self, file_path: str, conflict_text: str) -> bool:
        ext = self._ext(file_path)
        if ext not in self.IMPORT_PREFIXES:
            return False
        if not has_conflicts(conflict_text):
            return False
        block = parse_one_conflict(conflict_text)
        if block is None:
            return False
        prefixes = self.IMPORT_PREFIXES[ext]
        # Every non-blank line on both sides must look like an import.
        for chunk in (block.side_a, block.side_b):
            for line in _split_to_lines(chunk):
                stripped = line.strip()
                if not stripped or stripped.startswith(("//", "#")):
                    continue
                if not stripped.startswith(prefixes):
                    return False
        return True

    def resolve(self, file_path: str, conflict_text: str) -> str:
        block = parse_one_conflict(conflict_text)
        if block is None:
            return conflict_text
        merged = _dedupe_lines_preserve_order(
            _split_to_lines(block.side_a),
            _split_to_lines(block.side_b),
        )
        body = "\n".join(merged)
        if not body.endswith("\n"):
            body += "\n"
        return block.prefix + body + block.suffix


# ── CSS appends ─────────────────────────────────────────────────────────────


class CssAppendResolver(ConflictResolver):
    """When two branches append rules to the end of a CSS file: keep both,
    in A-then-B order. Detects the case by requiring the conflict to sit
    at the end of the file (suffix is empty or whitespace) and both sides
    to look like balanced CSS rules."""

    def can_resolve(self, file_path: str, conflict_text: str) -> bool:
        ext = file_path.rsplit(".", 1)[-1].lower()
        if ext not in ("css", "scss", "sass"):
            return False
        block = parse_one_conflict(conflict_text)
        if block is None:
            return False
        if block.suffix.strip():
            return False
        # Both sides should have balanced braces.
        for side in (block.side_a, block.side_b):
            if side.count("{") != side.count("}"):
                return False
        return True

    def resolve(self, file_path: str, conflict_text: str) -> str:
        block = parse_one_conflict(conflict_text)
        if block is None:
            return conflict_text
        a = block.side_a.rstrip("\n")
        b = block.side_b.rstrip("\n")
        body = f"{a}\n\n{b}\n"
        return block.prefix + body + block.suffix


# ── dispatch ────────────────────────────────────────────────────────────────


class NotResolvable(Exception):
    """A resolver thinks it could handle the file type, but the specific
    conflict isn't shaped right for an automatic merge."""


DEFAULT_RESOLVERS: list[ConflictResolver] = [
    PackageJsonResolver(),
    RequirementsTxtResolver(),
    CargoTomlResolver(),
    PyProjectTomlResolver(),
    ImportsResolver(),
    CssAppendResolver(),
]


@dataclass
class ResolutionResult:
    resolved: str | None
    resolver: str | None
    reason: str


def resolve_conflict(
    file_path: str,
    conflict_text: str,
    resolvers: list[ConflictResolver] | None = None,
) -> ResolutionResult:
    """Walk every resolver. Return the first successful merge."""
    if not has_conflicts(conflict_text):
        return ResolutionResult(
            resolved=conflict_text, resolver=None,
            reason="no conflict markers in file",
        )

    if resolvers is None:
        resolvers = DEFAULT_RESOLVERS

    for r in resolvers:
        if not r.can_resolve(file_path, conflict_text):
            continue
        try:
            merged = r.resolve(file_path, conflict_text)
        except NotResolvable as exc:
            # Try the next resolver — maybe a more general one will fit.
            continue  # noqa: B007 (variable kept for clarity; exc unused)
        else:
            if has_conflicts(merged):
                # The "merge" still has markers — that's a bug, treat as failure.
                continue
            return ResolutionResult(
                resolved=merged, resolver=r.name,
                reason="heuristic merge",
            )

    return ResolutionResult(
        resolved=None, resolver=None,
        reason="no heuristic resolver applied — escalate to LLM or user",
    )

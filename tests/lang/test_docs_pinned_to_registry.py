"""The hand-written language docs are pinned to ``crude.bindings.REGISTRY``.

:mod:`crude.bindings` is the single source of truth for what a script can call
and how many arguments each name takes; the docs under ``docs/`` are written by
hand against it. A hand-written list drifts - a phantom constructor, a signature
missing a required argument, an extra parameter the decorator never declares, a
binding renamed in code but not on the page - and every one of those is a promise
the interpreter will not keep. This test is the guard the plan's "docs never
drift" claim was always missing.

Two doc shapes need two checks:

* ``bindings.md``/``widgets.md``/``scripting.md`` spell names as ``NAME(...)``
  calls, so they get a **name** check (the call names a real ``REGISTRY``
  entry) and an **arity** check (the required count ``len(arg_names) -
  optional`` is never more than the doc shows, and the doc never shows more
  arguments than the decorator declares). Signatures whose trailing arguments
  are elided with ``...`` are name-checked but not arity-checked, because the
  ellipsis means the rest is not spelled out. The checks are deliberately
  robust against the docs' descriptive spellings (a list may say ``lo, hi``
  where the decorator says ``min, max``), so argument names are not compared.
* ``manifest.md``/``architecture.md``/``glyphs.md`` also reference bindings as
  bare words (```` ``MODE`` ````, ```` ``SERIAL_OPEN`` ````, a ``P_RECT`` mention),
  so they get an additional **name-existence** check: every binding-shaped
  all-caps token must resolve to a ``REGISTRY`` entry, modulo a curated
  allowlist of the legitimate non-binding vocabulary those pages use (language
  keywords, exit-code constants, hardware/file-format acronyms). A typo or a
  renamed binding is neither a real binding nor on that allowlist, so it trips
  the test. That check runs ``_check_doc`` too, so a doc in this group is
  guarded on both fronts - which is why ``glyphs.md``, with its ``P_GLYPH(...)``
  call and its bare ``P_RECT`` mention, belongs here rather than in the
  call-only group above.
"""

from __future__ import annotations

import re
from pathlib import Path

from crude.bindings import REGISTRY

#: the three hand-written docs that carry ``NAME(...)`` binding references.
DOCS = ("bindings.md", "widgets.md", "scripting.md")

#: all-caps words that appear in call form but are language keywords or host
#  types rather than bindings, so they are not checked against ``REGISTRY``.
NON_BINDINGS = {"FUNC"}

#: an all-caps identifier immediately followed by an opening paren.
CALL = re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*)\(")

#: the docs that carry bare code-span binding names (and any ``NAME(...)``
#  calls) and so get the name-existence token check on top of the call-form
#  arity check. glyphs.md is here because it uses both forms.
PROSE_DOCS = ("manifest.md", "architecture.md", "glyphs.md")

#: an all-caps identifier of two or more characters - a call's name or a bare
#  code-span word. Single letters (sentence-initial capitals) are excluded.
WORD = re.compile(r"\b([A-Z][A-Z0-9_]+)\b")

#: every all-caps token these docs use that is legitimate but *not* a
#  binding: the language keywords, the ``EXIT_*`` codes from ``crude.errors``,
#  and hardware/file-format acronyms (from ``glyphs.md``: DPR/UV/SHA/PNG/
#  RGBA/GL/GPU/DPI/DIR). A typo or a renamed binding is neither in ``REGISTRY``
#  nor on this list, so it trips the name-existence check.
PROSE_NON_BINDINGS = frozenset({
    "AST", "CI", "CLI", "COM3",
    "EXIT_LOAD", "EXIT_MANIFEST", "EXIT_OK", "EXIT_RUNTIME", "EXIT_SCRIPT",
    "FUNC", "GLFW", "JSON", "TTF", "UI", "VAR", "WHILE",
    "DIR", "DPI", "DPR", "GL", "GPU", "PNG", "RGBA", "SHA", "UV",
})


def _inside_parens(text: str, open_at: int) -> str | None:
    """The raw text inside the ``(`` at ``open_at``, or ``None`` if unbalanced."""
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:i]
    return None


def _split_args(inner: str) -> list[str]:
    """Split a parenthetical on top-level commas only (brackets/parens nest)."""
    parts, depth, cur = [], 0, ""
    for ch in inner:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
            continue
        cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _signatures(text: str):
    """Yield ``(name, inner)`` for every ``NAME(...)`` in ``text``."""
    for match in CALL.finditer(text):
        name = match.group(1)
        if name in NON_BINDINGS:
            continue
        inner = _inside_parens(text, match.end() - 1)
        yield name, inner


def _check_doc(path: Path) -> list[str]:
    """Every way one doc's ``NAME(...)`` references disagree with REGISTRY."""
    text = path.read_text(encoding="utf-8")
    problems: list[str] = []
    for name, inner in _signatures(text):
        entry = REGISTRY.get(name)
        if entry is None:
            problems.append(f"{path.name}: `{name}(...)` names no binding")
            continue
        if inner is None:  # unbalanced - skip arity, name already verified
            continue
        total = len(entry.arg_names)
        required = total - entry.optional
        if inner.strip() == "":
            shown_required = shown_total = 0
            elided = False
        else:
            tokens = _split_args(inner)
            elided = "..." in tokens
            shown = [t for t in tokens if t != "..."]
            shown_total = len(shown)
            shown_required = sum(1 for t in shown if not t.startswith("["))
        if elided:
            continue
        if required > shown_required:
            problems.append(
                f"{path.name}: `{name}` shows {shown_required} required "
                f"argument(s) but the binding needs {required} "
                f"(arg_names={entry.arg_names}, optional={entry.optional})"
            )
        if shown_total > total:
            problems.append(
                f"{path.name}: `{name}` shows {shown_total} argument(s) but "
                f"the binding declares {total} (arg_names={entry.arg_names})"
            )
    return problems


def _binding_word_problems(path: Path) -> list[str]:
    """Every binding-shaped token in a prose doc that names no real binding.

    These pages spell names as bare code-span words rather than ``NAME(...)``
    calls, so there is no arity to read; what is pinned is that every
    all-caps token either resolves to a ``REGISTRY`` entry or is on the
    curated non-binding allowlist. Trailing-underscore families (``ON_*``,
    ``PD_*``) are wildcards, not names, and are skipped.
    """
    text = path.read_text(encoding="utf-8")
    problems: list[str] = []
    for match in WORD.finditer(text):
        token = match.group(1)
        if token in PROSE_NON_BINDINGS or token.endswith("_") or token in REGISTRY:
            continue
        problems.append(f"{path.name}: `{token}` names no binding")
    return sorted(set(problems))


def test_every_binding_signature_in_the_docs_matches_the_registry(repo_root):
    """No doc call names a phantom binding or misstates its arity."""
    docs_dir = repo_root / "docs"
    failures: list[str] = []
    for doc in DOCS:
        path = docs_dir / doc
        assert path.exists(), f"missing doc under test: {path}"
        failures.extend(_check_doc(path))
    assert not failures, "\n".join(["", *failures])


def test_docs_actually_carry_binding_signatures(repo_root):
    """Guard the guard: the parser must find calls, or the check above is vacuous.

    A regex or doc-format change that stopped matching anything would make
    ``test_every_binding_signature_in_the_docs_matches_the_registry`` pass for
    the wrong reason, so this pins that a healthy number of real signatures are
    being read out of the docs.
    """
    docs_dir = repo_root / "docs"
    names = set()
    for doc in DOCS:
        for name, _inner in _signatures((docs_dir / doc).read_text("utf-8")):
            names.add(name)
    matched = {n for n in names if n in REGISTRY}
    assert len(matched) > 80, (
        f"only {len(matched)} registry bindings found in the docs; the parser "
        f"or the doc format likely broke"
    )


def test_prose_docs_binding_names_resolve_to_the_registry(repo_root):
    """No binding reference in a bare-name doc names a phantom or stale binding.

    The docs in ``PROSE_DOCS`` (``manifest.md``, ``architecture.md``,
    ``glyphs.md``) reference bindings as code-span words as well as - for
    ``glyphs.md`` - ``NAME(...)`` calls, so two things are pinned here: every
    all-caps token is either a real ``REGISTRY`` binding or a known non-binding
    term, and every ``NAME(...)`` call (e.g. ``EXIT(0)``, ``P_GLYPH(p, x, y,
    w, h, name)``) is arity-checked through the same ``_check_doc`` the
    reference docs use, so a stale name or signature trips the test.
    """
    docs_dir = repo_root / "docs"
    failures: list[str] = []
    for doc in PROSE_DOCS:
        path = docs_dir / doc
        assert path.exists(), f"missing doc under test: {path}"
        failures.extend(_binding_word_problems(path))
        failures.extend(_check_doc(path))
    assert not failures, "\n".join(["", *failures])


def test_prose_docs_actually_name_bindings(repo_root):
    """Guard the guard for the prose-doc check, mirroring the call-form one.

    If the token regex or the code-span format broke, the name-existence check
    would find nothing to complain about and pass for the wrong reason, so this
    pins that a real number of ``REGISTRY`` names are read out of the prose docs.
    """
    docs_dir = repo_root / "docs"
    found = set()
    for doc in PROSE_DOCS:
        text = (docs_dir / doc).read_text("utf-8")
        for match in WORD.finditer(text):
            if match.group(1) in REGISTRY:
                found.add(match.group(1))
    assert len(found) >= 5, (
        f"only {len(found)} registry binding names found in the prose docs "
        f"{PROSE_DOCS}; the token parser or the doc format likely broke"
    )

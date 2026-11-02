"""The build cache: authored patches become hash-named ``.pd`` files on disk.

libpd only opens patches from a file, so every ``PD_LOAD`` needs one. The cache
makes that invisible and cheap:

* the file name is ``<patch id>-<sha256[:8]>.pd``, so an unchanged patch reuses
  its file and a changed one gets a new name - no timestamps, no staleness
  heuristics, and the content always matches the name;
* the directory (``.crude_build/pd`` by default, from ``pd.build_dir`` in the
  manifest) is disposable and ``.gitignore``d; :meth:`BuildCache.prune` removes
  what the current run did not produce;
* every file is an ordinary Pd patch, so opening the cache in Pd to see what the
  script actually built is the debugging path, not an afterthought - which is
  also why a failed load quotes the cache path and the text.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Dict, Iterable, Optional, Tuple

from .model import PatchDoc
from .writer import write_patch

#: Where a project keeps its generated patches, relative to the project root.
DEFAULT_BUILD_DIR = os.path.join(".crude_build", "pd")

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def slug(text: str) -> str:
    """A filesystem-safe fragment of a patch id."""
    cleaned = _UNSAFE.sub("-", str(text)).strip("-._")
    return cleaned or "patch"


def digest_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class BuildCache:
    """Serialize patches into ``<id>-<hash8>.pd`` and keep the directory tidy."""

    def __init__(self, directory: Optional[str] = None, rebuild: bool = False,
                 log=None):
        self.directory = os.path.abspath(directory or DEFAULT_BUILD_DIR)
        self.rebuild = bool(rebuild)
        self.log = log or (lambda message: None)
        #: patch id -> the file the cache produced for it this run
        self.paths: Dict[str, str] = {}
        self.generated = 0
        self.reused = 0
        self.pruned = 0

    # -- names ----------------------------------------------------------
    def render(self, doc: PatchDoc) -> str:
        """The deterministic text of a patch (validation included)."""
        return write_patch(doc)

    def name_for(self, doc: PatchDoc, text: Optional[str] = None) -> str:
        text = self.render(doc) if text is None else text
        return f"{slug(doc.id)}-{digest_of(text)[:8]}.pd"

    def path_for(self, doc: PatchDoc) -> str:
        """Where this patch *would* be cached; does not touch the disk."""
        text = self.render(doc)
        return os.path.join(self.directory, self.name_for(doc, text))

    # -- writing --------------------------------------------------------
    def write(self, doc: PatchDoc) -> Tuple[str, str]:
        """Cache ``doc``; returns ``(path, text)``. Reuses the file when unchanged."""
        text = self.render(doc)
        name = self.name_for(doc, text)
        path = os.path.join(self.directory, name)
        self.paths[doc.id] = path

        if not self.rebuild and os.path.isfile(path):
            self.reused += 1
            return path, text

        os.makedirs(self.directory, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        self.generated += 1
        self.log(f"built {name}")
        return path, text

    # -- housekeeping ---------------------------------------------------
    def existing(self) -> Iterable[str]:
        try:
            entries = os.listdir(self.directory)
        except OSError:
            return []
        return sorted(
            os.path.join(self.directory, entry)
            for entry in entries
            if entry.endswith(".pd")
        )

    def prune(self, keep: Iterable[str] = None) -> int:
        """Delete cached patches this run did not use; returns how many."""
        wanted = set(keep if keep is not None else self.paths.values())
        removed = 0
        for path in self.existing():
            if path in wanted:
                continue
            try:
                os.remove(path)
            except OSError:  # pragma: no cover - locked by another process
                self.log(f"could not prune {os.path.basename(path)}")
                continue
            removed += 1
        self.pruned += removed
        return removed

    def clear(self) -> int:
        """Remove every generated patch (``--rebuild-pd`` starts from scratch)."""
        removed = self.prune(keep=())
        self.paths.clear()
        self.generated = self.reused = 0
        return removed

    def report(self) -> Dict[str, int]:
        return {
            "generated": self.generated,
            "reused": self.reused,
            "pruned": self.pruned,
        }

    def __repr__(self):
        return f"<BuildCache {self.directory} kept={len(self.paths)}>"

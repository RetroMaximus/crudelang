"""The build cache: patches by content hash, and nothing to clean up by hand.

libpd opens patches from files, so ``PD_LOAD`` needs one on disk. These tests
cover the two promises that make it invisible: the file name is a function of
the content (unchanged patch, unchanged file, no rewriting) and the directory is
disposable (what this run did not produce can go).
"""

from __future__ import annotations

import os

import pytest

from crude.pd.cache import DEFAULT_BUILD_DIR, BuildCache, digest_of, slug
from crude.pd.model import PatchDoc, PatchError


@pytest.fixture
def tone():
    """A small valid patch, plus a way to change it in place."""
    doc = PatchDoc("synth patch")
    osc = doc.add_object("osc~ 440", 20, 20)
    dac = doc.add_object("dac~", 20, 60)
    doc.link(osc, 0, dac, 0)
    return doc


@pytest.fixture
def cache_dir(tmp_path):
    """An existing, empty build directory: the cache is allowed to assume it."""
    directory = tmp_path / "build" / "pd"
    directory.mkdir(parents=True)
    return str(directory)


def touch(doc, value):
    """Change one number in the patch, so its content hash moves."""
    doc.boxes[0].text = f"osc~ {value}"


# -- names --------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("wave", "wave"),
        ("synth patch", "synth-patch"),
        ("synth\t2", "synth-2"),
        ("wave_2.0-x", "wave_2.0-x"),
        ("../../etc/passwd", "etc-passwd"),
        ("...;;", "patch"),
        ("", "patch"),
    ],
)
def test_a_patch_id_becomes_a_safe_file_fragment(text, expected):
    assert slug(text) == expected


def test_the_digest_is_stable_and_short():
    assert digest_of("abc") == digest_of("abc")
    assert digest_of("abc") != digest_of("abd")
    assert digest_of("abc")[:8] == "ba7816bf"


def test_the_name_is_the_id_plus_the_content_hash(tone):
    cache = BuildCache()
    name = cache.name_for(tone)

    assert name.startswith("synth-patch-")
    assert name.endswith(".pd")
    assert len(name[len("synth-patch-"):-len(".pd")]) == 8
    assert name == cache.name_for(tone)


def test_the_name_moves_when_the_patch_moves(tone, cache_dir):
    cache = BuildCache(cache_dir)
    before = cache.name_for(tone)
    touch(tone, 220)

    assert cache.name_for(tone) != before


def test_path_for_does_not_touch_the_disk(tone, cache_dir):
    cache = BuildCache(cache_dir)

    assert cache.path_for(tone) == os.path.join(cache_dir, cache.name_for(tone))
    assert os.listdir(cache_dir) == []


def test_the_default_directory_is_under_the_project():
    assert BuildCache().directory.endswith(DEFAULT_BUILD_DIR)
    assert os.path.isabs(BuildCache().directory)


# -- writing ------------------------------------------------------------


def test_a_patch_is_written_as_exact_bytes(tone, cache_dir):
    cache = BuildCache(cache_dir)

    path, text = cache.write(tone)

    assert path == os.path.join(cache_dir, cache.name_for(tone))
    with open(path, "rb") as handle:
        raw = handle.read()
    assert raw == text.encode("utf-8")
    assert b"\r" not in raw


def test_the_cache_reports_what_it_generated(tone, cache_dir):
    messages = []
    cache = BuildCache(cache_dir, log=messages.append)

    cache.write(tone)

    assert cache.report() == {"generated": 1, "reused": 0, "pruned": 0}
    assert messages == [f"built {os.path.basename(cache.paths['synth patch'])}"]


def test_an_unchanged_patch_is_reused_not_rewritten(tone, cache_dir):
    cache = BuildCache(cache_dir)
    path, _text = cache.write(tone)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("#N canvas 0 0 1 1 12;\n")

    second, _ = cache.write(tone)

    assert second == path
    assert cache.report() == {"generated": 1, "reused": 1, "pruned": 0}
    with open(path, "r", encoding="utf-8") as handle:
        assert handle.read() == "#N canvas 0 0 1 1 12;\n"


def test_a_reused_name_is_the_one_the_next_run_will_use_too(tone, cache_dir):
    BuildCache(cache_dir).write(tone)

    fresh = BuildCache(cache_dir)
    fresh.write(tone)

    assert fresh.report()["reused"] == 1


def test_rebuild_writes_the_file_again_even_when_it_exists(tone, cache_dir):
    cache = BuildCache(cache_dir)
    cache.write(tone)
    rebuilding = BuildCache(cache_dir, rebuild=True)

    rebuilding.write(tone)

    assert rebuilding.report() == {"generated": 1, "reused": 0, "pruned": 0}


def test_a_changed_patch_gets_a_new_file(tone, cache_dir):
    cache = BuildCache(cache_dir)
    first, _ = cache.write(tone)
    touch(tone, 220)
    second, _ = cache.write(tone)

    assert first != second
    assert os.path.isfile(first)
    assert os.path.isfile(second)
    assert cache.report()["generated"] == 2


def test_two_patches_are_kept_apart(tone, cache_dir):
    cache = BuildCache(cache_dir)
    other = PatchDoc("other")
    other.add_object("osc~ 440", 0, 0, box_id="obj1")
    other.add_object("dac~", 0, 30, box_id="obj2")
    other.link("obj1", 0, "obj2", 0)

    cache.write(tone)
    cache.write(other)

    assert sorted(cache.paths) == ["other", "synth patch"]
    assert len(cache.existing()) == 2


def test_an_invalid_patch_never_reaches_the_disk(tone, cache_dir):
    cache = BuildCache(cache_dir)
    tone.add_object("", 90, 90)

    with pytest.raises(PatchError, match="has no text"):
        cache.write(tone)
    with pytest.raises(PatchError, match="has no text"):
        cache.path_for(tone)

    assert os.listdir(cache_dir) == []


def test_a_patch_that_only_warns_is_still_written(tone, cache_dir):
    tone.add_object("unloadable-thing", 90, 90)

    path, _text = BuildCache(cache_dir).write(tone)

    assert os.path.isfile(path)


# -- housekeeping -------------------------------------------------------


def test_existing_lists_only_generated_patches(cache_dir):
    cache = BuildCache(cache_dir)
    with open(os.path.join(cache_dir, "notes.txt"), "w", encoding="utf-8") as handle:
        handle.write("not a patch")
    with open(os.path.join(cache_dir, "b.pd"), "w", encoding="utf-8") as handle:
        handle.write("")
    with open(os.path.join(cache_dir, "a.pd"), "w", encoding="utf-8") as handle:
        handle.write("")

    assert [os.path.basename(p) for p in cache.existing()] == ["a.pd", "b.pd"]


def test_existing_is_empty_when_nothing_was_built_yet(tmp_path):
    cache = BuildCache(str(tmp_path / "not-made-yet"))

    assert list(cache.existing()) == []
    assert cache.prune() == 0


def test_prune_removes_what_this_run_did_not_use(tone, cache_dir):
    cache = BuildCache(cache_dir)
    used, _ = cache.write(tone)
    touch(tone, 220)
    stale, _ = cache.write(tone)
    assert cache.paths[tone.id] == stale

    removed = cache.prune(keep=[used])

    assert removed == 1
    assert not os.path.exists(stale)
    assert os.path.exists(used)
    assert cache.report()["pruned"] == 1


def test_prune_keeps_what_this_run_produced_by_default(tone, cache_dir):
    cache = BuildCache(cache_dir)
    _used, _text = cache.write(tone)
    with open(os.path.join(cache_dir, "stray.pd"), "w", encoding="utf-8") as handle:
        handle.write("#N canvas 0 0 1 1 12;\n")

    assert cache.prune() == 1
    assert list(cache.existing()) == [cache.paths[tone.id]]


def test_clear_forgets_everything(tone, cache_dir):
    cache = BuildCache(cache_dir)
    cache.write(tone)
    touch(tone, 220)
    cache.write(tone)

    removed = cache.clear()

    assert removed == 2
    assert list(cache.existing()) == []
    assert cache.paths == {}
    assert cache.report() == {"generated": 0, "reused": 0, "pruned": 2}
    assert cache.name_for(tone) not in os.listdir(cache_dir)


def test_repr_shows_where_the_cache_lives(cache_dir):
    assert cache_dir in repr(BuildCache(cache_dir))
    assert repr(BuildCache(cache_dir)).startswith("<BuildCache")

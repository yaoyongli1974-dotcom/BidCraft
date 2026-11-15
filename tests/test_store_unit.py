from __future__ import annotations

import pytest

from bidcraft.rules import store

VALID_NAMES = [
    "a",
    "abc",
    "weakcurrent-cabling",
    "a1-b2-c3",
    "a" + "b" * 63,  # 64 characters total: the upper bound
]

INVALID_NAMES = [
    "",
    "   ",
    "ABC",
    "1abc",
    "-abc",
    "abc_",
    "abc.def",
    "a/b",
    "../etc",
    "中文名称",
    "a" * 65,
]


@pytest.mark.parametrize("name", VALID_NAMES)
def test_validate_name_accepts_valid(db, name):
    assert store.validate_name(name) == name
    assert store.is_valid_name(name) is True


@pytest.mark.parametrize("name", INVALID_NAMES)
def test_validate_name_rejects_invalid(db, name):
    with pytest.raises(store.StoreError) as excinfo:
        store.validate_name(name)
    assert str(excinfo.value) == store.INVALID_NAME_MESSAGE
    assert store.is_valid_name(name) is False


def test_validate_name_strips_surrounding_space(db):
    assert store.validate_name("  demo-pack  ") == "demo-pack"


def test_save_and_remove_roundtrip(db):
    path = store.save_user_package("demo-pack", "version: 1\n")

    assert path == store.user_path("demo-pack")
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == "version: 1\n"
    assert store.has_user_override("demo-pack") is True

    assert store.remove_user_package("demo-pack") is True
    assert not path.exists()
    assert store.remove_user_package("demo-pack") is False


def test_save_is_atomic_on_replace_failure(db, monkeypatch):
    store.save_user_package("demo-pack", "old content\n")
    target = store.user_path("demo-pack")

    def boom(_src, _dst):
        raise OSError("disk full")

    monkeypatch.setattr(store.os, "replace", boom)
    with pytest.raises(OSError):
        store.save_user_package("demo-pack", "new content\n")

    # The previous file is untouched and the temporary file was cleaned up.
    assert target.read_text(encoding="utf-8") == "old content\n"
    assert {path.name for path in target.parent.iterdir()} == {"demo-pack.yaml"}


def test_read_hidden_tolerates_corrupt_json(db):
    hidden_file = store.user_dir(create=True) / store.HIDDEN_FILE

    hidden_file.write_text("{not json", encoding="utf-8")
    assert store.read_hidden() == set()

    hidden_file.write_text('["demo-pack", "BAD NAME", 3]', encoding="utf-8")
    assert store.read_hidden() == {"demo-pack"}

    hidden_file.write_text('{"hidden": ["demo-pack"]}', encoding="utf-8")
    assert store.read_hidden() == {"demo-pack"}

    hidden_file.write_text('{"hidden": "demo-pack"}', encoding="utf-8")
    assert store.read_hidden() == set()

    hidden_file.unlink()
    assert store.read_hidden() == set()


def test_hide_unhide_and_is_hidden(db):
    store.hide_package("demo-pack")
    assert store.is_hidden("demo-pack") is True
    assert store.read_hidden() == {"demo-pack"}

    store.hide_package("other-pack")
    assert store.read_hidden() == {"demo-pack", "other-pack"}

    store.unhide_package("demo-pack")
    assert store.is_hidden("demo-pack") is False
    assert store.read_hidden() == {"other-pack"}


def test_hide_rejects_invalid_name(db):
    with pytest.raises(store.StoreError):
        store.hide_package("../etc/passwd")

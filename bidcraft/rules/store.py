"""User rule-package overlay store.

Built-in packages live in ``bidcraft/resources/packages`` and are never written
to.  Every administrator change is persisted under ``BIDCRAFT_HOME/packages``:

* ``<name>.yaml`` shadows a built-in package with the same name, or defines a
  brand new custom package.
* ``hidden.json`` lists package names that were "deleted".  Hiding only removes
  a package from the new-project list; ``load_package`` keeps working so that
  existing projects continue to render.

All writes are atomic (temporary file + ``os.replace``) and are confined to the
user directory.  Package names are validated before they ever touch the
filesystem, which rules out path traversal.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from ..config import settings

BUILTIN_DIR = Path(__file__).resolve().parent.parent / "resources" / "packages"

NAME_PATTERN = r"^[a-z][a-z0-9-]{0,63}$"
_NAME_RE = re.compile(NAME_PATTERN)
HIDDEN_FILE = "hidden.json"

PROJECT_TYPE_LABELS = {
    "common": "通用",
    "weakcurrent": "弱电智能化",
    "fire": "消防工程",
    "installation": "安装工程",
    "civil": "土建工程",
    "hvac": "暖通空调",
    "decoration": "装饰装修",
    "other": "其他",
}


class StoreError(ValueError):
    """Invalid package name, missing package or unreadable store file."""


INVALID_NAME_MESSAGE = (
    "规则包名不合法：只能以小写字母开头，由小写字母、数字或连字符组成，最长 64 个字符"
)


def validate_name(name: str) -> str:
    """Return the normalised name or raise :class:`StoreError`."""
    value = (name or "").strip()
    if not _NAME_RE.fullmatch(value):
        raise StoreError(INVALID_NAME_MESSAGE)
    return value


def is_valid_name(name: str) -> bool:
    return bool(_NAME_RE.fullmatch((name or "").strip()))


def user_dir(*, create: bool = False) -> Path:
    path = Path(settings.home) / "packages"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def builtin_path(name: str) -> Path:
    return BUILTIN_DIR / f"{validate_name(name)}.yaml"


def user_path(name: str) -> Path:
    return user_dir() / f"{validate_name(name)}.yaml"


def is_builtin(name: str) -> bool:
    try:
        return builtin_path(name).is_file()
    except StoreError:
        return False


def has_user_override(name: str) -> bool:
    try:
        return user_path(name).is_file()
    except StoreError:
        return False


def package_path(name: str) -> Path:
    """Resolve a package name: user overlay first, then built-in resources."""
    validated = validate_name(name)
    override = user_dir() / f"{validated}.yaml"
    if override.is_file():
        return override
    builtin = BUILTIN_DIR / f"{validated}.yaml"
    if builtin.is_file():
        return builtin
    raise StoreError(f"找不到规则包: {validated}")


def package_exists(name: str) -> bool:
    try:
        package_path(name)
    except StoreError:
        return False
    return True


def read_document(name: str) -> str:
    try:
        return package_path(name).read_text(encoding="utf-8")
    except OSError as exc:
        raise StoreError(f"读取规则包失败: {name}（{exc}）") from exc


def save_user_package(name: str, document: str) -> Path:
    """Atomically write a user override/new package."""
    target = user_path(name)
    _atomic_write(target, document)
    return target


def remove_user_package(name: str) -> bool:
    path = user_path(name)
    if not path.is_file():
        return False
    path.unlink()
    return True


def list_builtin_names() -> list[str]:
    return sorted(
        path.stem for path in BUILTIN_DIR.glob("*.yaml") if _NAME_RE.fullmatch(path.stem)
    )


def list_user_names() -> list[str]:
    return sorted(
        path.stem for path in user_dir().glob("*.yaml") if _NAME_RE.fullmatch(path.stem)
    )


def all_names() -> list[str]:
    return sorted(set(list_builtin_names()) | set(list_user_names()))


def read_hidden() -> set[str]:
    path = user_dir() / HIDDEN_FILE
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A corrupt hidden list must not break the package list.
        return set()
    if isinstance(data, dict):
        data = data.get("hidden") or []
    if not isinstance(data, list):
        return set()
    return {str(item) for item in data if _NAME_RE.fullmatch(str(item))}


def write_hidden(names: set[str]) -> None:
    payload = json.dumps({"hidden": sorted(names)}, ensure_ascii=False, indent=2)
    _atomic_write(user_dir(create=True) / HIDDEN_FILE, payload + "\n")


def hide_package(name: str) -> None:
    hidden = read_hidden()
    hidden.add(validate_name(name))
    write_hidden(hidden)


def unhide_package(name: str) -> None:
    hidden = read_hidden()
    hidden.discard(validate_name(name))
    write_hidden(hidden)


def is_hidden(name: str) -> bool:
    return validate_name(name) in read_hidden()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

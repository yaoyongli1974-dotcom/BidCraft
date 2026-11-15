from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import store

PACKAGE_DIR = store.BUILTIN_DIR


class PackageError(ValueError):
    pass


@dataclass
class FieldSpec:
    key: str
    label: str
    group: str = ""
    aliases: list[str] = field(default_factory=list)
    required: bool = False
    type: str = "text"
    unit: str = ""
    max_len: int = 0
    reject_patterns: list[str] = field(default_factory=list)

    @property
    def all_labels(self) -> list[str]:
        return list(dict.fromkeys([self.label, *self.aliases]))


@dataclass
class OutlineItemSpec:
    heading: str
    intent: str = ""
    target_words: int = 1200
    needs: list[str] = field(default_factory=list)


@dataclass
class CheckSpec:
    id: str
    level: str = "major"
    category: str = ""
    assert_: str = ""
    message: str = ""
    recommendation: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class Package:
    name: str
    version: int = 1
    title: str = ""
    description: str = ""
    extends: list[str] = field(default_factory=list)
    parse: dict[str, Any] = field(default_factory=dict)
    fields: list[FieldSpec] = field(default_factory=list)
    outline: list[OutlineItemSpec] = field(default_factory=list)
    outline_mode: str = "replace"
    checks: list[CheckSpec] = field(default_factory=list)
    source_path: str = ""

    def field_map(self) -> dict[str, FieldSpec]:
        return {f.key: f for f in self.fields}

    def heading_patterns(self) -> list[str]:
        return list(self.parse.get("heading_patterns") or [])

    def table_hints(self) -> dict[str, list[str]]:
        value = self.parse.get("table_hints") or {}
        return {str(k): list(v or []) for k, v in value.items()}

    def keywords(self) -> list[str]:
        return list(self.parse.get("keywords") or [])

    def parameter_vocab(self) -> list[str]:
        return list(self.parse.get("parameter_vocab") or [])


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PackageError(f"{path.name} YAML 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise PackageError(f"{path.name} 顶层必须是对象")
    return data


def _parse_fields(items: Any) -> list[FieldSpec]:
    result: list[FieldSpec] = []
    for item in items or []:
        if not isinstance(item, dict) or not item.get("key"):
            raise PackageError(f"字段定义缺少 key: {item!r}")
        result.append(
            FieldSpec(
                key=str(item["key"]),
                label=str(item.get("label") or item["key"]),
                group=str(item.get("group") or ""),
                aliases=[str(a) for a in (item.get("aliases") or [])],
                required=bool(item.get("required", False)),
                type=str(item.get("type") or "text"),
                unit=str(item.get("unit") or ""),
                max_len=int(item.get("max_len") or 0),
                reject_patterns=[str(p) for p in (item.get("reject_patterns") or [])],
            )
        )
    return result


def _parse_outline(data: dict[str, Any]) -> tuple[list[OutlineItemSpec], str]:
    block = data.get("outline") or {}
    if isinstance(block, list):
        items, mode = block, "replace"
    else:
        items = block.get("items") or []
        mode = str(block.get("mode") or "replace")
    parsed = [
        OutlineItemSpec(
            heading=str(item.get("heading") or "").strip(),
            intent=str(item.get("intent") or ""),
            target_words=int(item.get("target_words") or 1200),
            needs=[str(n) for n in (item.get("needs") or [])],
        )
        for item in items
        if isinstance(item, dict) and str(item.get("heading") or "").strip()
    ]
    return parsed, mode


def _parse_checks(items: Any) -> list[CheckSpec]:
    result: list[CheckSpec] = []
    for item in items or []:
        if not isinstance(item, dict) or not item.get("id"):
            raise PackageError(f"核对项缺少 id: {item!r}")
        expression = item.get("assert") or item.get("assert_") or ""
        result.append(
            CheckSpec(
                id=str(item["id"]),
                level=str(item.get("level") or "major"),
                category=str(item.get("category") or ""),
                assert_=str(expression),
                message=str(item.get("message") or ""),
                recommendation=str(item.get("recommendation") or ""),
                evidence=dict(item.get("evidence") or {}),
            )
        )
    return result


def _deep_merge(base: Any, override: Any, *, list_mode: str = "replace") -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            if key in merged:
                merged[key] = _deep_merge(merged[key], value, list_mode=list_mode)
            else:
                merged[key] = value
        return merged
    if isinstance(base, list) and isinstance(override, list):
        if list_mode == "append":
            return base + [item for item in override if item not in base]
        return override
    return override


def _build_package(
    name: str, data: dict[str, Any], *, source_path: str = "", seen: tuple[str, ...] = ()
) -> Package:
    extends = [str(x) for x in (data.get("extends") or [])]
    if isinstance(data.get("extends"), str):
        extends = [str(data["extends"])]

    fields: list[FieldSpec] = []
    outline: list[OutlineItemSpec] = []
    outline_mode = "replace"
    checks: list[CheckSpec] = []
    parse: dict[str, Any] = {}

    for parent_name in extends:
        parent = load_package(parent_name, _seen=(*seen, name))
        parse = _merge_parse(parse, parent.parse)
        fields = _merge_fields(fields, parent.fields)
        checks = _merge_checks(checks, parent.checks)
        if parent.outline:
            outline, outline_mode = list(parent.outline), parent.outline_mode

    local_parse = data.get("parse") or {}
    if not isinstance(local_parse, dict):
        raise PackageError(f"{name}: parse 必须是对象")
    parse = _merge_parse(parse, local_parse)
    fields = _merge_fields(fields, _parse_fields(data.get("fields")))
    local_outline, local_mode = _parse_outline(data)
    if local_outline:
        if local_mode == "append":
            outline = outline + local_outline
        else:
            outline, outline_mode = local_outline, local_mode
    checks = _merge_checks(checks, _parse_checks(data.get("checks")))

    return Package(
        name=str(data.get("package") or name),
        version=int(data.get("version") or 1),
        title=str(data.get("title") or name),
        description=str(data.get("description") or ""),
        extends=extends,
        parse=parse,
        fields=fields,
        outline=outline,
        outline_mode=outline_mode,
        checks=checks,
        source_path=source_path,
    )


def load_package(name: str, *, _seen: tuple[str, ...] = ()) -> Package:
    if name in _seen:
        raise PackageError(f"包继承出现循环: {' -> '.join([*_seen, name])}")
    try:
        path = store.package_path(name)
    except store.StoreError as exc:
        raise PackageError(str(exc)) from exc
    data = _read_yaml(path)
    return _build_package(name, data, source_path=str(path), seen=_seen)


def _merge_parse(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    if not base:
        return dict(override or {})
    merged = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged[key])
            for nk, nv in value.items():
                if isinstance(nv, list) and isinstance(nested.get(nk), list):
                    nested[nk] = list(dict.fromkeys([*nested[nk], *nv]))
                else:
                    nested[nk] = nv
            merged[key] = nested
        elif isinstance(value, list) and isinstance(merged.get(key), list):
            merged[key] = list(dict.fromkeys([*merged[key], *value]))
        else:
            merged[key] = value
    return merged


def _merge_fields(base: list[FieldSpec], override: list[FieldSpec]) -> list[FieldSpec]:
    if not base:
        return list(override)
    merged = list(base)
    index = {item.key: i for i, item in enumerate(merged)}
    for item in override:
        if item.key in index:
            merged[index[item.key]] = item
        else:
            index[item.key] = len(merged)
            merged.append(item)
    return merged


def _merge_checks(base: list[CheckSpec], override: list[CheckSpec]) -> list[CheckSpec]:
    merged = list(base)
    index = {item.id: i for i, item in enumerate(merged)}
    for item in override:
        if item.id in index:
            merged[index[item.id]] = item
        else:
            index[item.id] = len(merged)
            merged.append(item)
    return merged


def _package_record(
    path: Path, fallback_name: str, *, builtin: bool, overridden: bool
) -> dict[str, Any]:
    try:
        data = _read_yaml(path)
    except PackageError as exc:
        return {
            "name": fallback_name,
            "title": "",
            "version": 1,
            "abstract": False,
            "extends": [],
            "path": str(path),
            "project_type": "other",
            "project_type_label": store.PROJECT_TYPE_LABELS["other"],
            "description": "",
            "builtin": builtin,
            "overridden": overridden,
            "hidden": False,
            "error": str(exc),
        }
    abstract = bool(data.get("abstract", False))
    project_type = str(data.get("project_type") or ("common" if abstract else "other"))
    label = str(
        data.get("project_type_label")
        or store.PROJECT_TYPE_LABELS.get(project_type)
        or store.PROJECT_TYPE_LABELS["other"]
    )
    extends = data.get("extends") or []
    if isinstance(extends, str):
        extends = [extends]
    return {
        "name": str(data.get("package") or fallback_name),
        "title": str(data.get("title") or ""),
        "version": data.get("version") or 1,
        "abstract": abstract,
        "extends": [str(item) for item in extends],
        "path": str(path),
        "project_type": project_type,
        "project_type_label": label,
        "description": str(data.get("description") or ""),
        "builtin": builtin,
        "overridden": overridden,
        "hidden": False,
        "error": "",
    }


def list_packages(include_hidden: bool = False) -> list[dict[str, Any]]:
    """Merge built-in packages with the user overlay.

    Hidden packages are excluded unless ``include_hidden`` is true.  A broken
    YAML file never raises: its record carries an ``error`` string instead.
    """
    hidden = store.read_hidden()
    entries: dict[str, dict[str, Any]] = {}
    for path in sorted(store.BUILTIN_DIR.glob("*.yaml")):
        if not store.is_valid_name(path.stem):
            continue
        entries[path.stem] = _package_record(
            path, path.stem, builtin=True, overridden=False
        )
    for path in sorted(store.user_dir().glob("*.yaml")):
        stem = path.stem
        if not store.is_valid_name(stem):
            continue
        shadowed = stem in entries
        entries[stem] = _package_record(
            path, stem, builtin=shadowed, overridden=shadowed
        )
    result: list[dict[str, Any]] = []
    for stem, record in entries.items():
        # ``hidden.json`` stores the filesystem/package name.  Accept either the
        # key (stem) or the YAML package field for hand-written files.
        record["hidden"] = stem in hidden or record["name"] in hidden
        if record["hidden"] and not include_hidden:
            continue
        result.append(record)
    result.sort(key=lambda item: (item.get("project_type") or "", item["name"]))
    return result


def _package_problems(package: Package) -> list[str]:
    problems: list[str] = []
    if not package.fields:
        problems.append("未定义任何字段")
    if not package.outline:
        problems.append("未定义章节大纲")
    for check in package.checks:
        if not check.assert_:
            problems.append(f"{check.id}: 缺少 assert 表达式")
        else:
            from .expr import ExpressionError, evaluate

            try:
                evaluate(check.assert_, _empty_context())
            except ExpressionError as exc:
                # 只有语法/白名单错误才是包的问题；运行时 KeyError 由上下文兜底。
                if "语法" in str(exc) or "不允许" in str(exc) or "只允许" in str(exc):
                    problems.append(f"{check.id}: {exc}")
    seen: set[str] = set()
    for item in package.outline:
        if item.heading in seen:
            problems.append(f"大纲章节重复: {item.heading}")
        seen.add(item.heading)
    return problems


def validate_package(name: str) -> list[str]:
    """Return human-readable problems; empty list means valid."""
    try:
        package = load_package(name)
    except PackageError as exc:
        return [str(exc)]
    return _package_problems(package)


def document_package_name(document: str) -> str:
    """Return the YAML ``package`` field without validating the document."""
    try:
        data = _parse_yaml_text(document)
    except PackageError:
        return ""
    return str(data.get("package") or "").strip()


def validate_package_text(document: str, name: str = "") -> dict[str, Any]:
    """Validate a full YAML document without writing it to disk.

    ``name`` is the package name the caller intends to save (for example the
    path segment of ``PUT /admin/rule-packages/{name}``).  When given, the
    YAML ``package`` field must match it.
    """
    summary = {"fields": 0, "outline": 0, "checks": 0}
    problems: list[str] = []
    try:
        data = _parse_yaml_text(document)
    except PackageError as exc:
        return {"ok": False, "problems": [str(exc)], "summary": summary}

    requested = (name or "").strip()
    document_name = str(data.get("package") or "").strip()
    effective_name = requested or document_name

    if requested:
        if not store.is_valid_name(requested):
            problems.append(store.INVALID_NAME_MESSAGE)
        if not document_name:
            problems.append("YAML 缺少 package 字段")
        elif document_name != requested:
            problems.append(
                f"YAML 中的 package 为 {document_name}，与要保存的包名 {requested} 不一致"
            )
    elif not document_name:
        problems.append("YAML 缺少 package 字段")
    elif not store.is_valid_name(document_name):
        problems.append(store.INVALID_NAME_MESSAGE)

    package: Package | None = None
    try:
        package = _build_package(effective_name or "unnamed", data)
    except (PackageError, ValueError, TypeError) as exc:
        problems.append(str(exc))

    if package is not None:
        summary = {
            "fields": len(package.fields),
            "outline": len(package.outline),
            "checks": len(package.checks),
        }
        problems.extend(_package_problems(package))

    return {"ok": not problems, "problems": problems, "summary": summary}


def _parse_yaml_text(document: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(document) or {}
    except yaml.YAMLError as exc:
        raise PackageError(f"YAML 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise PackageError("YAML 顶层必须是对象")
    return data


def _empty_context() -> dict[str, Any]:
    return {
        "facts": {},
        "materials": [],
        "parameters": [],
        "scoring": [],
        "requirements": [],
        "risks": [],
        "sections": [],
        "doc": "",
        "meta": {},
    }

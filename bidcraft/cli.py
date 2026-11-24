from __future__ import annotations

import json
import signal
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__, knowledge, model_config
from .config import settings
from .db import init_db, session_scope
from .export_gate import ExportGateError, enforce_export_gate
from .generation.agent import ModelClient
from .generation.outline import build_outline, load_outline, save_outline
from .generation.writer import generate_document
from .ledger import ledger_summary, load_ledger, update_fact
from .models import Delivery, Project
from .pipeline import add_source, check_project, create_project, parse_project, resolve_project
from .rules.engine import ledger_gate
from .rules.loader import list_packages, load_package, validate_package

app = typer.Typer(add_completion=False, help="BidCraft · 弱电投标文件编制系统")
console = Console()


def _install_shutdown_handlers() -> None:
    """SIGTERM/SIGINT 触发 worker 优雅退出（主线程外调用静默忽略）。"""

    from . import jobqueue

    def _handle(signum, frame) -> None:  # noqa: ANN001, ARG001
        console.print("[yellow]收到退出信号，正在优雅停止…[/yellow]")
        jobqueue.request_shutdown()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handle)
        except (ValueError, OSError):  # pragma: no cover - 非主线程/平台不支持
            pass


def _db():
    init_db()
    return session_scope()


def _generation_client(db) -> ModelClient:
    endpoint = model_config.resolve_chat_endpoint(db)
    return ModelClient(endpoint if endpoint is not None else settings)


def _knowledge_provider(db):
    def provide(query: str) -> str:
        return knowledge.context_block(knowledge.search(db, query, limit=4))

    return provide


def _project_or_exit(db, ref: str) -> Project:
    try:
        return resolve_project(db, ref)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)


def _image_resolver(project_id: str):
    from .images import url_resolver

    return url_resolver(project_id)


@app.command()
def version() -> None:
    """显示版本。"""
    console.print(f"BidCraft {__version__}")


@app.command()
def mcp(
    readonly: bool = typer.Option(
        False, "--readonly/--no-readonly", help="只读模式：禁用核对与导出"
    ),
    plugin_owner: str | None = typer.Option(
        None, "--plugin-owner", help="可见的个人插件归属用户 id（默认仅全局插件）"
    ),
    plugin_all: bool = typer.Option(
        False, "--plugin-all", help="按超管视角暴露全部插件（仅本机可信调用者使用）"
    ),
) -> None:
    """启动 MCP stdio 服务（供 Claude/Cursor 等客户端接入）。"""
    from . import mcp as mcp_server

    mode = "只读" if readonly else "读写"
    print(
        f"BidCraft MCP stdio 服务启动：{len(mcp_server.TOOL_SPECS)} 个工具，{mode}模式",
        file=sys.stderr,
        flush=True,
    )
    mcp_server.serve_stdio(
        readonly=readonly, plugin_owner=plugin_owner, plugin_all=plugin_all
    )


@app.command()
def doctor() -> None:
    """检查运行环境、数据库与模型端点配置。"""
    import shutil

    init_db()
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    from sqlalchemy import func, select

    from .models import KnowledgeBase, ModelConfig, User
    from .parsing.ocr import probe_ocr

    with session_scope() as db:
        users = db.scalar(select(func.count()).select_from(User)) or 0
        models = db.scalar(select(func.count()).select_from(ModelConfig)) or 0
        kbs = db.scalar(select(func.count()).select_from(KnowledgeBase)) or 0
        endpoint = model_config.resolve_chat_endpoint(db)
        embedding = model_config.resolve_embedding_endpoint(db)
    ocr = probe_ocr()
    if not settings.kb_vector_enabled:
        vector_label = "禁用（BIDCRAFT_KB_VECTOR=0）"
    elif embedding is not None:
        vector_label = f"可用（{embedding.model_name}）"
    else:
        vector_label = "未配置（关键词召回）"
    if not settings.ocr_enabled:
        ocr_label = "已禁用（BIDCRAFT_OCR=0）"
    elif ocr.available:
        ocr_label = f"可用（{'+'.join(ocr.languages)}，pdftoppm + tesseract）"
        if ocr.warning:
            ocr_label += f"；{ocr.warning}"
    else:
        ocr_label = f"不可用（{ocr.reason}）"
    rows = [
        ("BIDCRAFT_HOME", str(settings.home)),
        ("数据库", settings.db_url),
        (
            "登录与权限",
            f"已启用（{users} 个用户，RBAC）" if users else "未初始化（首次 serve 自动创建管理员）",
        ),
        ("对话模型", endpoint.label if endpoint else "（未配置，将使用确定性草稿）"),
        ("模型配置", f"{models} 个"),
        ("知识库", f"{kbs} 个"),
        ("向量检索", vector_label),
        ("离线模式", "是" if settings.offline else "否"),
        ("PDF 导出", soffice or "（未找到 LibreOffice，可仅导出 DOCX）"),
        ("扫描件 OCR", ocr_label),
        (
            "Web 客户端",
            "已构建" if (Path(__file__).resolve().parent / "web_dist" / "index.html").is_file() else "未构建（cd web && npm install && npm run build）",
        ),
    ]
    table = Table(title="环境检查")
    table.add_column("项目")
    table.add_column("值")
    for key, value in rows:
        table.add_row(key, value)
    console.print(table)
    problems = []
    for item in list_packages():
        problems.extend(f"{item['name']}: {p}" for p in validate_package(str(item["name"])))
    if problems:
        console.print("[red]规则包校验发现问题：[/red]")
        for problem in problems:
            console.print(f"  - {problem}")
    else:
        console.print("[green]规则包校验通过[/green]")
    console.print("[dim]零遥测：除显式配置的模型端点外，本程序不发起任何外部连接。[/dim]")


@app.command()
def calibrate(
    directory: Path = typer.Argument(..., help="招标文件目录"),
    output: Path | None = typer.Option(None, "--output", "-o", help="报告 JSON 输出路径"),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="递归扫描子目录"),
    save_baseline: Path | None = typer.Option(
        None, "--save-baseline", help="保存回归基线 JSON（仅 aggregate，跨机器可比）"
    ),
    compare_baseline: Path | None = typer.Option(
        None, "--compare-baseline", help="与回归基线比对（路径或索引标签，有回归 exit 1，基线缺失 exit 2）"
    ),
    label: str | None = typer.Option(None, "--label", help="基线标签（真实件批次名，写入溯源元数据）"),
    note: str | None = typer.Option(None, "--note", help="基线备注（仅溯源，不写招标文件正文）"),
    manifest: bool = typer.Option(
        True, "--manifest/--no-manifest", help="记录文件指纹清单（sha256/大小，不含正文）"
    ),
    baseline_index: Path | None = typer.Option(
        None, "--baseline-index", help="保存基线后登记到此索引（便于按标签比对）"
    ),
) -> None:
    """对真实招标文件目录做解析/字段回归校准（只读，不写数据库）。"""
    from .calibrate import calibrate_dir, index_add, render_report, resolve_baseline
    from .calibrate import compare_baseline as compare_baseline_report
    from .calibrate import save_baseline as save_baseline_file

    if not directory.is_dir():
        console.print(f"[red]目录不存在：{directory}[/red]")
        raise typer.Exit(1)

    report = calibrate_dir(directory, recursive=recursive)
    console.print(render_report(report))

    aggregate = report["aggregate"]
    console.print(
        f"文件 {aggregate['files']}（成功 {aggregate['ok']}，失败 {aggregate['failed']}）· "
        f"必填命中率 {aggregate['required_hit_rate']:.1%} · "
        f"平均材料 {aggregate['avg_materials']} · 需 OCR {aggregate['ocr_files']}"
    )

    hits = sorted(
        aggregate["field_hits"].items(), key=lambda pair: (-pair[1], pair[0])
    )
    if hits:
        table = Table(title="字段命中 Top 10")
        table.add_column("字段")
        table.add_column("命中文件数", justify="right")
        for key, count in hits[:10]:
            table.add_row(key, str(count))
        console.print(table)
    else:
        console.print("[yellow]没有任何字段命中。[/yellow]")

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        console.print(f"[green]报告已写入[/green] {output}")

    exit_code = 0
    compare_target: Path | None = compare_baseline
    if (
        compare_target is not None
        and not compare_target.exists()
        and baseline_index is not None
    ):
        try:
            compare_target = resolve_baseline(baseline_index, str(compare_target))
        except (KeyError, ValueError):
            pass  # 保持原路径，下面的 FileNotFoundError 会给出「基线文件不存在」
    if compare_target is not None:
        try:
            comparison = compare_baseline_report(report, compare_target)
        except FileNotFoundError:
            console.print(f"[red]基线文件不存在：{compare_target}[/red]")
            raise typer.Exit(2) from None
        except ValueError as exc:
            console.print(f"[red]基线文件无效：{exc}[/red]")
            raise typer.Exit(2) from None
        if comparison["regressions"] or comparison["improvements"]:
            table = Table(title="基线比对")
            table.add_column("结果")
            table.add_column("指标", overflow="fold")
            table.add_column("说明", overflow="fold")
            table.add_column("基线", justify="right")
            table.add_column("当前", justify="right")
            for item in comparison["regressions"]:
                table.add_row(
                    "[red]回归[/red]",
                    item["metric"],
                    item["message"],
                    str(item["baseline"]),
                    str(item["current"]),
                )
            for item in comparison["improvements"]:
                table.add_row(
                    "[green]改进[/green]",
                    item["metric"],
                    item["message"],
                    str(item["baseline"]),
                    str(item["current"]),
                )
            console.print(table)
        if comparison["ok"]:
            console.print("[green]基线比对通过：无回归[/green]")
        else:
            console.print(
                f"[red]基线比对发现 {len(comparison['regressions'])} 项回归[/red]"
            )
            exit_code = 1

    if save_baseline is not None:
        saved = save_baseline_file(
            report,
            save_baseline,
            label=label,
            note=note,
            manifest=manifest,
        )
        console.print(f"[green]基线已保存[/green] {saved}")
        if label:
            console.print(f"[dim]溯源标签：{label}[/dim]")
        if baseline_index is not None:
            entry = index_add(baseline_index, saved, label=label)
            console.print(
                f"[green]基线已登记[/green] {baseline_index} · {entry['id']} · {entry['label']}"
            )

    if exit_code:
        raise typer.Exit(exit_code)


@app.command("baselines")
def baselines_cmd(
    index: Path = typer.Option(
        Path("baselines/index.json"), "--index", help="真实件基线索引路径"
    ),
    list_items: bool = typer.Option(False, "--list", help="列出已登记的基线"),
    add: Path | None = typer.Option(None, "--add", help="登记一份已有的基线 JSON"),
    label: str | None = typer.Option(None, "--label", help="登记时指定标签"),
    verify: str | None = typer.Option(None, "--verify", help="按标签/id 核对真实件指纹"),
    root: Path | None = typer.Option(
        None, "--root", help="核对指纹时真实件所在目录（默认用当前目录）"
    ),
) -> None:
    """管理真实招标文件回归基线（索引里只有标签与路径，没有正文）。"""
    from .calibrate import index_add, load_index, resolve_baseline, verify_baseline

    if add is not None:
        try:
            entry = index_add(index, add, label=label)
        except FileNotFoundError:
            console.print(f"[red]基线文件不存在：{add}[/red]")
            raise typer.Exit(2) from None
        except ValueError as exc:
            console.print(f"[red]基线文件无效：{exc}[/red]")
            raise typer.Exit(2) from None
        console.print(
            f"[green]基线已登记[/green] {index} · {entry['id']} · {entry['label']}"
        )
        return

    if verify is not None:
        try:
            baseline_path = resolve_baseline(index, verify)
            result = verify_baseline(baseline_path, root=root)
        except (KeyError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from None
        if not result["has_manifest"]:
            console.print("[yellow]该基线没有文件指纹清单，无法核对真实件。[/yellow]")
            return
        table = Table(title=f"真实件指纹核对：{verify}")
        table.add_column("文件", overflow="fold")
        table.add_column("结果")
        table.add_column("说明", overflow="fold")
        for item in result["files"]:
            if item["ok"]:
                status, detail = "[green]一致[/green]", "-"
            elif item["missing"]:
                status, detail = "[red]缺失[/red]", "文件不存在"
            else:
                status, detail = "[red]变更[/red]", (
                    f"期望 {str(item['expected_sha256'])[:12]}… / "
                    f"实际 {str(item['actual_sha256'])[:12]}…"
                )
            table.add_row(item["file"], status, detail)
        console.print(table)
        if result["ok"]:
            console.print("[green]真实件指纹全部一致[/green]")
        else:
            console.print("[red]真实件已变更或缺失，请确认后重建基线[/red]")
            raise typer.Exit(1)
        return

    entries = load_index(index).get("entries", [])
    if not entries:
        console.print(f"[yellow]索引为空：{index}[/yellow]（用 --add 或 calibrate --baseline-index 登记）")
        return
    table = Table(title="真实件回归基线")
    table.add_column("标签")
    table.add_column("ID")
    table.add_column("路径", overflow="fold")
    table.add_column("保存时间")
    for entry in entries:
        table.add_row(
            str(entry.get("label", "")),
            str(entry.get("id", "")),
            str(entry.get("path", "")),
            str(entry.get("saved_at", "")),
        )
    console.print(table)
    if not list_items:
        console.print("[dim]默认展示列表；可用 --add 登记、--verify 核对真实件。[/dim]")


@app.command("packages")
def packages_cmd(validate: bool = typer.Option(False, "--validate", help="深度校验规则包")) -> None:
    """列出可用行业规则包（含管理员覆盖，隐藏包不显示）。"""
    table = Table(title="规则包")
    table.add_column("名称")
    table.add_column("类型")
    table.add_column("标题")
    table.add_column("继承")
    table.add_column("版本")
    for item in list_packages():
        table.add_row(
            str(item["name"]),
            str(item.get("project_type_label") or item.get("project_type") or "其他"),
            str(item["title"]),
            ", ".join(item.get("extends") or []) or "-",
            str(item["version"]),
        )
    console.print(table)
    if validate:
        bad = 0
        for item in list_packages():
            problems = validate_package(str(item["name"]))
            if problems:
                bad += 1
                console.print(f"[red]{item['name']}[/red]")
                for problem in problems:
                    console.print(f"  - {problem}")
        if not bad:
            console.print("[green]全部规则包校验通过[/green]")


@app.command()
def init(
    name: str = typer.Argument(..., help="项目名称"),
    package: str = typer.Option("weakcurrent-cabling", "--package", "-p", help="行业规则包"),
) -> None:
    """创建一个投标项目。"""
    with _db() as db:
        try:
            project = create_project(db, name, package)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
        console.print(f"[green]已创建项目[/green] {project.id}  {project.name}  ({project.package})")


@app.command()
def projects() -> None:
    """列出项目。"""
    with _db() as db:
        from sqlalchemy import select

        rows = list(db.scalars(select(Project).order_by(Project.created_at.desc())))
        table = Table(title="投标项目")
        for column in ("ID", "名称", "规则包", "状态", "创建时间"):
            table.add_column(column)
        for project in rows:
            table.add_row(
                project.id[:8],
                project.name,
                project.package,
                project.status,
                project.created_at.strftime("%Y-%m-%d %H:%M"),
            )
        console.print(table)


@app.command()
def ingest(
    file: Path = typer.Argument(..., exists=True, help="招标文件 PDF/DOCX/TXT/XLSX"),
    name: str = typer.Option("", "--name", "-n", help="项目名称（默认取文件名）"),
    package: str = typer.Option("weakcurrent-cabling", "--package", "-p"),
    project: str = typer.Option("", "--project", help="导入到已有项目 ID/名称"),
) -> None:
    """导入招标文件并抽取事实台账。"""
    with _db() as db:
        if project:
            target = _project_or_exit(db, project)
        else:
            target = create_project(db, name or file.stem, package)
        add_source(db, target, file)
        try:
            ledger, info = parse_project(db, target.id)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]解析失败：{exc}[/red]")
            raise typer.Exit(1)
        db.commit()
        summary = ledger_summary(ledger)
        console.rule(f"事实台账 · {target.name}")
        console.print(
            f"字段 {summary['fields_filled']}/{summary['fields_total']} 已填，"
            f"必填缺失 {summary['missing_required']}，"
            f"材料 {summary['materials']}，参数 {summary['parameters']}，"
            f"评分项 {summary['scoring']}，要求 {summary['requirements']}，风险 {summary['risks']}"
        )
        for note in info["notes"]:
            console.print(f"[yellow]{note}[/yellow]")
        _print_facts(ledger)
        console.print(f"\n项目 ID：{target.id}")


def _print_facts(ledger) -> None:
    table = Table(title="关键事实")
    table.add_column("字段")
    table.add_column("取值")
    table.add_column("状态")
    for item in ledger.facts:
        if not item.required and not item.value:
            continue
        mark = "✓" if item.value else "缺"
        color = "green" if item.value else "red"
        table.add_row(item.label, item.value or "—", f"[{color}]{mark}[/{color}]")
    console.print(table)


@app.command()
def show(ref: str = typer.Argument(..., help="项目 ID/名称")) -> None:
    """查看项目事实台账。"""
    with _db() as db:
        project = _project_or_exit(db, ref)
        ledger = load_ledger(db, project.id)
        _print_facts(ledger)
        if ledger.scoring:
            table = Table(title="评分项")
            table.add_column("评分因素")
            table.add_column("分值")
            table.add_column("标准")
            for item in ledger.scoring:
                table.add_row(item.name, "" if item.score is None else f"{item.score:g}", item.criteria[:60])
            console.print(table)
        console.print(
            f"\n材料 {len(ledger.materials)} · 参数 {len(ledger.parameters)} · "
            f"要求 {len(ledger.requirements)} · 风险 {len(ledger.risks)}"
        )


@app.command()
def fact(
    ref: str = typer.Argument(..., help="项目 ID/名称"),
    key: str = typer.Argument(..., help="事实 key，例如 project.name"),
    value: str = typer.Option(..., "--value", "-v", help="人工确认后的取值"),
) -> None:
    """人工修正一条事实（抽取结果必须可校订）。"""
    with _db() as db:
        project = _project_or_exit(db, ref)
        try:
            row = update_fact(db, project.id, key, value=value, status="confirmed")
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
        console.print(f"[green]{row.label}[/green] = {row.value}（已确认）")


@app.command()
def outline(
    ref: str = typer.Argument(..., help="项目 ID/名称"),
    mode: str = typer.Option("auto", "--mode", help="auto|package|scoring|tender-format"),
    total_words: int = typer.Option(30000, "--words", help="评分对齐时的全书目标字数"),
    force: bool = typer.Option(False, "--force", help="覆盖已有大纲与正文"),
) -> None:
    """生成章节大纲。"""
    with _db() as db:
        project = _project_or_exit(db, ref)
        ledger = load_ledger(db, project.id)
        existing = load_outline(db, project.id)
        if existing and not force:
            console.print("[yellow]已有大纲，如需重建请加 --force[/yellow]")
            _print_outline(existing)
            return
        package = load_package(project.package)
        spec = build_outline(ledger, package, mode=mode, total_words=total_words)
        save_outline(db, project.id, spec)
        db.flush()
        rows = load_outline(db, project.id)
        console.print(f"[green]已生成 {len(rows)} 章大纲（{spec.mode}）[/green]")
        _print_outline(rows)


def _print_outline(rows) -> None:
    table = Table(title="章节大纲")
    table.add_column("#", width=4)
    table.add_column("章节")
    table.add_column("目标字数", justify="right")
    table.add_column("状态")
    for index, row in enumerate(rows, start=1):
        table.add_row(str(index), row["heading"], str(row["target_words"]), row["status"])
    console.print(table)


@app.command()
def write(
    ref: str = typer.Argument(..., help="项目 ID/名称"),
    stream: bool = typer.Option(False, "--stream", help="流式输出到终端"),
    force: bool = typer.Option(False, "--force", help="重写已生成章节"),
) -> None:
    """按大纲生成正文（模型不可用时回落到确定性草稿）。"""
    with _db() as db:
        project = _project_or_exit(db, ref)
        ledger = load_ledger(db, project.id)
        problems = ledger_gate(ledger)
        if problems:
            console.print("[red]事实台账未就绪，拒绝生成：[/red]")
            for problem in problems:
                console.print(f"  - {problem}")
            console.print("[dim]用 `bidcraft fact <项目> <key> -v <值>` 补齐必填事实。[/dim]")
            raise typer.Exit(1)
        if force:
            from sqlalchemy import delete

            from .models import Section

            nodes = load_outline(db, project.id)
            ids = [node["id"] for node in nodes]
            if ids:
                db.execute(delete(Section).where(Section.node_id.in_(ids)))
            for node in nodes:
                db.add(Section(node_id=node["id"], body="", status="pending", words=0))
            db.flush()

        package = load_package(project.package)
        if not load_outline(db, project.id):
            spec = build_outline(ledger, package)
            save_outline(db, project.id, spec)
            db.flush()
            console.print(f"[green]已自动生成大纲 {len(spec.items)} 章（{spec.mode}）[/green]")

        client = _generation_client(db)
        if not client.available:
            console.print("[yellow]未配置模型端点，使用确定性草稿模式（可正常出稿，质量有限）。[/yellow]")

        def on_progress(info):
            console.print(
                f"[cyan]{info['progress']:3d}%[/cyan] {info['heading']} "
                f"({info['done']}/{info['total']})"
            )

        def on_delta(delta):
            console.print(delta, end="")

        report = generate_document(
            db,
            project.id,
            client,
            ledger,
            package,
            stream=stream,
            on_delta=on_delta,
            on_progress=on_progress,
            only_node=None,
            knowledge_provider=_knowledge_provider(db),
        )
        project.status = "drafting"
        db.commit()
        console.print(
            f"\n[green]完成 {len(report.written)} 章[/green]，"
            f"回落草稿 {len(report.fallback)} 章，失败 {len(report.failed)} 章"
        )
        for failure in report.failed:
            console.print(f"[red]{failure['heading']}: {failure['error']}[/red]")
        if report.suspicious:
            console.print("[yellow]疑似台账外数字（请人工复核）：[/yellow]")
            for heading, numbers in report.suspicious.items():
                console.print(f"  - {heading}: {', '.join(numbers)}")


@app.command()
def check(ref: str = typer.Argument(..., help="项目 ID/名称")) -> None:
    """执行废标项/参数响应/一致性核对。"""
    with _db() as db:
        project = _project_or_exit(db, ref)
        results, summary = check_project(db, project.id)
        db.commit()
        table = Table(title=f"核对结果 · {summary['verdict']}")
        table.add_column("编号")
        table.add_column("级别")
        table.add_column("类别")
        table.add_column("问题")
        table.add_column("位置")
        for result in results:
            color = {"blocking": "red", "critical": "red", "major": "yellow"}.get(result.level, "white")
            table.add_row(
                result.code,
                f"[{color}]{result.level}[/{color}]",
                result.category,
                result.message,
                result.location,
            )
        console.print(table)
        console.print(
            f"失败 {summary['failed']} 项，分布 {summary['by_level']}；结论：{summary['verdict']}"
        )


@app.command()
def export(
    ref: str = typer.Argument(..., help="项目 ID/名称"),
    fmt: str = typer.Option("docx", "--format", "-f", help="docx|md|pdf|xlsx"),
    output: Path = typer.Option(None, "--output", "-o"),
    force: bool = typer.Option(False, "--force", help="存在未处置废标/阻断风险时强制导出"),
    force_reason: str = typer.Option(
        "", "--force-reason", help="强制导出原因（去空白后不少于 4 个字）"
    ),
) -> None:
    """导出 DOCX / Markdown / PDF / Excel。"""
    from .export import templates as export_templates
    from .export.docx import export_docx, export_markdown
    from .images import get_policy

    with _db() as db:
        project = _project_or_exit(db, ref)
        # 交付硬门禁与 HTTP/MCP/CLI 共用 export_gate；CLI 按本地操作者（等同 owner）处理。
        try:
            enforce_export_gate(
                db,
                project.id,
                fmt,
                force=force,
                force_reason=force_reason,
                source="cli",
            )
        except ExportGateError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        ledger = load_ledger(db, project.id)
        nodes = load_outline(db, project.id)
        template = export_templates.load(project)
        image_policy = get_policy(project)
        if fmt not in {"xlsx"} and (not nodes or not any(node.get("body") for node in nodes)):
            console.print("[red]还没有正文，先运行 bidcraft write[/red]")
            raise typer.Exit(1)
        suffix = {"docx": "docx", "md": "md", "markdown": "md", "pdf": "pdf", "xlsx": "xlsx"}.get(fmt)
        if suffix is None:
            console.print("[red]--format 只能是 docx、md、pdf 或 xlsx[/red]")
            raise typer.Exit(1)
        target = output or (settings.project_dir(project.id) / "output" / f"bid.{suffix}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "docx":
            path = export_docx(
                target,
                ledger,
                nodes,
                image_resolver=_image_resolver(project.id),
                image_policy=image_policy,
                template=template,
            )
        elif fmt in {"md", "markdown"}:
            path = export_markdown(target, ledger, nodes)
        elif fmt == "xlsx":
            from .export.xlsx import export_xlsx

            path = export_xlsx(target, ledger)
        else:
            from .export.pdf import PdfExportError, export_pdf

            docx_path = target.with_suffix(".docx")
            export_docx(
                docx_path,
                ledger,
                nodes,
                image_resolver=_image_resolver(project.id),
                image_policy=image_policy,
                template=template,
            )
            try:
                path = export_pdf(docx_path, target)
            except PdfExportError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(2) from exc
        db.add(Delivery(project_id=project.id, file_format=fmt, file_path=str(path), status="done"))
        project.status = "delivery"
        db.commit()
        console.print(f"[green]已导出[/green] {path}")


@app.command()
def boq(
    ref: str = typer.Argument(..., help="项目 ID/名称"),
    file: Path = typer.Argument(..., exists=True, help="工程量清单 .xlsx"),
    sheet: str = typer.Option("", "--sheet", help="指定工作表名称（默认取清单行最多的工作表）"),
) -> None:
    """导入招标工程量清单并与台账材料对齐。"""
    from .boq import build_payload, import_boq

    with _db() as db:
        project = _project_or_exit(db, ref)
        try:
            snapshot = import_boq(db, project, file, sheet=sheet or None)
        except Exception as exc:
            console.print(f"[red]清单导入失败：{exc}[/red]")
            raise typer.Exit(1) from exc
        payload = build_payload(db, project.id)
        db.commit()
        summary = payload["summary"]
        console.rule(f"工程量清单 · {project.name}")
        console.print(
            f"工作表 {snapshot.sheet}（表头第 {snapshot.header_row} 行），"
            f"清单条目 {summary['boq_rows']}，已匹配 {summary['matched']}，"
            f"差异 {summary['total_diffs']}（数量 {summary['qty_diff']} / "
            f"单位 {summary['unit_diff']} / 规格 {summary['spec_diff']}），"
            f"清单有台账无 {summary['missing_in_ledger']}，"
            f"台账有清单无 {summary['missing_in_boq']}，"
            f"匹配率 {summary['match_rate']:.1%}；"
            f"报价 已报价 {summary['priced']} / 未报价 {summary['unpriced']}，"
            f"合计 {summary['total_amount']:,.2f} 元"
        )
        for warning in snapshot.warnings or []:
            console.print(f"[yellow]{warning}[/yellow]")
        pending = [
            row for row in payload["rows"] if row["status"] not in {"matched"} and not row["ignored"]
        ]
        if pending:
            table = Table(title=f"待对齐（{len(pending)} 行，显示前 20 行）")
            table.add_column("行")
            table.add_column("名称")
            table.add_column("清单规格")
            table.add_column("数量", justify="right")
            table.add_column("状态")
            for row in pending[:20]:
                table.add_row(
                    str(row["boq_row"] or "-"),
                    row["name"],
                    row["spec"] or "-",
                    "" if row["qty"] is None else f"{row['qty']:g}",
                    row["status"],
                )
            console.print(table)
        else:
            console.print("[green]清单与台账已全部一致[/green]")
        console.print(f"\n项目 ID：{project.id}")


@app.command("boq-price")
def boq_price(
    ref: str = typer.Argument(..., help="项目 ID/名称"),
    boq_row: int = typer.Argument(..., help="清单行号"),
    unit_price: float | None = typer.Argument(None, help="单价（元）；用 --clear 清除"),
    clear: bool = typer.Option(False, "--clear", help="清除该行单价"),
    package: str | None = typer.Option(
        None, "--package", help="包件 ID/名称（多包件项目必填；省略则操作默认清单）"
    ),
) -> None:
    """设置或清除某条清单的单价（报价按清单数量计算合价）。"""
    from .services.quote_service import set_unit_price as service_set_unit_price

    if clear is False and unit_price is None:
        console.print("[red]请提供单价，或使用 --clear 清除已有单价[/red]")
        raise typer.Exit(1)
    with _db() as db:
        project = _project_or_exit(db, ref)
        try:
            # 与 HTTP 共用同一编排：透传 package_id 修复多包件项目改价落空的 bug。
            # CLI 无请求用户，actor 省略 → 审计记 system（既有语义）。
            payload = service_set_unit_price(
                db,
                project,
                boq_row,
                None if clear else unit_price,
                package_id=package,
            )
        except Exception as exc:
            console.print(f"[red]设置单价失败：{exc}[/red]")
            raise typer.Exit(1) from exc
        db.commit()
        summary = payload["summary"]
        console.print(
            f"[green]{'已清除' if clear else '已设置'}[/green] 第 {boq_row} 行单价；"
            f"已报价 {summary['priced']} / 未报价 {summary['unpriced']}，"
            f"合计 {summary['total_amount']:,.2f} 元"
        )


@app.command("boq-export")
def boq_export(
    ref: str = typer.Argument(..., help="项目 ID/名称"),
    output: Path = typer.Option(None, "--output", "-o", help="另存路径"),
) -> None:
    """导出工程量清单响应表（.xlsx）。"""
    from .boq import build_payload
    from .export.boq_xlsx import export_boq_xlsx

    with _db() as db:
        project = _project_or_exit(db, ref)
        payload = build_payload(db, project.id)
        if payload["snapshot"] is None:
            console.print("[red]尚未上传工程量清单，请先运行 bidcraft boq[/red]")
            raise typer.Exit(1)
        target = output or (settings.project_dir(project.id) / "output" / "boq-response.xlsx")
        path = export_boq_xlsx(target, payload)
        db.commit()
        console.print(f"[green]已导出[/green] {path}")


@app.command()
def run(
    file: Path = typer.Argument(..., exists=True, help="招标文件 PDF/DOCX/TXT/XLSX"),
    name: str = typer.Option("", "--name", "-n"),
    package: str = typer.Option("weakcurrent-cabling", "--package", "-p"),
    mode: str = typer.Option("auto", "--mode"),
    no_write: bool = typer.Option(False, "--no-write", help="只解析并建大纲，不生成正文"),
    force: bool = typer.Option(False, "--force", help="存在未处置废标/阻断风险时强制导出 DOCX"),
    force_reason: str = typer.Option(
        "", "--force-reason", help="强制导出原因（去空白后不少于 4 个字）"
    ),
) -> None:
    """一条命令跑通：导入 → 台账 → 大纲 → 正文 → 核对 → DOCX。"""
    with _db() as db:
        project = create_project(db, name or file.stem, package)
        add_source(db, project, file)
        ledger, info = parse_project(db, project.id)
        db.commit()
        summary = ledger_summary(ledger)
        console.print(
            f"[green]解析完成[/green] 字段 {summary['fields_filled']}/{summary['fields_total']}，"
            f"材料 {summary['materials']}，参数 {summary['parameters']}，评分项 {summary['scoring']}"
        )
        for note in info["notes"]:
            console.print(f"[yellow]{note}[/yellow]")

        problems = ledger_gate(ledger)
        if problems and not no_write:
            console.print("[red]必填事实缺失，拒绝生成正文：[/red]")
            for problem in problems:
                console.print(f"  - {problem}")
            console.print(f"[dim]项目 ID：{project.id}；补齐后运行 bidcraft write {project.id}[/dim]")
            raise typer.Exit(1)

        pkg = load_package(project.package)
        spec = build_outline(ledger, pkg, mode=mode)
        save_outline(db, project.id, spec)
        db.flush()
        console.print(f"[green]大纲 {len(spec.items)} 章（{spec.mode}）[/green]")
        if no_write:
            db.commit()
            return

        client = _generation_client(db)
        if not client.available:
            console.print("[yellow]未配置模型端点，使用确定性草稿。[/yellow]")
        report = generate_document(
            db,
            project.id,
            client,
            ledger,
            pkg,
            knowledge_provider=_knowledge_provider(db),
        )
        project.status = "drafting"
        db.commit()
        console.print(f"[green]正文 {len(report.written)} 章完成[/green]")

        _, verdict = check_project(db, project.id)
        db.commit()
        console.print(f"核对未通过 {verdict['failed']} 项，结论 {verdict['verdict']}")

        from .export import templates as export_templates
        from .export.docx import export_docx
        from .images import get_policy

        # 一条命令跑到 DOCX 也是真实导出路径，同样过交付硬门禁（按本地操作者处理）。
        # 但 `run` 本身没有复核环节，章节确认在这里不适用（人工检查点在交互式导出）。
        try:
            enforce_export_gate(
                db,
                project.id,
                "docx",
                force=force,
                force_reason=force_reason,
                source="cli",
                require_confirmed_chapters=False,
            )
        except ExportGateError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc

        target = settings.project_dir(project.id) / "output" / "bid.docx"
        path = export_docx(
            target,
            ledger,
            load_outline(db, project.id),
            image_resolver=_image_resolver(project.id),
            image_policy=get_policy(project),
            template=export_templates.load(project),
        )
        db.add(Delivery(project_id=project.id, file_format="docx", file_path=str(path), status="done"))
        db.commit()
        console.print(f"[green]已导出[/green] {path}")
        console.print(f"项目 ID：{project.id}")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    open_browser: bool = typer.Option(False, "--open", "-o", help="启动后打开浏览器"),
) -> None:
    """启动 B/S 服务：浏览器客户端 + HTTP API（需安装 api 扩展）。"""
    try:
        import uvicorn
    except ImportError as exc:
        console.print("[red]缺少可选依赖：uv pip install -e '.[api]'[/red]")
        raise typer.Exit(2) from exc
    from .api import create_app

    app = create_app()
    # 单进程模式：API 与 worker 同进程。恢复旧租约 + 启动常驻 worker（DB 认领 + 租约心跳）。
    # 分离部署请改用 `bidcraft worker`，并设置 BIDCRAFT_EMBEDDED_WORKERS=0 让 API 只入队。
    from . import jobqueue

    jobqueue.recover_on_startup()
    jobqueue.start_workers()
    # uvicorn 安装自己的信号处理器；应用层的 shutdown 钩子（create_app）负责在
    # HTTP 优雅关闭时 drain worker。这里额外注册，覆盖 uvicorn 退出后的窗口。
    _install_shutdown_handlers()
    web_ready = (Path(__file__).resolve().parent / "web_dist" / "index.html").is_file()
    console.print(f"[green]BidCraft[/green] http://{host}:{port}  API 文档 /docs")
    if web_ready:
        console.print("[dim]浏览器客户端已就绪；局域网部署请加 --host 0.0.0.0[/dim]")
    else:
        console.print(
            "[yellow]Web 客户端未构建：cd web && npm install && npm run build[/yellow]"
        )
    if host in {"0.0.0.0", "::"}:
        console.print(
            "[yellow]局域网模式已启用登录与 RBAC；公网访问仍须使用 HTTPS"
            "（BIDCRAFT_SECURE_COOKIE=1）或置于 TLS 反向代理之后。[/yellow]"
        )
    initial = settings.home / "initial-admin.txt"
    if initial.is_file():
        console.print(f"[yellow]初始管理员凭据：{initial}（登录后请立即修改）[/yellow]")
    if open_browser:
        import threading
        import webbrowser

        url = f"http://{host if host not in {'0.0.0.0', '::'} else '127.0.0.1'}:{port}/"
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    # uvicorn 的 ProxyHeadersMiddleware 即使 trust_proxy=0 也会默认信任来自
    # 127.0.0.1 的 X-Forwarded-*，在应用中间件之前改写 request.client，导致
    # 本地伪造 IP 绕过限流/审计。是否启用转发头统一由应用侧的 trust_proxy 决定。
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        proxy_headers=settings.trust_proxy,
        forwarded_allow_ips="*" if settings.trust_proxy else "",
    )


@app.command("worker")
def worker() -> None:
    """独立任务 Worker：认领并执行 Job（与 API 进程分离部署）。

    启动流程：初始化库 → 恢复已过期的中断任务 → 拉起认领 worker 与独立 reaper。
    进程常驻，Ctrl-C / SIGTERM 退出；运行中的任务靠租约超时由下一个 worker 回收。

    典型分离部署：
        # API（可多实例，BIDCRAFT_EMBEDDED_WORKERS=0 时不拉起本地 worker、不执行恢复）
        BIDCRAFT_EMBEDDED_WORKERS=0 uvicorn bidcraft.api:app --host 0.0.0.0 --port 8000 --workers 4
        # Worker（1..N 个进程，各自原子认领；只有一个需要跑恢复，多跑也只动已过期租约）
        bidcraft worker
    """
    init_db()
    from . import jobqueue

    requeued, failed = jobqueue.recover_on_startup()
    jobqueue.start_workers()
    _install_shutdown_handlers()
    console.print(
        f"[green]BidCraft Worker[/green] 已启动（恢复 {requeued} 项，失败 {failed} 项），等待任务…"
    )
    try:
        while not jobqueue.is_shutdown_requested():
            time.sleep(0.5)
    except KeyboardInterrupt:  # pragma: no cover - 交互式退出
        jobqueue.request_shutdown()
    # 等待在途任务在检查点收尾（有上限，不会永久阻塞退出）。
    jobqueue.wait_for_workers(timeout=settings.shutdown_grace_seconds)
    console.print("[yellow]Worker 已优雅停止[/yellow]")


@app.command("reconcile-charges")
def reconcile_charges(
    older_than_seconds: int | None = typer.Option(
        None, "--older-than", help="覆盖 TTL（秒）；默认 BIDCRAFT_PENDING_CHARGE_TTL"
    ),
) -> None:
    """对账崩溃残留的未结扣费标记（建议由 cron/运维周期调用）。

    只退还在 ``pending_charges`` 里停留超过 TTL 的 marker；行级条件更新保证
    多实例并发调用时不会重复退款。worker 的 reaper 也会周期做同一件事，本命令
    用于手动核查、排障或在未启动 worker 的部署里定时执行。
    """

    from . import jobqueue

    init_db()
    with session_scope() as db:
        settled = jobqueue.reconcile_pending_charges(
            older_than_seconds=older_than_seconds, db=db
        )
    typer.echo(f"扣费对账完成：已结清 {settled} 条未结扣费标记")


@app.command("reset-password")
def reset_password(
    username: str = typer.Argument(..., help="用户名"),
    password: str = typer.Option("", "--password", help="新密码；省略则交互输入"),
) -> None:
    """重置本地账号密码（忘记管理员密码时的离线恢复方式）。"""
    from sqlalchemy import select

    from .auth import revoke_user_sessions
    from .models import User
    from .security import hash_password

    init_db()
    with session_scope() as db:
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            console.print(f"[red]用户不存在：{username}[/red]")
            raise typer.Exit(1)
        if not password:
            password = typer.prompt("新密码", hide_input=True, confirmation_prompt=True)
        if len(password) < 8:
            console.print("[red]密码至少 8 位[/red]")
            raise typer.Exit(1)
        user.password_hash = hash_password(password)
        revoke_user_sessions(db, user.id)
        console.print(f"[green]已重置 {user.username} 的密码，所有会话已失效。[/green]")


@app.command("ledger-json")
def ledger_json(ref: str = typer.Argument(...)) -> None:
    """输出事实台账 JSON（供外部系统/人工校订）。"""
    with _db() as db:
        project = _project_or_exit(db, ref)
        ledger = load_ledger(db, project.id)
        console.print_json(ledger.model_dump_json())


@app.command("schema")
def schema_cmd(
    out: Path | None = typer.Option(None, "--out", help="另存路径；默认打印内置 C1 Schema"),
) -> None:
    """输出事实台账 JSON Schema（C1 契约）。"""
    from .tools.dump_schema import SCHEMA_PATH, build_schema

    if out is None:
        console.print_json(json.dumps(build_schema(), ensure_ascii=False))
        return
    path = json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(path, encoding="utf-8")
    console.print(f"[green]已写入[/green] {out}")
    console.print(f"内置契约：{SCHEMA_PATH}")


def _format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _backup_data_files(home: Path, directories: list[str]) -> list[tuple[Path, str]]:
    """收集需要随备份打包的数据文件，返回 ``(源文件, zip 内相对路径)``。

    只允许 home 之内的目录，避免配置错误把整台机器的文件塞进备份；保留
    ``backups/`` 防止备份递归膨胀。清单元数据里的路径始终相对 BIDCRAFT_HOME，
    旧备份恢复时按需手动解压即可。
    """

    home = home.resolve()
    reserved = {"bidcraft.db", "dump.sql", "secret.key", "manifest.json"}
    seen: set[str] = set()
    found: list[tuple[Path, str]] = []
    for name in directories:
        candidate = (home / name.strip().strip("/\\")).resolve()
        if candidate == home or home not in candidate.parents or not candidate.is_dir():
            continue
        for path in sorted(candidate.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            arcname = path.resolve().relative_to(home).as_posix()
            if arcname in reserved or arcname.startswith("backups/"):
                continue
            if arcname in seen:
                continue
            seen.add(arcname)
            found.append((path, arcname))
    return found


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _print_backups(directory: Path) -> None:
    from datetime import datetime

    items = (
        sorted(directory.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        if directory.is_dir()
        else []
    )
    if not items:
        console.print(f"[dim]还没有备份：{directory}[/dim]")
        return
    table = Table(title=f"备份 · {directory}")
    table.add_column("文件")
    table.add_column("大小", justify="right")
    table.add_column("修改时间")
    for item in items:
        stat = item.stat()
        table.add_row(
            item.name,
            _format_bytes(stat.st_size),
            datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        )
    console.print(table)


@app.command()
def backup(
    output: Path | None = typer.Option(
        None, "--output", "-o", help="输出目录（自动命名）或 .zip 文件路径"
    ),
    list_existing: bool = typer.Option(False, "--list", help="列出已有备份后退出"),
    include_dir: list[str] = typer.Option(
        [], "--include-dir", help="额外打包的数据目录（相对 BIDCRAFT_HOME，可重复）"
    ),
) -> None:
    """在线备份数据库、secret.key 与项目数据目录（一致性快照打包为 zip）。

    SQLite 走在线备份 API；PostgreSQL 走 pg_dump（需 postgresql-client），
    口令通过 PGPASSWORD 传递，不进入命令行参数。默认数据目录由
    BIDCRAFT_BACKUP_DIRS 指定（projects,packages,output,docs）。
    """
    import os
    import shutil
    import sqlite3
    import subprocess
    import tempfile
    import zipfile
    from datetime import UTC, datetime

    from sqlalchemy.engine import make_url

    backup_dir = settings.home / "backups"
    if list_existing:
        _print_backups(backup_dir)
        return

    is_pg = settings.is_postgres
    db_path = settings.home / "bidcraft.db"
    if not is_pg and not db_path.is_file():
        console.print(f"[red]数据库不存在：{db_path}[/red]")
        console.print("[dim]请先运行 bidcraft init 或 bidcraft doctor 初始化。[/dim]")
        raise typer.Exit(1)
    if is_pg and not shutil.which("pg_dump"):
        console.print("[red]PostgreSQL 备份需要 pg_dump，请先安装 postgresql-client。[/red]")
        raise typer.Exit(1)

    from . import clock

    stamp = clock.now().strftime("%Y%m%d-%H%M%S")
    default_name = f"bidcraft-{stamp}.zip"
    if output is None:
        target = backup_dir / default_name
    elif output.is_dir() or output.suffix.lower() != ".zip":
        target = output / default_name
    else:
        target = output
    if target.exists():
        console.print(f"[red]目标文件已存在：{target}[/red]")
        raise typer.Exit(1)

    secret_path = settings.home / "secret.key"
    includes_secret = secret_path.is_file()
    data_dirs = list(settings.backup_data_dirs_list)
    for extra in include_dir:
        if extra.strip() and extra not in data_dirs:
            data_dirs.append(extra.strip())
    data_entries = _backup_data_files(settings.home, data_dirs)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="bidcraft-backup-") as tmp:
            if is_pg:
                url = make_url(settings.db_url)
                # 直接按部件拼 libpq URI：render_as_string 无论 hide_password 真假都会
                # 把口令写进 URI（真值或 "***"），pg_dump 会拿它覆盖 PGPASSWORD。
                user = f"{url.username}@" if url.username else ""
                port = f":{url.port}" if url.port else ""
                libpq_uri = f"postgresql://{user}{url.host}{port}/{url.database or ''}"
                dump = Path(tmp) / "dump.sql"
                env = os.environ.copy()
                if url.password:
                    env["PGPASSWORD"] = url.password
                result = subprocess.run(
                    [
                        "pg_dump", "--no-owner", "--no-privileges",
                        "-d", libpq_uri, "-f", str(dump),
                    ],
                    env=env, capture_output=True, text=True, check=False,
                )
                if result.returncode != 0:
                    raise RuntimeError("pg_dump 失败：" + (result.stderr or "").strip()[:500])
                snapshot, db_entry = dump, "dump.sql"
            else:
                snapshot, db_entry = Path(tmp) / "bidcraft.db", "bidcraft.db"
                # SQLite 在线备份 API：在源库事务一致性点复制，不写原库。
                source = sqlite3.connect(str(db_path), timeout=30)
                try:
                    dest = sqlite3.connect(str(snapshot))
                    try:
                        source.backup(dest)
                        check = dest.execute("PRAGMA quick_check").fetchone()
                    finally:
                        dest.close()
                finally:
                    source.close()
                if not check or check[0] != "ok":
                    detail = check[0] if check else "无结果"
                    raise RuntimeError(f"快照完整性校验失败：{detail}")

            db_bytes = snapshot.stat().st_size
            data_manifest = [
                {
                    "path": arcname,
                    "bytes": source.stat().st_size,
                    "sha256": _sha256_file(source),
                }
                for source, arcname in data_entries
            ]
            manifest = {
                "version": __version__,
                "created_at": datetime.now(UTC).isoformat(),
                "db_backend": settings.db_backend,
                "db_file": db_entry,
                "db_bytes": db_bytes,
                "includes_secret_key": includes_secret,
                "bidcraft_home": str(settings.home),
                "data_dirs": data_dirs,
                "data_files": data_manifest,
            }
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(snapshot, db_entry)
                if includes_secret:
                    archive.write(secret_path, "secret.key")
                for source, arcname in data_entries:
                    archive.write(source, arcname)
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                )
    except Exception as exc:  # noqa: BLE001
        if target.is_file():
            target.unlink(missing_ok=True)
        console.print(f"[red]备份失败：{exc}[/red]")
        raise typer.Exit(1) from exc

    size = target.stat().st_size
    console.print(f"[green]备份完成[/green] {target}")
    console.print(
        f"大小 {_format_bytes(size)}（{size:,} 字节）· "
        f"secret.key：{'已包含' if includes_secret else '未找到（跳过）'} · "
        f"数据文件：{len(data_entries)} 个"
    )
    if is_pg:
        console.print(
            "[dim]恢复：停止服务 → 解压出 dump.sql → 用 pg_restore/psql 导入目标库 "
            "→ 放回 secret.key 与 projects/ 等数据目录 → 启动服务。[/dim]"
        )
    else:
        console.print(
            "[dim]恢复：停止服务 → 解压 zip → 将 bidcraft.db、secret.key 与 "
            "projects/ 等数据目录放回 BIDCRAFT_HOME → 启动服务。[/dim]"
        )


# ------------------------------------------------------------------ client CLI
client_app = typer.Typer(add_completion=False, help="命令行客户端：登录、上传与下载。")
app.add_typer(client_app, name="client")


def _client_or_exit(server: str | None = None):
    from .client import BidCraftClient, ClientError

    try:
        return BidCraftClient(server=server)
    except ClientError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@client_app.command("login")
def client_login(
    server: str = typer.Option(..., "--server", help="服务器地址，如 https://bid.example.com"),
    username: str = typer.Option("", "--username", "-u", help="用户名（省略则使用配置或交互输入）"),
    password: str = typer.Option("", "--password", help="密码（省略则交互式输入）"),
) -> None:
    """登录并保存会话 Cookie。"""
    import getpass

    from .client import CLIENT_HOME, BidCraftClient, ClientError

    if not username:
        existing = BidCraftClient(server=server).config.username
        username = existing or typer.prompt("用户名")
    if not password:
        password = getpass.getpass("密码：")
    try:
        with BidCraftClient(server=server) as client:
            user = client.login(username, password)
    except ClientError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    name = user.get("display_name") or user.get("username") or username
    console.print(f"[green]登录成功[/green] {name}")
    console.print(f"[dim]配置目录：{CLIENT_HOME}[/dim]")


@client_app.command("status")
def client_status(
    server: str = typer.Option("", "--server", help="临时覆盖服务器地址"),
) -> None:
    """查看服务器状态与当前登录用户。"""
    from .client import ClientError

    client = _client_or_exit(server or None)
    try:
        health = client.health()
        user = client.me()
    except ClientError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    finally:
        client.close()
    console.print(f"服务器：{client.server}")
    console.print(f"版本：{health.get('version', '未知')}")
    console.print(
        f"当前用户：{user.get('display_name') or user.get('username', '')} "
        f"（{user.get('role_label') or user.get('role', '')}）"
    )
    if "credits" in user:
        console.print(f"算力余额：{user.get('credits')}")


@client_app.command("projects")
def client_projects(
    server: str = typer.Option("", "--server", help="临时覆盖服务器地址"),
) -> None:
    """列出当前用户可见的项目。"""
    from .client import ClientError

    client = _client_or_exit(server or None)
    try:
        rows = client.projects()
    except ClientError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    finally:
        client.close()
    if not rows:
        console.print("[dim]暂无项目[/dim]")
        return
    table = Table(title="项目列表")
    table.add_column("ID", style="cyan")
    table.add_column("名称")
    table.add_column("包件")
    table.add_column("创建时间", style="dim")
    for row in rows:
        table.add_row(
            str(row.get("id", "")),
            str(row.get("name", "")),
            str(row.get("package", "")),
            str(row.get("created_at", "")),
        )
    console.print(table)


@client_app.command("upload")
def client_upload(
    paths: list[Path] = typer.Argument(..., help="本地文件路径（可多个）"),
    project: str = typer.Option("", "--project", help="上传为项目原件（项目 ID/编号）"),
    doc: bool = typer.Option(False, "--doc", help="上传到个人知识库（默认）"),
) -> None:
    """上传文档到个人知识库或项目原件。"""
    from .client import ClientError

    if project and doc:
        console.print("[red]--project 与 --doc 不能同时使用[/red]")
        raise typer.Exit(1)
    client = _client_or_exit()
    try:
        for path in paths:
            if project:
                result = client.upload_source(project, path)
                summary = result.get("project") or {}
                title = summary.get("name") if isinstance(summary, dict) else ""
                console.print(
                    f"[green]已上传项目原件[/green] {path.name}"
                    + (f" → {title}" if title else "")
                )
            else:
                created = client.upload_doc(path)
                console.print(
                    f"[green]已上传文档[/green] {created.get('filename') or path.name}"
                    + (f"（{created.get('id')}）" if created.get("id") else "")
                )
    except ClientError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    finally:
        client.close()


@client_app.command("download")
def client_download(
    target: str = typer.Argument(..., help="下载 URL、相对路径或文档 ID"),
    out: Path = typer.Option(..., "--out", "-o", help="保存路径（文件或目录）"),
) -> None:
    """下载文档或项目文件。"""
    from .client import ClientError

    if target.startswith(("http://", "https://")):
        url = target
    elif "/" in target:
        url = "/" + target.lstrip("/")
    else:
        url = f"/my/docs/{target}/file"
    client = _client_or_exit()
    try:
        saved = client.download(url, out)
    except ClientError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    finally:
        client.close()
    console.print(f"[green]已保存[/green] {saved}")


@app.command()
def desktop(
    server: str = typer.Option(
        "",
        "--server",
        help="远端服务器地址（省略时读环境变量 BIDCRAFT_SERVER 或上次 client login 保存的地址）",
    ),
    port: int = typer.Option(0, "--port", help="本地端口（默认随机）"),
    browser: bool = typer.Option(False, "--browser", help="使用系统浏览器（默认原生窗口）"),
    no_browser: bool = typer.Option(False, "--no-browser", help="不自动打开浏览器（仅 --browser）"),
    webview_mode: bool = typer.Option(False, "--webview", help="使用 pywebview 内嵌窗口（旧模式）"),
) -> None:
    """启动桌面客户端：Qt 原生外壳 + API 直连 + 本地文件桥。"""
    from .client import load_config
    from .desktop import run_desktop, run_window

    target = server.strip() or load_config().server
    if not target and (browser or webview_mode):
        console.print(
            "[red]未指定服务器[/red]：请使用 --server，或先执行 "
            "[bold]bidcraft client login --server URL[/bold] 保存地址。"
        )
        raise typer.Exit(2)
    try:
        if browser:
            run_desktop(target, port=port, open_browser=not no_browser)
        elif webview_mode:
            run_window(target, port=port)
        else:
            from .qtshell import run_qt

            if not run_qt(target, port=port):
                console.print("[yellow]Qt 外壳不可用，回退到内嵌窗口模式…[/yellow]")
                if not target:
                    console.print("[red]回退模式需要 --server 指定服务器地址[/red]")
                    raise typer.Exit(2)
                run_window(target, port=port)
    except KeyboardInterrupt:  # pragma: no cover - 交互式退出
        pass
    except (ValueError, OSError) as exc:
        console.print(f"[red]桌面网关启动失败：{exc}[/red]")
        raise typer.Exit(1) from exc


if __name__ == "__main__":
    app()


@app.command("knowledge-audit")
def knowledge_audit(
    directory: Path = typer.Argument(..., help="待入库素材目录（md/txt）"),
    package: str | None = typer.Option(None, "--package", "-p", help="只看某个行业包"),
    threshold: float = typer.Option(0.62, "--threshold", help="章节匹配阈值 0–1"),
    output: Path | None = typer.Option(None, "--output", "-o", help="完整报告 JSON 输出路径"),
    as_json: bool = typer.Option(False, "--json", help="直接输出 JSON"),
) -> None:
    """素材体检与行业包章节覆盖度对比（只读，不写库）。"""
    from .knowledge_audit import audit, render, to_json

    if not directory.is_dir():
        console.print(f"[red]目录不存在：{directory}[/red]")
        raise typer.Exit(1)

    report = audit(directory, threshold=threshold)
    if package:
        if package not in report["packages"]:
            console.print(f"[red]未知行业包：{package}[/red]")
            raise typer.Exit(2)
        report["packages"] = {package: report["packages"][package]}

    if as_json:
        console.print_json(to_json(report))
    else:
        console.print(render(report))

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(to_json(report) + "\n", encoding="utf-8")
        console.print(f"[green]报告已写入 {output}[/green]")


@app.command()
def dedup(
    directory: Path = typer.Argument(..., help="待查重目录（txt/md/markdown，docx 可选）"),
    threshold: float = typer.Option(0.85, "--threshold", help="相似度阈值 0–1"),
    min_chars: int = typer.Option(80, "--min-chars", help="参与比对的最少段落字数"),
    as_json: bool = typer.Option(False, "--json", help="直接输出 JSON"),
    output: Path | None = typer.Option(None, "--output", "-o", help="报告 JSON 输出路径"),
) -> None:
    """跨文件段落查重（只读，不联网）。"""
    from .dedup import compare, render, to_json, write_json

    if not directory.is_dir():
        console.print(f"[red]目录不存在：{directory}[/red]")
        raise typer.Exit(1)

    report = compare(directory, threshold=threshold, min_chars=min_chars)
    if as_json:
        console.print_json(to_json(report))
    else:
        console.print(render(report), markup=False, highlight=False)

    if output is not None:
        write_json(report, output)
        console.print(f"[green]报告已写入 {output}[/green]")


@app.command("data-audit")
def data_audit(
    path: Path = typer.Argument(..., help="开放数据文件（csv/txt/xlsx）"),
    mode: str = typer.Option("reasons", "--mode", "-m", help="reasons=废标原因归因；bids=开标报价离散度"),
    output: Path | None = typer.Option(None, "--output", "-o", help="完整报告 JSON 输出路径"),
    as_json: bool = typer.Option(False, "--json", help="直接输出 JSON"),
) -> None:
    """分析公共资源交易开放数据（只读，不联网）。"""
    import json as _json

    from .tender_stats import DataAuditError, audit_file, write_json

    if not path.is_file():
        console.print(f"[red]文件不存在：{path}[/red]")
        raise typer.Exit(1)
    try:
        result = audit_file(path, mode)
    except DataAuditError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    if as_json:
        console.print_json(_json.dumps(result, ensure_ascii=False))
    else:
        console.print(result["markdown"])
    if output is not None:
        write_json(result, output)
        console.print(f"[green]报告已写入 {output}[/green]")


@app.command("mineru-check")
def mineru_check(
    url: str = typer.Option("", "--url", help="临时覆盖 BIDCRAFT_MINERU_URL"),
    pdf: str = typer.Option("", "--pdf", help="可选：用一份 PDF 实跑一次解析"),
) -> None:
    """检查 MinerU 解析服务是否可用（配置缺失时提示回退内置解析链）。"""
    from .parsing import mineru

    if url.strip():
        settings.mineru_url = url.strip()
    if not mineru.available():
        console.print("[yellow]未配置 MinerU，PDF 使用内置解析（pypdf/pdfplumber + OCR）。[/yellow]")
        console.print("[dim]设置 BIDCRAFT_MINERU_URL=http://<host>:<port> 后重新运行。[/dim]")
        raise typer.Exit(2)
    console.print(f"MinerU 地址：{settings.mineru_url}")
    if not pdf:
        console.print("[green]已配置。[/green] 用 --pdf 指定一份 PDF 可实测解析质量。")
        return
    try:
        document = mineru.parse_pdf(pdf)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]解析失败：{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]解析成功[/green] 页数≈{document.page_count} 表格 {len(document.tables)} "
        f"字符 {len(document.text)} 耗时 {document.meta.get('mineru_elapsed')}s"
    )


@app.command("backup-prune")
def backup_prune(
    keep: int = typer.Option(10, "--keep", "-k", help="保留最近多少份备份"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只显示将删除的文件"),
) -> None:
    """按保留份数清理旧备份（默认保留 10 份）。"""
    from . import compliance

    try:
        result = compliance.prune_backups(keep, dry_run=dry_run)
    except compliance.ComplianceError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    console.print(
        f"备份共 {result['total']} 份，保留 {result['keep']} 份，"
        f"{'将删除' if dry_run else '已删除'} {len(result['removed'])} 份"
    )
    for name in result["removed"]:
        console.print(f"  - {name}")


@app.command("backup-verify")
def backup_verify(
    path: str = typer.Argument(..., help="备份 zip 或 SQLite .db 文件"),
) -> None:
    """校验备份可恢复：SQLite 跑 integrity_check，PostgreSQL 解析 pg_dump SQL。"""
    from .compliance import ComplianceError, verify_backup

    try:
        report = verify_backup(path)
    except ComplianceError as exc:
        typer.echo(f"[red]{exc}[/red]", err=True)
        raise typer.Exit(2) from exc
    data = report.get("data_files") or {}
    extra = f"，数据文件 {data['checked']} 个" if data.get("checked") else ""
    typer.echo(
        f"备份完整：{report['tables']} 张表，{report['rows']} 行数据{extra}（{path}）"
    )


@app.command("purge-accounts")
def purge_accounts(
    dry_run: bool = typer.Option(False, "--dry-run", help="只列出到期账号，不删除"),
) -> None:
    """执行注销冷静期到期的账号删除（建议由 cron 每日调用）。"""
    from .compliance import purge_due_accounts
    from .db import session_scope

    with session_scope() as db:
        report = purge_due_accounts(db, dry_run=dry_run)
    prefix = "[dry-run] " if dry_run else ""
    usernames = ", ".join(item["username"] for item in report["purged"]) or "无"
    skipped = ", ".join(item["username"] for item in report["skipped"]) or "无"
    typer.echo(f"{prefix}到期账号 {len(report['purged'])} 个：{usernames}")
    typer.echo(f"{prefix}跳过（仍有子账号）{len(report['skipped'])} 个：{skipped}")


@app.command("db-orphans")
def db_orphans(
    apply: bool = typer.Option(False, "--apply", help="实际删除孤儿行；默认只报告"),
) -> None:
    """检测并可选清理数据库中的孤儿引用（ID 指向已不存在的父行）。

    只读运行（默认）列出各关联字段的孤儿数量；``--apply`` 才会删除。这是开启
    外键强制之前的数据体检手段：先清零孤儿，再整表重建补 FK。注意审计
    ``user_id`` 的悬挂是注销追责的有意设计，不在检测/清理范围。
    """
    from .db import session_scope
    from .services.purge import clean_orphans, find_orphans

    with session_scope() as db:
        found = find_orphans(db)
        total = sum(len(rows) for rows in found.values())
        if not found:
            typer.echo("未发现孤儿引用")
            return
        for key, rows in sorted(found.items()):
            sample = ", ".join(str(row) for row in rows[:5])
            suffix = " ..." if len(rows) > 5 else ""
            typer.echo(f"[yellow]{key}[/yellow]: {len(rows)} 条 ({sample}{suffix})")
        typer.echo(f"共 {total} 条孤儿引用")
        if apply:
            removed = clean_orphans(db)
            typer.echo(f"[green]已清理 {sum(removed.values())} 条孤儿引用[/green]")
        else:
            typer.echo("[dim]只报告模式；加 --apply 才会删除[/dim]")

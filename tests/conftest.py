from __future__ import annotations

import os
from pathlib import Path

import pytest

from bidcraft.config import settings
from bidcraft.db import init_db, reset_engine_for_tests


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from bidcraft import cli, jobqueue  # noqa: F401  (ensure imports are wired)

    # DB 驱动队列：上一测试可能启动了常驻 worker，先停掉，避免它认领本用例的 job。
    jobqueue._reset()
    monkeypatch.setattr(settings, "home", tmp_path / ".bidcraft")
    settings.model_base_url = ""
    settings.model_name = ""
    settings.offline = True
    settings.admin_password = "test-admin"
    settings.session_days = 1
    settings.home.mkdir(parents=True, exist_ok=True)
    reset_engine_for_tests(f"sqlite+pysqlite:///{settings.home / 'bidcraft.db'}")
    init_db()
    yield
    reset_engine_for_tests(f"sqlite+pysqlite:///{settings.home / 'bidcraft.db'}")


@pytest.fixture()
def tender_path():
    return Path(__file__).parent / "fixtures" / "tender_cabling.txt"


@pytest.fixture()
def real_tender_root() -> Path:
    """真实/脱敏招标文件数据集根目录。

    只由 ``BIDCRAFT_REAL_TENDER_ROOT`` 注入，未注入时用数据集模块的默认路径；
    测试代码不硬编码真实数据位置，也不用合成夹具替代用户提供的数据集。
    """
    from bidcraft.evaluation.tender_dataset import DEFAULT_ROOT

    configured = os.environ.get("BIDCRAFT_REAL_TENDER_ROOT", "").strip()
    return Path(configured) if configured else DEFAULT_ROOT


def make_docx_tender(path):
    from docx import Document

    document = Document()
    document.add_paragraph("项目名称：某园区智能化工程（一期）")
    document.add_paragraph("项目编号：ZB-2026-0412")
    document.add_paragraph("招标人：某科技园区管理委员会")
    document.add_paragraph("预算金额：人民币 350 万元")
    document.add_paragraph("投标截止时间：2026 年 12 月 20 日 09:30")
    document.add_paragraph("投标有效期：90 天")
    document.add_paragraph("工期：90 日历天")
    document.add_paragraph("质保期：2 年")
    document.add_paragraph("项目地点：某市某区某园区")
    document.add_paragraph("资格要求：投标人须具备电子与智能化工程专业承包二级及以上资质")
    document.add_paragraph("信息点数量：480 个")
    document.add_paragraph("线缆等级：Cat6A")
    document.add_paragraph("测试标准：GB 50311-2016")
    document.add_paragraph("评标办法：综合评分法")

    document.add_paragraph("工程量清单")
    table = document.add_table(rows=5, cols=5)
    rows = [
        ["序号", "名称", "规格型号", "单位", "数量"],
        ["1", "信息点面板", "双口 Cat6A", "个", "480"],
        ["2", "六类非屏蔽双绞线", "Cat6A", "米", "12000"],
        ["3", "配线架", "24 口", "个", "20"],
        ["4", "机柜", "42U", "台", "4"],
    ]
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            table.cell(r, c).text = value

    document.add_paragraph("评分办法")
    table = document.add_table(rows=4, cols=3)
    rows = [
        ["评审因素", "分值", "评分标准"],
        ["技术方案", "40", "方案完整、响应招标要求"],
        ["投标报价", "30", "低价优先"],
        ["售后服务", "10", "响应时限与备件"],
    ]
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            table.cell(r, c).text = value

    document.save(str(path))
    return path

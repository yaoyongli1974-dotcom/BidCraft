from openpyxl import Workbook

from bidcraft import boq, pipeline
from bidcraft.db import session_scope


def _xlsx(p):
    wb = Workbook(); ws = wb.active
    ws.append(["名称", "单位", "数量"])
    ws.append(["交换机", "台", 2])
    ws.append(["线缆", "米", 100])
    f = p / "b.xlsx"; wb.save(f); return f


def test_diff_version(db, tmp_path):
    with session_scope() as s:
        proj = pipeline.create_project(s, "差异项目")
        boq.import_boq(s, proj, _xlsx(tmp_path))
        rows = boq.build_payload(s, proj.id)["rows"]
        keys = [r["key"] for r in rows if r.get("boq_row") is not None and r.get("key")]
        rows_by_key = {r["key"]: r for r in rows}
        k0, k1 = keys[0], keys[1]
        boq.set_unit_price(s, proj, rows_by_key[k0]["boq_row"], 10.0)
        boq.set_unit_price(s, proj, rows_by_key[k1]["boq_row"], 5.0)
        v = boq.save_version(s, proj, "v1")
        vid = v["id"] if isinstance(v, dict) else v.id
        boq.set_unit_price(s, proj, rows_by_key[k0]["boq_row"], 20.0)
        boq.set_unit_price(s, proj, rows_by_key[k1]["boq_row"], None)
        d = boq.diff_version(s, proj, vid)
        assert d["other_id"] == "current"
        ch = {c["key"]: c for c in d["changed"]}
        assert k0 in ch and ch[k0]["from"] == 10.0 and ch[k0]["to"] == 20.0
        assert k1 in {r["key"] for r in d["added"]}
        same = boq.diff_version(s, proj, vid, other=vid)
        assert not same["added"] and not same["removed"] and not same["changed"]

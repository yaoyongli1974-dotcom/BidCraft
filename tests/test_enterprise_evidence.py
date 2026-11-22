"""企业资产证据描述：哈希、去重与文档元数据（Lane A）。"""

from __future__ import annotations


def test_qualification_evidence_hash_and_descriptor(db, tmp_path):
    from bidcraft.db import session_scope
    from bidcraft.enterprise import evidence
    from bidcraft.models import Qualification

    proof = tmp_path / "license.pdf"
    proof.write_bytes(b"%PDF-1.4 proof")
    with session_scope() as session:
        row = Qualification(
            owner_id="u1",
            kind="license",
            name="电子与智能化工程专业承包二级",
            cert_no="DZ-001",
            enterprise_id="e1",
            evidence=[
                {
                    "filename": "license.pdf",
                    "path": str(proof),
                    "size": proof.stat().st_size,
                    "mime": "application/pdf",
                }
            ],
        )
        session.add(row)
        session.flush()
        entries = evidence.qualification_evidence(row)
        descriptor = evidence.qualification_descriptor(row, status="valid")

    assert entries[0]["sha256"] == evidence.file_sha256(proof)
    assert descriptor["shared"] is True
    assert descriptor["asset_type"] == "qualification"
    assert descriptor["evidence"][0]["filename"] == "license.pdf"


def test_personnel_and_document_evidence_are_deduplicated(db):
    from bidcraft.db import session_scope
    from bidcraft.enterprise import evidence
    from bidcraft.models import KbDocument, Personnel

    with session_scope() as session:
        person = Personnel(
            owner_id="u1", name="张三", cert_no="建造师-001", enterprise_id="e1"
        )
        document = KbDocument(
            kb_id="kb1",
            source="业绩证明.pdf",
            sha256="a" * 64,
            size=12,
            pages=2,
            kind="performance",
            version=3,
            tags=["业绩"],
        )
        session.add_all([person, document])
        session.flush()
        person_desc = evidence.personnel_descriptor(person)
        doc_desc = evidence.document_descriptor(document, enterprise_id="e1")
        merged = evidence.aggregate([person_desc, person_desc, doc_desc])

    assert person_desc["asset_type"] == "personnel"
    assert person_desc["evidence"][0]["sha256"]
    assert doc_desc["sha256"] == "a" * 64
    assert doc_desc["version"] == 3
    assert len(merged) == 2

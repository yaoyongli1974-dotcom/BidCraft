"""语义查重：注入向量、降级回退、重排。"""

from __future__ import annotations

from bidcraft.similarity import semantic as semantic_engine

LONG_A = (
    "本方案采用集中式管理平台对综合布线系统进行统一监控，支持端口状态、链路质量与"
    "告警信息的实时展示，并提供历史数据查询和报表导出能力。"
)
LONG_B = (
    "系统通过统一管理平台集中监控布线端口与链路状态，可实时呈现告警，并支持历史"
    "记录查询以及报表输出，方便运维人员日常管理。"
)


def _paragraph(text: str, *, heading: str, node_id: str, index: int = 0) -> dict:
    return {
        "text": text,
        "heading": heading,
        "node_id": node_id,
        "project_id": "p",
        "project_name": "项目",
        "index": index,
    }


def test_compare_semantic_vectors_and_threshold():
    pairs = semantic_engine.compare_semantic_vectors(
        [[1.0, 0.0], [0.95, 0.05], [0.0, 1.0]], threshold=0.9
    )
    assert len(pairs) == 1
    assert (pairs[0]["i"], pairs[0]["j"]) == (0, 1)
    assert pairs[0]["similarity"] > 0.99
    assert semantic_engine.compare_semantic_vectors([[1.0, 0.0]], [[0.0, 1.0]], threshold=0.5) == []


def test_find_semantic_duplicates_with_injected_vectors():
    left = [_paragraph(LONG_A, heading="管理平台", node_id="a")]
    right = [_paragraph(LONG_B, heading="运维管理", node_id="b")]
    result = semantic_engine.find_semantic_duplicates(
        None,
        left,
        right,
        threshold=0.9,
        vectors_a=[[1.0, 0.0, 0.0]],
        vectors_b=[[0.97, 0.1, 0.0]],
    )
    assert result["stats"]["degraded"] is False
    assert len(result["hits"]) == 1
    hit = result["hits"][0]
    assert hit["kind"] == "semantic"
    assert hit["evidence"]["method"] == "embedding"
    assert hit["evidence"]["vector_similarity"] > 0.9
    assert hit["loc_a"] and hit["loc_b"] and hit["excerpt_a"] and hit["excerpt_b"]


def test_semantic_falls_back_to_text_without_model(db):
    paragraphs = [
        _paragraph(LONG_A, heading="技术方案", node_id="a"),
        _paragraph(LONG_A, heading="重复章节", node_id="b"),
    ]
    result = semantic_engine.find_semantic_duplicates(db, paragraphs, threshold=0.85)
    assert result["stats"]["degraded"] is True
    assert result["hits"], result
    assert result["hits"][0]["kind"] == "semantic"
    assert result["hits"][0]["evidence"]["method"] == "text-fallback"
    assert result["skipped"]


def test_rerank_similarity_hits_uses_vector_score():
    hits = [
        {"similarity": 0.9, "loc_a": "b", "evidence": {"vector_similarity": 0.6}},
        {"similarity": 0.5, "loc_a": "a", "evidence": {"vector_similarity": 0.99}},
    ]
    ranked = semantic_engine.rerank_similarity_hits(hits, semantic_weight=0.65)
    assert ranked[0]["loc_a"] == "a"
    assert ranked[0]["evidence"]["rerank_score"] > ranked[1]["evidence"]["rerank_score"]
    assert hits[0]["loc_a"] == "b"  # 不修改输入列表

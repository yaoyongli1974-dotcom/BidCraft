from bidcraft.generation.writer import continuity_digest


def test_digest_empty():
    assert continuity_digest([]) == ""


def test_digest_includes_recent_and_index():
    chapters = [(f"第{i}章", f"正文内容 {i} " * 40) for i in range(1, 6)]
    text = continuity_digest(chapters)
    assert "全书已写章节索引" in text
    assert "第1章" in text and "第5章" in text
    assert "近期章节" in text
    # only the last three chapters get full excerpts
    assert text.count("【第") == 3
    assert "【第1章】" not in text


def test_digest_capped_and_collapses_whitespace():
    chapters = [("章", "字  字\n字" * 500) for _ in range(4)]
    text = continuity_digest(chapters, cap=500)
    assert len(text) <= 500
    assert "\n\n\n" not in text


def test_digest_skips_empty_bodies():
    text = continuity_digest([("空章", "")])
    assert "空章" in text and "近期章节" not in text


def test_long_document_digest_keeps_every_heading_within_cap():
    chapters = [(f"第{i}章", f"长正文 {i} " * 200) for i in range(1, 51)]
    text = continuity_digest(chapters, cap=1200)
    assert len(text) <= 1200
    for index in range(1, 51):
        assert f"第{index}章" in text
    assert "全书已写章节索引" in text
    assert not text.endswith("长正文")

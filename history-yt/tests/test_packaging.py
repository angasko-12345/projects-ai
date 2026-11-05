from histyt.assemble import build_chapters, build_description


def _manifest(sections, scenes_per_section=3):
    scenes = []
    index = 0
    for name, seconds in sections:
        for _ in range(scenes_per_section):
            index += 1
            scenes.append({"scene_id": f"SC{index:03d}", "section": name,
                           "duration": seconds})
    return {"scenes": scenes}


def test_short_video_gets_no_invalid_chapters():
    short = _manifest([("rise", 2), ("peak", 2), ("fall", 2), ("legacy", 2)])
    assert build_chapters(short) == []


def test_long_video_gets_chapters_each_at_least_ten_seconds():
    long = _manifest([("rise", 10), ("peak", 10), ("fall", 10), ("legacy", 10)])
    chapters = build_chapters(long)
    assert len(chapters) == 4
    assert chapters[0][1] == 0.0
    for index, (name, start) in enumerate(chapters):
        assert name
        if index + 1 < len(chapters):
            assert chapters[index + 1][1] - start >= 10.0


def test_description_includes_chapters_and_disclosure_only_when_set():
    long = _manifest([("rise", 10), ("peak", 10), ("fall", 10), ("legacy", 10)])
    meta = {"claims": [], "needs_check": []}
    sources = [{"id": "S1", "title": "A", "url": "https://example.com/a"}]
    description = build_description(
        meta, sources, "T", manifest=long,
        cfg={"youtube": {"contains_synthetic_media": True}})
    assert "Chapters:" in description
    assert "00:00 rise" in description
    assert "realistic AI-generated scenes" in description
    assert "Corrections welcome" in description
    assert "https://example.com/a" in description

    plain = build_description(meta, sources, "T", manifest=long, cfg=None)
    assert "AI-generated" not in plain
    assert "Chapters:" in plain

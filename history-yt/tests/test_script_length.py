import os

import pytest

from histyt.providers_llm import LLMProvider
from histyt.script_stage import (build_prompt, count_words, generate_script,
                                 parse_script_full, target_words)
from histyt.state import PipelineError


class ShortProvider(LLMProvider):
    def complete(self, system, user):
        return ("Title: T\n"
                "## rise\n[C1] One short claim. (S1, S2)\n"
                "## fall\n[C2] Another short claim. (S1, S2)\n")


def test_target_words_from_config(cfg):
    cfg["target_length_minutes"] = 12
    cfg["words_per_minute"] = 150
    assert target_words(cfg) == (1800, 150)


def test_fake_provider_honors_word_budget(runs, cfg, make_video):
    vid = make_video()
    meta = generate_script(runs, vid, cfg)
    assert meta["target_words"] == 150
    assert 0.9 * 150 <= meta["word_count"] <= 1.4 * 150


def test_claims_are_grouped_under_sections(runs, cfg, make_video):
    vid = make_video()
    meta = generate_script(runs, vid, cfg)
    with open(os.path.join(runs, vid, "script.md"), encoding="utf-8") as f:
        text = f.read()
    parsed = parse_script_full(text)
    assert parsed["sections"]
    assert all(claim["section"] for claim in parsed["claims"])
    assert [c["section"] for c in parsed["claims"]] == [c["section"] for c in meta["claims"]]
    assert meta["sections"]
    for section in meta["sections"]:
        assert section["claims"], f"section {section['name']} has no claims"
    # Re-parsing the written file reproduces the claim set.
    assert [c["id"] for c in parsed["claims"]] == [c["id"] for c in meta["claims"]]


class DuplicateIdProvider(LLMProvider):
    def complete(self, system, user):
        return ("Title: T\n"
                "## a\n[C1] First claim here. (S1, S2)\n"
                "## b\n[C1] Second claim here. (S1, S2)\n")


def test_duplicate_claim_ids_are_renumbered(runs, cfg, make_video):
    cfg["target_length_minutes"] = 0
    vid = make_video()
    meta = generate_script(runs, vid, cfg, provider=DuplicateIdProvider())
    assert [claim["id"] for claim in meta["claims"]] == ["C1", "C2"]
    with open(os.path.join(runs, vid, "script.md"), encoding="utf-8") as f:
        text = f.read()
    assert "[C1] First claim here." in text
    assert "[C2] Second claim here." in text


def test_short_script_hard_fails_below_sixty_percent(runs, cfg, make_video):
    vid = make_video()
    with pytest.raises(PipelineError, match="below 60%"):
        generate_script(runs, vid, cfg, provider=ShortProvider())


def test_no_target_skips_the_length_floor(runs, cfg, make_video):
    cfg["target_length_minutes"] = 0
    vid = make_video()
    meta = generate_script(runs, vid, cfg, provider=ShortProvider())
    assert meta["word_count"] == count_words("One short claim. Another short claim.")


def test_prompt_carries_the_length_and_section_contract():
    prompt = build_prompt("Rome", ["rise", "fall"], [{"id": "S1"}], target=1800)
    assert "1800 words" in prompt
    assert "## rise" in prompt and "## fall" in prompt
    assert "first 30 words" in prompt
    assert "Do not invent numbers" in prompt


def test_prompt_includes_stored_source_excerpts():
    sources = [{"id": "S1", "title": "A", "text": "Arminius won in 9 CE at Teutoburg."}]
    prompt = build_prompt("Rome", ["rise"], sources)
    assert "Arminius won in 9 CE at Teutoburg." in prompt

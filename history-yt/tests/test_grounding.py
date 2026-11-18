import pytest

from histyt.providers_llm import LLMProvider
from histyt.research import add_source, load_sources
from histyt.script_stage import (assess_claim, format_review, generate_script,
                                 parse_script_full)
from histyt.state import PipelineError


class GroundedProvider(LLMProvider):
    def complete(self, system, user):
        return ("Title: Arminius\n"
                "## rise\n"
                "[C1] Arminius destroyed three legions in 9 CE. (S1, S2)\n"
                "[C2] The Rhine frontier shifted after the battle. (S1, S2)\n")


def _sources(runs, vid):
    add_source(runs, vid, "https://example.com/one", "One", "",
               tier="primary",
               text="Arminius destroyed three legions in 9 CE at Teutoburg.")
    add_source(runs, vid, "https://example.com/two", "Two", "",
               tier="secondary",
               text="The Rhine frontier shifted after the battle in 9 CE.")


def test_add_source_stores_tier_and_text(runs, make_video):
    vid = make_video(with_sources=False)
    _sources(runs, vid)
    sources = load_sources(runs, vid)
    assert [s["tier"] for s in sources] == ["primary", "secondary"]
    assert "Teutoburg" in sources[0]["text"]


def test_unknown_tier_raises(runs, make_video):
    vid = make_video(with_sources=False)
    with pytest.raises(PipelineError, match="unknown source tier"):
        add_source(runs, vid, "https://example.com", "X", "", tier="blog")


def test_assess_claim_supported_with_matching_text():
    claim = {"id": "C1", "text": "Arminius destroyed three legions in 9 CE.",
             "sources": ["S1", "S2"]}
    sources = {"S1": {"text": "Arminius destroyed three legions in 9 CE at Teutoburg.",
                      "tier": "primary"},
               "S2": {"text": "Three legions were lost in 9 CE.", "tier": "secondary"}}
    result = assess_claim(claim, sources)
    assert result["status"] == "supported"
    assert result["missing_numbers"] == []


def test_assess_claim_flags_a_number_the_sources_do_not_contain():
    claim = {"id": "C1", "text": "Arminius destroyed three legions in 9 CE.",
             "sources": ["S1"]}
    sources = {"S1": {"text": "Arminius destroyed three legions at Teutoburg.",
                      "tier": "primary"}}
    result = assess_claim(claim, sources)
    assert "9" in result["missing_numbers"]
    assert result["status"] in ("weak", "unverified")


def test_assess_claim_is_unchecked_without_stored_text():
    claim = {"id": "C1", "text": "Anything at all.", "sources": ["S1"]}
    sources = {"S1": {"text": "", "tier": "secondary"}}
    assert assess_claim(claim, sources)["status"] == "unchecked"


def test_discovery_only_citations_are_flagged():
    claim = {"id": "C1", "text": "A claim.", "sources": ["S1", "S2"]}
    sources = {"S1": {"text": "A claim.", "tier": "discovery"},
               "S2": {"text": "A claim too.", "tier": "discovery"}}
    result = assess_claim(claim, sources)
    assert result["discovery_only"] is True


def test_generate_script_records_review(runs, cfg, make_video):
    cfg["target_length_minutes"] = 0  # length is tested elsewhere
    vid = make_video(with_sources=False)
    _sources(runs, vid)
    meta = generate_script(runs, vid, cfg, provider=GroundedProvider())
    assert meta["review"]["counts"]["supported"] >= 1
    assert "source grounding" in format_review(meta)
    parsed = parse_script_full(open(f"{runs}/{vid}/script.md", encoding="utf-8").read())
    assert parsed["sections"] == ["rise"]

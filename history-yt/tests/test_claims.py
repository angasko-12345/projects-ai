import pytest

from histyt.providers_llm import LLMProvider
from histyt.script_stage import generate_script, parse_script, require_claim_sources
from histyt.state import PipelineError


class BadProvider(LLMProvider):
    def complete(self, system, user):
        return ("Title: X\n[C1] Claims galore without cites.\n[C2] More claims. (S1)\n"
                "Needs check:\n- verify\n")


def test_claim_without_enough_sources_fails(runs, cfg, make_video):
    vid = make_video()
    with pytest.raises(PipelineError, match="lacks >=2 sources"):
        generate_script(runs, vid, cfg, provider=BadProvider())


def test_require_claim_sources_direct():
    claims = [{"id": "C1", "text": "x", "sources": ["S1"]}]
    with pytest.raises(PipelineError):
        require_claim_sources(claims, ["S1", "S2"])


def test_parse_script_formats():
    text = "Title: T\n[C1] Hello world. (S1,S2)\n[C2] More. [S1,S2]\nNeeds check:\n- fact\n"
    title, claims, needs = parse_script(text)
    assert title == "T"
    assert len(claims) == 2
    assert claims[0]["sources"] == ["S1", "S2"]
    assert claims[1]["sources"] == ["S1", "S2"]
    assert needs == ["fact"]

import pytest

from histyt import state, topics
from histyt.variation import title_similarity, variation_check


def _seeded(runs, title, template, phash):
    vid = topics.add_topic(runs, title, "angle")
    st = state.load(runs, vid)
    st["template"] = template
    st["thumbnail_phash"] = phash
    state.save(runs, vid, st)
    return vid


def test_title_similarity_known():
    assert title_similarity("the great wall falls", "the great wall falls") == pytest.approx(1.0)
    assert title_similarity("alpha beta gamma", "delta epsilon zeta") == 0.0


def test_pass_when_distinct(runs, cfg):
    a = _seeded(runs, "Alpha Beta Gamma Delta", "origin_story", "0000000000000000")
    _seeded(runs, "Zulu Yankee Xray Whiskey", "rise_fall", "ffffffffffffffff")
    _, passed = variation_check(runs, a, cfg)
    assert passed is True


def test_fail_on_title_sim(runs, cfg):
    a = _seeded(runs, "The Great Eastern War Chronicles", "origin_story", "0000000000000000")
    _seeded(runs, "The Great Eastern War Chronicles Continues", "rise_fall", "1234567812345678")
    _, passed = variation_check(runs, a, cfg)
    assert passed is False


def test_fail_on_phash_close(runs, cfg):
    a = _seeded(runs, "Alpha Beta Gamma Delta One", "origin_story", "0000000000000000")
    _seeded(runs, "Zulu Yankee Xray Whiskey Two", "rise_fall", "0000000000000001")
    _, passed = variation_check(runs, a, cfg)
    assert passed is False


def test_fail_on_template_repeat(runs, cfg):
    a = _seeded(runs, "Alpha Beta Gamma Delta", "origin_story", "0000000000000000")
    _seeded(runs, "Zulu Yankee Xray Whiskey", "origin_story", "ffffffffffffffff")
    _, passed = variation_check(runs, a, cfg)
    assert passed is False

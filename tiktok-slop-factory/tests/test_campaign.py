"""Campaign brief loading for clipcheck: valid files, defaults, and precise errors.

Pure Python, no FFmpeg. Every error must name the file and the offending key so
a clipper can fix the TOML without reading a traceback.
"""
from pathlib import Path

import pytest

from app import campaign as campaign_mod
from app.campaign import (
    DEFAULT_CTA_MATCH_THRESHOLD,
    DEFAULT_CTA_MIN_WIDTH_FRACTION,
    CampaignError,
    load_campaign,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = PROJECT_ROOT / "examples" / "campaign.example.toml"

needs_toml = pytest.mark.skipif(campaign_mod._toml is None,
                                reason="no tomllib (Python < 3.11) and no tomli")


def _write(tmp_path, text, name="campaign.toml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


@needs_toml
def test_example_campaign_loads():
    c = load_campaign(EXAMPLE)
    assert c.length_range == (15.0, 28.0)
    assert c.max_static_seconds == 1.5
    assert c.loudness_target == (-15.0, -14.0)
    assert c.max_true_peak == -1.0
    assert c.required_hashtags == ("#brandpartner", "#gamedev", "#indiegame")
    assert c.hashtags_must_be_last is True
    assert c.required_mentions == ("BrandName",)
    assert c.cta_min_seconds == 2.0
    # The example ships no logo file, so logo_path is commented out there.
    assert c.cta_logo_path is None


@needs_toml
def test_empty_campaign_configures_nothing(tmp_path):
    c = load_campaign(_write(tmp_path, ""))
    assert c.length_range is None and c.loudness_target is None
    assert c.cta_logo_path is None and c.required_hashtags == ()
    assert c.cta_logo_min_width_fraction == DEFAULT_CTA_MIN_WIDTH_FRACTION
    assert c.cta_logo_match_threshold == DEFAULT_CTA_MATCH_THRESHOLD


@needs_toml
def test_campaign_is_frozen(tmp_path):
    c = load_campaign(_write(tmp_path, ""))
    with pytest.raises(AttributeError):
        c.length_range = (1, 2)


@needs_toml
def test_logo_path_resolves_relative_to_toml_with_spaces(tmp_path):
    folder = tmp_path / "my campaign"
    folder.mkdir()
    (folder / "brand logo.png").write_bytes(b"x")
    c = load_campaign(_write(folder, '[cta]\nlogo_path = "brand logo.png"\nmin_seconds = 2\n'))
    assert c.cta_logo_path == (folder / "brand logo.png").resolve()


@pytest.mark.parametrize("text, key, fragment", [
    ("[video]\nlenght_range = [1, 2]\n", "lenght_range", "unknown key"),
    ("[vidoe]\nlength_range = [1, 2]\n", "[vidoe]", "unknown section"),
    ("[video]\nlength_range = [30, 10]\n", "length_range", "greater than max"),
    ("[video]\nlength_range = [10]\n", "length_range", "two-number list"),
    ("[video]\nmax_static_seconds = -1\n", "max_static_seconds", "greater than 0"),
    ("[audio]\nmax_true_peak = \"loud\"\n", "max_true_peak", "expected a number"),
    ("[cta]\nmin_width_fraction = 1.5\n", "min_width_fraction", "at most 1"),
    ("[post]\nrequired_hashtags = [\"brand\"]\n", "required_hashtags", "not a hashtag"),
    ("[post]\nrequired_hashtags = [\"#a b\"]\n", "required_hashtags", "not a hashtag"),
    ("[post]\nrequired_hashtags = [\"#a\", \"#A\"]\n", "required_hashtags", "twice"),
    ("[post]\nhashtags_must_be_last = \"yes\"\n", "hashtags_must_be_last", "true or false"),
    ("[post]\nrequired_mentions = [\"\"]\n", "required_mentions", "non-empty"),
    ("[cta]\nlogo_path = \"missing.png\"\n", "logo_path", "file not found"),
])
@needs_toml
def test_bad_values_name_file_and_key(tmp_path, text, key, fragment):
    path = _write(tmp_path, text)
    with pytest.raises(CampaignError) as exc:
        load_campaign(path)
    msg = str(exc.value)
    assert str(path) in msg and key in msg and fragment in msg


@needs_toml
def test_invalid_toml_is_a_campaign_error(tmp_path):
    path = _write(tmp_path, "[video\nlength_range = 1")
    with pytest.raises(CampaignError, match="invalid TOML"):
        load_campaign(path)


def test_missing_file_is_a_campaign_error(tmp_path):
    with pytest.raises(CampaignError, match="not found"):
        load_campaign(tmp_path / "nope.toml")

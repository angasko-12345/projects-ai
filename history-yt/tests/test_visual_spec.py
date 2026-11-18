"""Visual spec: deterministic type, archive queries and period constraints."""
from histyt import visual_spec as spec

PERIOD = "Roman Empire, 9 CE"
LOCATION = "Teutoburg Forest, Germania"


def test_keywords_drop_stopwords_and_keep_reading_order():
    words = spec.keywords("The legion crossed the river, and the auxiliaries followed.")
    assert words == ["legion", "crossed", "river", "auxiliaries", "followed"]


def test_queries_carry_period_and_location():
    queries = spec.build_queries(
        "Three legions entered the forest and none returned.",
        PERIOD, LOCATION, "conflict")
    assert len(queries) == 3
    assert PERIOD in queries[0]
    assert LOCATION in queries[1]
    assert queries[2] == f"{PERIOD} {LOCATION}"


def test_queries_never_send_the_narration_as_a_phrase():
    narration = "Three legions entered the forest and none returned."
    queries = spec.build_queries(narration, PERIOD, LOCATION, "conflict")
    assert all(query.lower().strip(".") != narration.lower().strip(".") for query in queries)


def test_queries_fall_back_to_the_section_without_period_or_location():
    queries = spec.build_queries("The frontier collapsed along the Rhine.", "", "", "decline")
    assert queries
    assert "decline" in queries


def test_visual_type_prefers_a_map_for_geography_claims():
    assert spec.visual_type("The frontier collapsed along the Rhine.", "", {}) == "map"
    assert spec.visual_type("Varus trusted the wrong ally.", "fall", {}) == "archive"


def test_visual_type_honours_config_overrides_and_ignores_junk():
    overrides = {"visual_types": {"legacy": "diagram"}}
    assert spec.visual_type("Anything at all.", "legacy", overrides) == "diagram"
    assert spec.visual_type("Anything at all.", "rise", {"visual_type": "generated"}) == "generated"
    assert spec.visual_type("Anything at all.", "rise", {"visual_type": "poster"}) == "archive"


def test_period_note_matches_the_era_and_falls_back_generically():
    note = spec.period_note(PERIOD, LOCATION, "the legions marched")
    assert "segmentata" in note
    assert spec.period_note("", "", "a trade dispute") == spec.GENERIC_PERIOD_NOTE


def test_authenticity_risk_is_high_for_generated_named_figures():
    assert spec.authenticity_risk("generated", "Publius Quinctilius Varus ordered the march.") == "high"
    assert spec.authenticity_risk("generated", "A column of legionaries in the rain.") == "medium"
    assert spec.authenticity_risk("archive", "Varus ordered the march.") == "low"


def test_spec_for_scene_is_complete_stable_and_pending():
    first = spec.spec_for_scene("The frontier collapsed along the Rhine.", "decline",
                                PERIOD, LOCATION, {})
    second = spec.spec_for_scene("The frontier collapsed along the Rhine.", "decline",
                                 PERIOD, LOCATION, {})
    assert first == second
    assert first["visual_type"] == "map"
    assert first["visual_query"] == first["visual_queries"][0]
    assert first["subject"]
    assert first["asset_status"] == "pending"
    assert set(first) == {"visual_type", "visual_query", "visual_queries", "subject",
                          "period_note", "authenticity_risk", "asset_status"}

from __future__ import annotations

import pytest

from trividia_truemetrix_daemon.dosing import SlidingScaleError, lookup_dose, parse_sliding_scale


def test_parse_sliding_scale_basic_bands():
    raw = ":70:0:hypo, do not dose\n71:150:0\n151:200:2\n201:250:4\n251::6:call doctor\n"
    bands = parse_sliding_scale(raw, "test")
    assert len(bands) == 5
    assert bands[0].low_mg_dl is None
    assert bands[0].high_mg_dl == 70
    assert bands[0].label == "hypo, do not dose"
    assert bands[-1].low_mg_dl == 251
    assert bands[-1].high_mg_dl is None
    assert bands[-1].dose_units == 6.0


def test_parse_sliding_scale_ignores_blank_lines():
    raw = "\n71:150:0\n\n151:200:2\n\n"
    bands = parse_sliding_scale(raw, "test")
    assert len(bands) == 2


def test_parse_sliding_scale_empty_returns_no_bands():
    assert parse_sliding_scale("", "test") == ()


def test_parse_sliding_scale_rejects_malformed_line():
    with pytest.raises(SlidingScaleError, match="must be 'low:high:dose"):
        parse_sliding_scale("71-150-0", "test")


def test_parse_sliding_scale_rejects_non_integer_bound():
    with pytest.raises(SlidingScaleError, match="non-integer bound"):
        parse_sliding_scale("abc:150:0", "test")


def test_parse_sliding_scale_rejects_low_greater_than_high():
    with pytest.raises(SlidingScaleError, match="low > high"):
        parse_sliding_scale("200:100:2", "test")


def test_parse_sliding_scale_rejects_non_numeric_dose():
    with pytest.raises(SlidingScaleError, match="non-numeric dose"):
        parse_sliding_scale("71:150:abc", "test")


def test_parse_sliding_scale_rejects_negative_dose():
    with pytest.raises(SlidingScaleError, match="negative dose"):
        parse_sliding_scale("71:150:-2", "test")


def test_parse_sliding_scale_rejects_overlapping_bands():
    with pytest.raises(SlidingScaleError, match="overlap"):
        parse_sliding_scale("71:150:0\n140:200:2", "test")


def test_parse_sliding_scale_rejects_overlap_with_open_ended_band():
    with pytest.raises(SlidingScaleError, match="overlap"):
        parse_sliding_scale(":150:0\n100:200:2", "test")


def test_parse_sliding_scale_adjacent_bands_do_not_overlap():
    # 70 and 71 are distinct integers -- not an overlap.
    bands = parse_sliding_scale(":70:0\n71:150:2", "test")
    assert len(bands) == 2


def test_lookup_dose_finds_matching_band():
    bands = parse_sliding_scale(":70:0\n71:150:2\n151::4", "test")
    assert lookup_dose(50, bands).dose_units == 0
    assert lookup_dose(100, bands).dose_units == 2
    assert lookup_dose(300, bands).dose_units == 4


def test_lookup_dose_returns_none_for_uncovered_gap():
    bands = parse_sliding_scale("71:150:2\n201:300:4", "test")
    assert lookup_dose(175, bands) is None


def test_lookup_dose_empty_bands_returns_none():
    assert lookup_dose(100, ()) is None

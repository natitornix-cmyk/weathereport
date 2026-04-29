from app.polymarket import _to_celsius_if_needed, parse_bin_label


def test_parse_range_celsius_integer():
    assert parse_bin_label("32-33°C") == (32.0, 34.0)


def test_parse_range_decimal():
    lo, hi = parse_bin_label("32.5-33.5")
    assert lo == 32.5 and hi == 33.5


def test_parse_open_top():
    assert parse_bin_label("35+")[0] == 35.0
    assert parse_bin_label("> 35")[0] == 35.0
    assert parse_bin_label("35 or higher")[0] == 35.0


def test_parse_open_bottom():
    lo, hi = parse_bin_label("< 28")
    assert lo == float("-inf")
    assert hi == 28.0


def test_fahrenheit_label_converts():
    lo, hi = _to_celsius_if_needed(80.0, 81.0, "80-81°F")
    assert abs(lo - 26.6667) < 0.01
    assert abs(hi - 27.2222) < 0.01


def test_unknown_returns_none():
    assert parse_bin_label("blue moon") is None

from sekka.stats import (
    estimate_tokens,
    format_duration,
    format_stats,
    format_tokens,
    tokens_per_second,
)


def test_tokens_per_second_basic():
    assert tokens_per_second(100, 2.0) == 50.0


def test_tokens_per_second_guards():
    assert tokens_per_second(None, 2.0) is None
    assert tokens_per_second(0, 2.0) is None
    assert tokens_per_second(100, 0.0) is None
    assert tokens_per_second(100, -1.0) is None


def test_format_duration():
    assert format_duration(1.239) == "1.2s"
    assert format_duration(59.94) == "59.9s"
    assert format_duration(65.0) == "1m 5.0s"


def test_format_stats_full():
    assert format_stats(2.0, 100) == "[2.0s, 50.0 tok/s]"


def test_format_stats_missing_tokens():
    assert format_stats(1.5, None) == "[1.5s, ? tok/s]"
    assert format_stats(1.5, 0) == "[1.5s, ? tok/s]"


def test_format_tokens():
    assert format_tokens(None) == "?"
    assert format_tokens(0) == "0"
    assert format_tokens(812) == "812"
    assert format_tokens(1000) == "1k"
    assert format_tokens(45312) == "45k"
    assert format_tokens(262144) == "262k"


def test_estimate_tokens():
    assert estimate_tokens("") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("x" * 400) == 100

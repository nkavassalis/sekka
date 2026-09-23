from sekka.stats import format_duration, format_stats, tokens_per_second


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

import pytest

from sekka import dice


def test_single_die_bounds():
    for _ in range(200):
        roll = dice.roll("d20")
        assert 1 <= roll.total <= 20
        assert roll.dice == [roll.total]


def test_modifier_is_reported_and_applied():
    for _ in range(50):
        roll = dice.roll("1d4+3")
        assert 4 <= roll.total <= 7
        assert roll.modifier == 3
        assert roll.detail.endswith("+3")


def test_multiple_terms_and_subtraction():
    roll = dice.roll("2d8-1d4+3")
    assert len(roll.dice) == 3
    assert -6 + 3 <= roll.total <= 16 + 3


def test_same_expression_twice_is_not_the_same_roll():
    totals = {dice.roll("3d6").total for _ in range(200)}
    assert len(totals) > 3, "dice look suspiciously deterministic"


@pytest.mark.parametrize(
    "bad",
    ["", "2d", "abc", "101d6", "2d1", "-2d6", "2d6+", "7", "d0", "1d1001", "2d6**2"],
)
def test_bad_expressions_are_rejected(bad):
    with pytest.raises(dice.DiceError):
        dice.roll(bad)


def test_usage_hint_when_empty():
    with pytest.raises(dice.DiceError, match="Usage"):
        dice.roll("")


def test_whitespace_and_uppercase_are_fine():
    roll = dice.roll(" 2 D 6 + 1 ")
    assert len(roll.dice) == 2
    assert roll.modifier == 1

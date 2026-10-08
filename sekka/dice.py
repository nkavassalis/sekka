"""Fair dice for /roll.

Rolls use random.SystemRandom (os.urandom) and the result is shown to the player
and put into the model's context, so the model cannot quietly invent a better
number.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

MAX_DICE = 100
MAX_SIDES = 1000

DICE_TERM = re.compile(r"^(?P<sign>[+-]?)(?P<count>\d*)d(?P<sides>\d+)$", re.IGNORECASE)
MOD_TERM = re.compile(r"^(?P<value>[+-]?\d+)$")


class DiceError(ValueError):
    """A roll expression sekka cannot parse (safe to show in the TUI)."""


@dataclass
class Roll:
    expression: str
    total: int
    dice: list[int] = field(default_factory=list)
    modifier: int = 0

    @property
    def detail(self) -> str:
        """'2d6+3 = 12 (4, 5, +3)' - everything a player wants to see."""
        parts = []
        if self.dice:
            parts.append("(" + ", ".join(str(d) for d in self.dice) + ")")
        if self.modifier:
            parts.append(f"{'+' if self.modifier > 0 else '-'}{abs(self.modifier)}")
        return f"{self.expression} = {self.total}" + (" " + " ".join(parts) if parts else "")


def _split_terms(raw: str) -> list[str]:
    """Split '2d8-1d4+3' into signed terms ['2d8', '-1d4', '+3']."""
    terms: list[str] = []
    current = ""
    for ch in raw:
        if ch in "+-" and current:
            terms.append(current)
            current = ch
        else:
            current += ch
    if current:
        terms.append(current)
    return terms


def roll(expression: str) -> Roll:
    """Parse and roll NdM[+/-K]: 'd20', '2d6', '3d6+2', '2d8-1d4+3'."""
    raw = expression.strip().lower().replace(" ", "")
    if not raw:
        raise DiceError("Usage: /roll 2d6+3")
    rng = random.SystemRandom()
    if raw.startswith("-"):
        raise DiceError("Start the expression with a die, e.g. /roll 2d6-1d4+3")
    terms = _split_terms(raw)
    if not terms:
        raise DiceError(f"Cannot understand {expression!r}. Try /roll 2d6+3")

    total = 0
    modifier = 0
    throws: list[int] = []
    for term in terms:
        dice_match = DICE_TERM.match(term)
        if dice_match:
            sign = -1 if dice_match.group("sign") == "-" else 1
            count = int(dice_match.group("count") or "1")
            sides = int(dice_match.group("sides"))
            if not 1 <= count <= MAX_DICE:
                raise DiceError(f"Roll between 1 and {MAX_DICE} dice (got {count}).")
            if not 2 <= sides <= MAX_SIDES:
                raise DiceError(f"Die sides must be between 2 and {MAX_SIDES} (got {sides}).")
            batch = [rng.randint(1, sides) for _ in range(count)]
            throws.extend(batch)
            total += sign * sum(batch)
            continue
        mod_match = MOD_TERM.match(term)
        if mod_match:
            value = int(mod_match.group("value"))
            total += value
            modifier += value
            continue
        raise DiceError(f"Cannot understand {expression!r}. Try /roll 2d6+3")

    if not throws:
        raise DiceError("Roll at least one die, e.g. /roll 1d20+3")
    return Roll(expression=raw, total=total, dice=throws, modifier=modifier)

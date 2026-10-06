"""Human-readable formatting for dashboard and table display."""

from __future__ import annotations


def human_tokens(count: int) -> str:
    """Format a token count the way the product brief specifies: 12.4K, 8.7M, 1.42B.

    Three significant digits maximum, trailing zeros stripped. Values that
    round to 1000 of one unit roll over to the next unit instead of
    displaying "1000K".
    """
    n = int(count)
    if n < 0:
        n = 0
    if n < 1_000:
        return str(n)
    exponent = len(str(n)) - 1
    if exponent >= 9:
        unit_exp, suffix = 9, "B"
    elif exponent >= 6:
        unit_exp, suffix = 6, "M"
    else:
        unit_exp, suffix = 3, "K"
    text = f"{n / 10 ** unit_exp:.3g}"
    if float(text) >= 1_000:  # 3-sig rounding hit 1000: promote the unit
        if suffix == "B":
            return str(n)
        unit_exp += 3
        suffix = {"K": "M", "M": "B"}[suffix]
        text = f"{n / 10 ** unit_exp:.3g}"
    return f"{text}{suffix}"


def human_count(count: int) -> str:
    """Plain count with thousands separators, e.g. 1,204 requests."""
    return f"{int(count):,}"


def human_cost(cost: float | None) -> str:
    """Currency with separators; a dash when the provider reported no cost."""
    if cost is None:
        return "-"
    return f"${cost:,.2f}"


def human_ratio(exact_part: int, total: int) -> str:
    """Share of a total that is exact, e.g. "96% exact"."""
    if total <= 0:
        return "no data"
    percent = round(100.0 * exact_part / total)
    return f"{percent}% exact"

"""Sliding-scale insulin dose lookup, from a profile's configured bands.

Every band comes directly from a `[profile.<name>] sliding_scale` config
value -- nothing here invents, validates the clinical correctness of, or
second-guesses a dosing table. Populate it with exactly what a doctor
prescribed for that person, not values guessed or looked up generically;
sliding scales vary by insulin type, sensitivity, and individual treatment
plan. This is a display convenience for a table you already have, not
medical advice, and not a substitute for checking with a doctor before
relying on it. See the main README's Disclaimer.
"""

from __future__ import annotations

import dataclasses


class SlidingScaleError(Exception):
    """Raised for a malformed or ambiguous sliding_scale configuration."""


@dataclasses.dataclass(frozen=True)
class SlidingScaleBand:
    """One row of a sliding scale: a glucose range, a dose, and an optional note.

    Attributes:
        low_mg_dl: Inclusive lower bound, or None for no lower bound.
        high_mg_dl: Inclusive upper bound, or None for no upper bound.
        dose_units: Insulin dose for this range, in units, as configured.
        label: Optional free-text note (e.g. "call doctor", "treat hypo
            first, do not dose").
    """

    low_mg_dl: int | None
    high_mg_dl: int | None
    dose_units: float
    label: str


_NEG_INF = -(10**9)
_POS_INF = 10**9


def _bounds(band: SlidingScaleBand) -> tuple[int, int]:
    low = band.low_mg_dl if band.low_mg_dl is not None else _NEG_INF
    high = band.high_mg_dl if band.high_mg_dl is not None else _POS_INF
    return low, high


def parse_sliding_scale(raw: str, context: str) -> tuple[SlidingScaleBand, ...]:
    """Parse a `sliding_scale` config value into validated bands.

    Format: one band per line, ``low:high:dose[:label]``. ``low``/``high``
    may be left blank for an open-ended band (e.g. ``:70:0:hypo, do not
    dose`` covers everything at or below 70; ``301::8:call doctor`` covers
    301 and up).

    Args:
        raw: The raw multi-line config value.
        context: Description of where this came from (e.g.
            ``"[profile.Alice] sliding_scale"``), used in error messages.

    Raises:
        SlidingScaleError: If a line is malformed, a bound or dose isn't
            numeric, low > high, a dose is negative, or two bands overlap.
            An ambiguous table -- two bands both claiming to cover the same
            reading -- is a safety issue, not just a config nit, so it's
            rejected outright rather than resolved by first-match-wins.
    """
    bands = []
    for line_num, raw_line in enumerate(raw.strip().splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        parts = line.split(":")
        if len(parts) not in (3, 4):
            raise SlidingScaleError(
                f"{context}: line {line_num} must be 'low:high:dose[:label]', got {line!r}"
            )
        low_raw, high_raw, dose_raw = (p.strip() for p in parts[:3])
        label = parts[3].strip() if len(parts) == 4 else ""

        try:
            low = int(low_raw) if low_raw else None
            high = int(high_raw) if high_raw else None
        except ValueError as exc:
            raise SlidingScaleError(
                f"{context}: line {line_num} has a non-integer bound in {line!r}"
            ) from exc
        if low is not None and high is not None and low > high:
            raise SlidingScaleError(
                f"{context}: line {line_num} has low > high ({low} > {high})"
            )

        try:
            dose = float(dose_raw)
        except ValueError as exc:
            raise SlidingScaleError(
                f"{context}: line {line_num} has a non-numeric dose {dose_raw!r}"
            ) from exc
        if dose < 0:
            raise SlidingScaleError(f"{context}: line {line_num} has a negative dose")

        bands.append(
            SlidingScaleBand(low_mg_dl=low, high_mg_dl=high, dose_units=dose, label=label)
        )

    _check_no_overlap(bands, context)
    return tuple(bands)


def _check_no_overlap(bands: list[SlidingScaleBand], context: str) -> None:
    for i, a in enumerate(bands):
        a_low, a_high = _bounds(a)
        for b in bands[i + 1:]:
            b_low, b_high = _bounds(b)
            if a_low <= b_high and b_low <= a_high:
                raise SlidingScaleError(
                    f"{context}: bands overlap ({a.low_mg_dl}-{a.high_mg_dl} and "
                    f"{b.low_mg_dl}-{b.high_mg_dl}) -- an ambiguous dosing table is a "
                    "safety issue; fix the ranges so every value maps to exactly one band"
                )


def lookup_dose(
    value_mg_dl: int, bands: tuple[SlidingScaleBand, ...]
) -> SlidingScaleBand | None:
    """Return the band covering value_mg_dl, or None if no configured band covers it.

    A gap in coverage (e.g. bands for 0-300 but a reading of 350) is a real
    possibility and deliberately not treated as an error here -- the caller
    displays "no guidance configured" rather than guessing.
    """
    for band in bands:
        low, high = _bounds(band)
        if low <= value_mg_dl <= high:
            return band
    return None

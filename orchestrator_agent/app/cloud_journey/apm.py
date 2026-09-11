"""Canonical APM identifier handling shared by Journey entry points."""

from __future__ import annotations

import re


CANONICAL_APM_ID_PATTERN = re.compile(r"^APM00\d{4}$")


def normalize_apm_id(value: str) -> str:
    """Return an APM ID as ``APM00####`` or reject an invalid identifier."""

    compact = re.sub(r"[\s-]+", "", value.strip().upper())
    number = compact.removeprefix("APM")
    if len(number) == 4 and number.isdigit():
        number = f"00{number}"
    canonical = f"APM{number}"
    if not CANONICAL_APM_ID_PATTERN.fullmatch(canonical):
        raise ValueError(
            "APM ID must use the APM00#### convention, for example APM004001."
        )
    return canonical


__all__ = ["CANONICAL_APM_ID_PATTERN", "normalize_apm_id"]

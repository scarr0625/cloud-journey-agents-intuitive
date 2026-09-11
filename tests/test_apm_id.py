from __future__ import annotations

import pytest

from orchestrator_agent.app.cloud_journey.apm import normalize_apm_id


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        ("APM004001", "APM004001"),
        ("apm004001", "APM004001"),
        ("APM 004001", "APM004001"),
        ("004001", "APM004001"),
        ("4001", "APM004001"),
    ],
)
def test_normalize_apm_id(supplied: str, expected: str) -> None:
    assert normalize_apm_id(supplied) == expected


@pytest.mark.parametrize("supplied", ["", "100401", "APM100401", "APM00123"])
def test_normalize_apm_id_rejects_noncanonical_numbers(supplied: str) -> None:
    with pytest.raises(ValueError, match=r"APM00####"):
        normalize_apm_id(supplied)

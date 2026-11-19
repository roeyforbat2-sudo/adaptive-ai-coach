from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from coach.engine.safety import (
    LOW_CONFIDENCE,
    NO_INTENSITY,
    REST_SUGGESTED,
    SLEEP_CAUTION,
    VOLUME_CAP,
    assess,
)

TODAY = date(2026, 11, 17)


def _recovery(
    status: str = "within normal range", rhr7: float = 45.0, base: float = 45.0, sleep7: float = 7.5
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [pd.Timestamp("2026-11-16")],
            "hrv_status": [status],
            "rhr_7d": [rhr7],
            "rhr_base": [base],
            "sleep_7d": [sleep7],
        }
    )


def _runs(km_per_day: dict[str, float]) -> pd.DataFrame:
    days = list(km_per_day)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(days),
            "family": "run",
            "distance_m": [km_per_day[d] * 1000 for d in days],
        }
    )


NO_RUNS = _runs({})


def test_all_clear_is_green() -> None:
    a = assess(_recovery(), NO_RUNS, None, TODAY)
    assert a.readiness == "green" and a.intensity_allowed and a.guardrails == []


def test_low_hrv_blocks_intensity() -> None:
    a = assess(_recovery(status="below normal range"), NO_RUNS, None, TODAY)
    assert a.readiness == "amber" and NO_INTENSITY in a.codes and not a.intensity_allowed


def test_elevated_resting_hr() -> None:
    a = assess(_recovery(rhr7=51, base=45), NO_RUNS, None, TODAY)
    assert NO_INTENSITY in a.codes
    assert assess(_recovery(rhr7=49, base=45), NO_RUNS, None, TODAY).intensity_allowed


def test_low_hrv_and_high_rhr_is_red() -> None:
    a = assess(_recovery(status="below normal range", rhr7=52, base=45), NO_RUNS, None, TODAY)
    assert a.readiness == "red" and REST_SUGGESTED in a.codes


def test_short_sleep_is_a_caution_not_a_block() -> None:
    a = assess(_recovery(sleep7=5.5), NO_RUNS, None, TODAY)
    assert SLEEP_CAUTION in a.codes and a.intensity_allowed and a.readiness == "amber"


def test_missing_baseline_lowers_confidence_only() -> None:
    a = assess(
        _recovery(status="insufficient data", rhr7=np.nan, base=np.nan), NO_RUNS, None, TODAY
    )
    assert a.codes == {LOW_CONFIDENCE} and a.readiness == "green"


def test_volume_spike() -> None:
    prior = {f"2026-10-{d:02d}": 5.0 for d in (14, 16, 18, 21, 23, 25, 28, 30)} | {
        "2026-11-02": 5.0,
        "2026-11-04": 5.0,
    }  # ~12.5 km/week before
    spike = prior | {"2026-11-11": 10.0, "2026-11-13": 10.0, "2026-11-15": 12.0}  # 32 km
    assert VOLUME_CAP in assess(_recovery(), _runs(spike), None, TODAY).codes
    assert VOLUME_CAP not in assess(_recovery(), _runs(prior), None, TODAY).codes


def test_reported_pain_and_illness() -> None:
    fb = pd.DataFrame(
        {"date": pd.to_datetime(["2026-11-15"]), "pain_flag": [True], "illness_flag": [False]}
    )
    a = assess(_recovery(), NO_RUNS, fb, TODAY)
    assert a.readiness == "red" and NO_INTENSITY in a.codes
    ill = fb.assign(pain_flag=False, illness_flag=True)
    assert REST_SUGGESTED in assess(_recovery(), NO_RUNS, ill, TODAY).codes
    old = fb.assign(date=pd.to_datetime(["2026-11-01"]))
    assert assess(_recovery(), NO_RUNS, old, TODAY).readiness == "green"

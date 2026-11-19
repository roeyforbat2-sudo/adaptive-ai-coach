"""Deterministic safety rules and readiness.

These rules are separate from the AI coach and always win: the AI may explain them and
choose within them, but its suggestions are checked against them afterwards
(see coach.ai.coach.enforce_guardrails). Thresholds are deliberately conservative and
descriptive; this is not medical advice. See docs/coaching-methodology.md.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Literal

import pandas as pd

Readiness = Literal["green", "amber", "red"]
NO_INTENSITY = "no_intensity"  # easy or rest only
REST_SUGGESTED = "rest_suggested"
VOLUME_CAP = "volume_cap"  # keep the rest of the week easy, no extra volume
LOW_CONFIDENCE = "low_confidence"
SLEEP_CAUTION = "sleep_caution"


@dataclass(frozen=True)
class Guardrail:
    code: str
    severity: Literal["info", "caution", "stop"]
    reason: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SafetyAssessment:
    readiness: Readiness
    guardrails: list[Guardrail] = field(default_factory=list)

    @property
    def codes(self) -> set[str]:
        return {g.code for g in self.guardrails}

    @property
    def intensity_allowed(self) -> bool:
        return not self.codes & {NO_INTENSITY, REST_SUGGESTED}


def assess(
    recovery: pd.DataFrame,
    activities: pd.DataFrame,
    feedback: pd.DataFrame | None,
    as_of: date,
    *,
    rhr_elevated_bpm: float = 5.0,
    min_sleep_h: float = 6.0,
    volume_spike_ratio: float = 1.3,
) -> SafetyAssessment:
    """Evaluate rules on data up to the day before `as_of` (today's session is the subject)."""
    guardrails: list[Guardrail] = []
    yesterday = pd.Timestamp(as_of) - pd.Timedelta(days=1)
    rec = recovery[recovery["date"] <= yesterday]
    latest = rec.tail(1)

    hrv_low = rhr_high = False
    if latest.empty or latest["hrv_status"].iloc[0] == "insufficient data":
        guardrails.append(
            Guardrail(LOW_CONFIDENCE, "info", "Not enough recent HRV data for a personal baseline.")
        )
    elif latest["hrv_status"].iloc[0] == "below normal range":
        hrv_low = True
        guardrails.append(
            Guardrail(NO_INTENSITY, "caution", "7-day HRV is below your personal normal range.")
        )

    if not latest.empty:
        rhr7, base = latest["rhr_7d"].iloc[0], latest["rhr_base"].iloc[0]
        if pd.notna(rhr7) and pd.notna(base) and rhr7 >= base + rhr_elevated_bpm:
            rhr_high = True
            guardrails.append(
                Guardrail(
                    NO_INTENSITY,
                    "caution",
                    f"7-day resting HR ({rhr7:.0f} bpm) is {rhr7 - base:.0f} bpm above "
                    "your baseline.",
                )
            )
        sleep7 = latest["sleep_7d"].iloc[0]
        if pd.notna(sleep7) and sleep7 < min_sleep_h:
            guardrails.append(
                Guardrail(
                    SLEEP_CAUTION, "caution", f"Average sleep over 7 nights is {sleep7:.1f} h."
                )
            )

    if hrv_low and rhr_high:
        guardrails.append(
            Guardrail(
                REST_SUGGESTED, "stop", "Both HRV and resting HR point to incomplete recovery."
            )
        )

    # Running volume: last 7 days vs. weekly average of the 4 weeks before.
    runs = activities[activities["family"] == "run"]
    last7 = runs[(runs["date"] > yesterday - pd.Timedelta(days=7)) & (runs["date"] <= yesterday)]
    prior = runs[
        (runs["date"] > yesterday - pd.Timedelta(days=35))
        & (runs["date"] <= yesterday - pd.Timedelta(days=7))
    ]
    last_km = float(last7["distance_m"].fillna(0).sum()) / 1000
    prior_km = float(prior["distance_m"].fillna(0).sum()) / 1000 / 4
    if prior_km >= 10 and last_km > volume_spike_ratio * prior_km:
        guardrails.append(
            Guardrail(
                VOLUME_CAP,
                "caution",
                f"Running volume jumped: {last_km:.0f} km in 7 days vs {prior_km:.0f} km/week "
                "before.",
            )
        )

    # Self-reported pain / illness in the last 3 days (feedback form, when used).
    if feedback is not None and not feedback.empty:
        recent = feedback[pd.to_datetime(feedback["date"]) > yesterday - pd.Timedelta(days=3)]
        if bool(recent.get("illness_flag", pd.Series(dtype=bool)).fillna(False).astype(bool).any()):
            guardrails.append(
                Guardrail(REST_SUGGESTED, "stop", "You reported feeling ill recently.")
            )
        if bool(recent.get("pain_flag", pd.Series(dtype=bool)).fillna(False).astype(bool).any()):
            guardrails.append(
                Guardrail(
                    NO_INTENSITY,
                    "stop",
                    "You reported pain recently; if it persists or worsens, see a professional.",
                )
            )

    severities = {g.severity for g in guardrails}
    readiness: Readiness = (
        "red" if "stop" in severities else "amber" if "caution" in severities else "green"
    )
    return SafetyAssessment(readiness=readiness, guardrails=guardrails)

# Coaching methodology

How the coach turns data into recommendations, which metrics it uses, and what they can and cannot tell us. Implemented in Phases 2 (analytics), 4 (engine), and 5 (LLM).

## Priorities
1. **Improve running performance.**
2. **Maintain or improve strength and muscle mass.**
3. **Optimize recovery and manage training load.**

Deterministic **safety rules sit above all three**: a priority can never override a safety rule. When priorities conflict (e.g., a heavy lower-body day vs. a key run), the engine keeps the key run and moves or modifies the strength session, while still protecting the weekly strength floor.

## Two separate layers

| | Deterministic layer (`analytics`, `engine`) | AI layer (`coach`) |
|---|---|---|
| Does | Computes metrics, applies safety rules, produces a baseline recommendation and **guardrails** | Explains, personalizes wording, answers questions, may *propose* alternatives |
| Can | Forbid intensity, cap duration, require rest | Only choose *within* guardrails |
| Tested by | Table-driven unit tests | Mocked-LLM tests + guardrail validator |

The LLM output is validated against the guardrails after generation; anything that violates them is discarded and logged. If the LLM fails, the deterministic recommendation is shown alone.

## Never invent missing data
- Missing values stay `None` end to end (DB → analytics → UI → LLM context). There is no interpolation and no "typical value" filling.
- **Load on a day with no activity is 0 only if we know the athlete had no session** (wellness data synced for that day, so the device was in use). Otherwise the day is *unknown*, and metrics that depend on it are flagged `incomplete`.
- Every derived metric carries a data-sufficiency status: `ok`, `incomplete`, or `insufficient_baseline`. The UI and the LLM context show this status, and the LLM is instructed to state uncertainty rather than guess.

## Session load
Used in this order of preference, recorded per session as `load_method`:
1. **Garmin `training_load`** when present (a device-computed, EPOC-based value).
2. **HR-based TRIMP** (Banister, 1991), using the athlete's resting/max HR from the profile.
3. **Session RPE** (Foster et al., 2001): RPE (1-10) × duration (min).

The methods are on different scales, so a series must not silently mix them: trends use one method consistently, and a method change is shown on charts. If none is available, load is *unknown* (not 0).

## Load indicators: descriptive, not predictive
These describe *how training has changed*. They are **not** injury predictors, fitness measurements, or absolute safety thresholds, and the coach never presents them as such.

| Indicator | Definition | Read as |
|---|---|---|
| **ATL** (acute load, "fatigue") | EWMA of daily load, time constant τ = 7 d: `ATL_t = ATL_{t-1} + λ(L_t − ATL_{t-1})`, `λ = 1 − e^{−1/τ}` | recent load |
| **CTL** (chronic load, "fitness") | Same with τ = 42 d | longer-term habitual load |
| **TSB** (balance, "form") | `CTL_{t-1} − ATL_{t-1}` | recent load relative to habitual load |
| **ACWR** | EWMA acute (7 d) / EWMA chronic (28 d) (Williams et al., 2017) | how fast load is changing vs. what the athlete is used to |
| Monotony | 7-day mean daily load / SD | lack of day-to-day variation |

### Limitations (documented in the UI too)
- **ACWR** has contested validity as an injury-risk measure. The ratio is mathematically coupled when acute load is part of chronic load, it is unstable when chronic load is low (e.g., returning from a break), and published "sweet spot" thresholds do not generalize to individuals (Lolli et al., 2019; Impellizzeri et al., 2020). We use the EWMA form, display it as a trend, and use thresholds only as *prompts to look closer*, combined with other signals. On its own it never blocks a session.
- **ATL/CTL/TSB** come from an impulse-response model (Banister). The time constants are population defaults, not fitted to the individual. They are only as good as the load input, and they ignore load type (a hard strength day and an easy run of equal "load" are not equivalent).
- CTL needs about 6 weeks of data before it means much; until then it is flagged `insufficient_baseline`.
- Strength training is poorly captured by HR-based load. Strength is therefore *also* tracked separately as weekly hard sets per muscle group.

## Individualized baselines
- **HRV:** Use ln(rMSSD). Compare the 7-day rolling mean against the athlete's own 60-day mean ± 1 SD (the smallest-worthwhile-change band) (Plews et al., 2013). One low night is noise; a 7-day mean below the band is a signal.
- **Resting HR:** deviation from the 60-day personal median.
- **Sleep:** duration vs. the athlete's own 30-day median, plus a sleep score if present.
- A baseline needs at least 14 valid days in its window; otherwise it is `insufficient_baseline` and that component is excluded (not guessed).

## Readiness (green / amber / red)
A transparent rule set, not a black-box score. Each component is shown with its reason:
- **Objective:** HRV trend vs. band, RHR deviation, sleep.
- **Subjective:** fatigue, soreness, mood, sleep quality (feedback form). Subjective measures often respond to training stress more consistently than objective ones (Saw, Main & Gastin, 2016), so they carry real weight. A strongly negative self-report can make the day amber even when the device data looks fine.
- **History:** recent hard sessions (last 48 h), load trend (ACWR/TSB as context), and missed sessions.

## Deterministic safety rules (`engine.safety`, Phase 4)
These rules are separate from, and run before, any AI step. They emit guardrails:

| Trigger | Guardrail |
|---|---|
| `pain_flag` reported | `no_intensity`, avoid loading the reported area; if pain persists more than 3 days or worsens → recommend seeing a qualified professional |
| `illness_flag` | `rest_day` (or easy walk only) |
| Readiness red | `no_intensity`, `max_duration` cap |
| Weekly run volume increase > configured cap (default 10%) | cap the remaining week's volume |
| Hard run within 48 h, plus heavy lower-body lifting planned | `avoid_lower_body` (move it, don't drop it) |
| Data insufficient for a judgement | recommendation marked low-confidence; no intensity escalation |

The thresholds live in `athlete_profile.settings_json` and can be edited. The coach is not medical advice.

## Strength preservation
- Track weekly hard sets (RIR ≤ 3) per muscle group against a maintenance floor (default ≥ 6/week for major groups) and the athlete's own recent average.
- Concurrent-training scheduling: keep heavy lower-body work ≥ 24-48 h from key runs where possible. During taper, keep intensity (load) but cut volume (sets).

## Rule-based insights (free mode, `coach/insights.py`)
All coaching on the Today tab comes from these fixed rules; no AI is involved.

**Per workout** (compared with the same kind of session in the previous 28 days; needs >= 3):
- Plan match: within 90-115% of the calendar slot = on plan. Gym sessions count as done at >= 50% of the slot, because calendar blocks include changing time and Garmin counts active time only.
- Runs: pace within +/-10 s/km and HR within +/-3 bpm = steady. Faster at the same HR = a fitness sign. Slower with a higher HR = a fatigue warning. A planned easy run with anaerobic training effect >= 2.0 or HR 8+ bpm above usual = "harder than easy".
- Quality sessions (intervals, tempo, tests) are recognised from the athlete's own activity names (for example `6*400`, איכות, טסט, tempo). They are compared only with other quality sessions, never with easy runs. The name itself is used locally only.
- A new longest run in 4 weeks (>= 8 km) is highlighted. The morning's HRV below range or < 6 h of sleep is noted.

**Per week** (last complete week vs. the up to 4 complete weeks before):
- Running km: > +30% warns, +10-30% is building, +/-10% is steady, < -10% is a lighter week (only when the recent average is >= 5 km).
- A new longest long run; gym sessions (>= 3 good, 2 note, < 2 warn); plan adherence (>= 90% good, >= 70% note); average sleep < 6.5 h noted.

**Goal progress** (long-run progression):
- Peak long run: half marathon ~18 km (2-week taper, safe increase ~1.5 km/week); marathon ~32 km (3-week taper, ~2 km/week); 10 km ~12 km (1-week taper).
- Needed increase = (peak - longest run in the last 4 weeks) / weeks until the peak. At <= 75% of the safe increase = comfortable; at <= 100% = on track; above = behind (suggest a lower peak or start extending now). Goals without a date ask for one.

**Training program** (`data/program.json`, the athlete's own plan translated to English; personal, git-ignored): weeks run Sunday to Saturday. Gym is on Sun/Tue/Thu, the quality run on Monday, the easy run on Wednesday (the test in calibration weeks), and the long run on Friday or Saturday. When a goal matches the program's race date, goal progress uses the program's own peak long run (19 km, week 17) instead of the generic peak.

**Calendar-driven week:** when Google Calendar has training events for the program week, they define the days. Each event's title (for example "ריצה- 6 קל" or "מכון- רגליים") is translated to English and classified: quality (איכות or intervals), easy, or the week's longest easy run of 8 km or more as the long run, and gym upper, legs or full body. Days without events are rest days. Moving, editing, adding or deleting an event therefore changes the app after the next sync. The program still supplies the weekly target, the pace guidance, and quality-session distances that titles don't state.

**Weekly suggestions** (`coach/week_plan.py`, current program week). These are advice only and never change the calendar:
- Safety first: low recovery means keep the remaining runs easy and swap a pending quality session; red readiness means an extra rest day before the long run; short sleep means a Saturday long run.
- Quality session done or missed. A missed one is not squeezed in later.
- Long run: if last week's long run was within 0.5 km of the plan, follow the plan. Otherwise cap this week at last week's actual + 1.5 km.
- Volume: under 80% of last week's plan means don't add the missing km; over 115% means stick to the plan.
- Easy-run discipline: the median pace of non-quality runs in the last 14 days, compared with the plan's easy range (more than 10 s/km faster is flagged).
- Gym sessions done so far, and the runs still to do with the km left to the weekly target.

These are general training-progression heuristics, not a personalised plan or medical advice.

## Workout analysis: "Coach's take" (`coach/workout_analysis.py`, Today tab)

The latest workout of the last 3 days, compared with **the session your calendar planned that day**. All rules are fixed and free; missing data gives no sentence.
- **Distance vs plan:** 95–110% of the planned km is on plan; 80–95% a bit short; under 80% well short (a note for quality sessions, whose planned km include jog recoveries; a warning for other runs); over 110% longer than planned.
- **Pace vs zone (easy and long runs):** compared with the program's easy range. More than 5 s/km faster is flagged ("Faster than planned"); more than 15 s/km slower is a neutral note. Mixed sessions (HMP segments, strides, "+" structures) are not judged on average pace.
- **Effort:** Garmin's Training Effect with Garmin's published scale (0 none, 1 minor, 2 maintaining, 3 improving, 4 highly improving, 5 overreaching). Aerobic TE ≥ 3.5 on an easy run is noted as high; anaerobic TE ≥ 2 on a quality session confirms the fast work.
- **History:** the existing per-workout rules (pace and heart rate against your own runs of the same kind over the last 28 days, recovery that morning).
- **Next:** the next session in your calendar. If the safety rules forbid intensity, the next run should be easy/shorter. After a long or quality run, the next run stays easy, and leg day keeps reps in reserve (the plan's own leg-fatigue rule).
- **Tag:** Extra session (not planned) / Shorter than planned / Faster than planned / Harder than planned / Check recovery (a warning) / On target.

## Weekly review (`coach/weekly_review.py`, every Saturday)

The program week (Sunday–Saturday) against the calendar-driven plan. It is shown on the Today tab (Saturday: at the top; other days: last week's review), and it is **posted to Google Calendar every Saturday at 20:00** as one event, "📋 Weekly review · Week N of M" (see `docs/security.md`). The same review is printed by `python -m coach.review`.
- **This week:** sessions done of due, km against the plan (90–115% is on plan), long run against plan (≥ 90% counts as done), quality sessions, gym sessions, easy-run pace against the easy range, sleep average, days with HRV below your normal range, resting HR against your baseline.
- **Next week:** the sessions from your calendar (or the program), and suggestions, in this order. (1) Low recovery markers: start easy, and do the quality session only once they recover. (2) Long run: do next week's planned long run if this week's was done; otherwise cap it at this week's + 1.5 km, or repeat this week's distance if it was missed. (3) If next week's plan is more than 20% above what you ran, don't add extra. (4) A missed quality session is not made up. (5) Slow down easy runs that are too fast. (6) Do all three gym sessions. (7) Sleep 7+ h if the average was under 6.5 h. (8) Notes for the week's purpose: down week, test, peak, taper, race.
- Mid-week ("your week so far"), later sessions count as upcoming, not missed.

## Daily check-in (`coach/checkins.py`, `services/checkin.py`, Today tab)

A 10-second form, with a reminder from a daily 17:30 calendar event. It records how hard the workout felt (effort/RPE 1–10, only on days with a workout), muscle soreness 0–10, tiredness 0–10, any pain (area), and illness. There is one check-in per day; saving again updates it. It is stored in the `feedback` table, which the safety rules already read: **pain means no intensity, illness means rest**.
- **Coach's take:** effort ≥ 7 on an easy or long run is a warning ("felt hard for an easy run"). Effort ≤ 4 there is good. A quality session at ≥ 9 means keep the next two days easy; at ≤ 5 the paces may be conservative. Soreness ≥ 6, pain, or illness add their own notes.
- **Weekly review:** number of check-ins, average effort on easy and long days (≥ 6 is flagged), average soreness (≥ 6) and tiredness (≥ 7), and pain days with their areas. Suggestions: pain means no quality sessions; worn down means easy until Wednesday.

## Morning brief (`coach/morning_brief.py`, 05:30 calendar event + Today tab)

Built for training days only (no brief on rest days). It covers:
- **The plan:** the session text, distance, pace zone and warm-up.
  - **Quality:** the target pace from the session, or effort-based guidance for hill sprints.
  - **Gym:** the A/B/C workout with sets, reps and RIR, plus the current volume note.
  - **Long ≥ 14 km:** fuelling.
- **Recovery:** last night's sleep, HRV status and resting HR. If today isn't synced yet, it uses the night before and says so.
- **Adjustments, in order:**
  1. The safety rules: a quality session becomes an easy run; red readiness suggests rest.
  2. Yesterday's check-in: pain avoids the painful load; soreness ≥ 6 trims leg work or starts very easy; tiredness ≥ 7 means controlled effort.
- **Focus:** one line. If your easy runs over the last 14 days averaged more than 10 s/km faster than the easy range, it reminds you to stay in range.

It is posted as one event, "☀️ Today: …", at 05:30–05:45, by the 05:25 scheduled task (which wakes the PC from sleep) or by the dashboard, between 04:30 and 09:00, once per day.

## References
- Banister, E. W. (1991). Modeling elite athletic performance.
- Foster, C. et al. (2001). A new approach to monitoring exercise training. *J Strength Cond Res*.
- Williams, S. et al. (2017). Better way to determine the acute:chronic workload ratio? *Br J Sports Med*.
- Lolli, L. et al. (2019). Mathematical coupling causes spurious correlation within the conventional acute-to-chronic workload ratio calculations. *Br J Sports Med*.
- Impellizzeri, F. M. et al. (2020). Acute:chronic workload ratio: conceptual issues and fundamental pitfalls. *Int J Sports Physiol Perform*.
- Plews, D. J. et al. (2013). Training adaptation and heart rate variability in elite endurance athletes. *Sports Med*.
- Saw, A. E., Main, L. C., & Gastin, P. B. (2016). Monitoring the athlete training response: subjective self-reported measures trump commonly used objective measures. *Br J Sports Med*.

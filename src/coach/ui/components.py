"""HTML card builders for the Today tab.

Security rule: these return app-authored markup. EVERY interpolated value goes through
`_e()` (html.escape), so data can never inject markup or scripts. Never pass external free
text (activity names, calendar titles) here; use our own labels and numbers.
"""

from __future__ import annotations

import html
from collections.abc import Iterable, Sequence

from coach.insights import Insight

_ICON = {"good": "✓", "note": "i", "warn": "!"}

# Icon badges and accent colours for cards and sections, chosen from the label's first words.
_ICONS = {
    "today": "⚡",
    "tomorrow": "🗓️",
    "recovery": "💚",
    "goal": "🎯",
    "last week": "📋",
    "recent workouts": "🏃",
    "chat": "💬",
    "weekly": "📋",
}
_CARD_TONE = {"today": "blue", "tomorrow": "violet", "recovery": "green", "goal": "pink"}


def _icon_for(label: str) -> str:
    low = label.lower()
    return next((icon for key, icon in _ICONS.items() if low.startswith(key)), "✦")


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _compact(markup: str) -> str:
    # Streamlit's markdown treats indented lines as code; keep HTML on single lines.
    return "".join(line.strip() for line in markup.splitlines())


def insights_list(insights: Iterable[Insight]) -> str:
    items = "".join(
        f'<li class="{_e(i.tone)}"><span class="i">{_e(_ICON[i.tone])}</span>'
        f"<span>{_e(i.text)}</span></li>"
        for i in insights
    )
    return f'<ul class="ac-insights">{items}</ul>' if items else ""


def hero(
    eyebrow: str,
    big: str,
    sub: str,
    readiness: str,
    ring_pct: float | None,
    ring_label: str,
    chips: Sequence[tuple[str, str]] = (),
) -> str:
    """chips: (icon, text) quick-glance facts under the countdown."""
    chips_html = "".join(
        f'<span class="ac-hchip"><span>{_e(i)}</span>{_e(t)}</span>' for i, t in chips
    )
    pill = {"green": "Ready to train", "amber": "Take it easier", "red": "Recover today"}.get(
        readiness, readiness
    )
    ring = ""
    if ring_pct is not None:
        pct = max(0.0, min(100.0, ring_pct))
        ring = (
            f'<div class="ac-ring" style="--pct:{pct:.0f}"><div><div>'
            f'<div class="v">{pct:.0f}%</div>'
            f'<div class="l">{_e(ring_label)}</div></div></div></div>'
        )
    return _compact(f"""
<div class="ac-hero">
  <div style="z-index:1">
    <div class="eyebrow">{_e(eyebrow)}</div>
    <div class="big">{_e(big)}</div>
    <div class="sub">{_e(sub)}</div>
    {f'<div class="ac-hchips">{chips_html}</div>' if chips_html else ""}
  </div>
  <div class="right">
    <span class="ac-pill {_e(readiness)}"><span class="dot"></span>{_e(pill)}</span>
    {ring}
  </div>
</div>""")


def card(
    label: str,
    title: str,
    desc: str = "",
    stats: Sequence[tuple[str, str, str]] = (),
    insights: Iterable[Insight] = (),
    progress_pct: float | None = None,
    icon: str | None = None,
) -> str:
    """stats: (value, label, sub) triples. The icon badge defaults from the label."""
    stats_html = "".join(
        f'<div class="ac-stat"><div class="v">{_e(v)}</div><div class="l">{_e(lbl)}</div>'
        f'<div class="s">{_e(s)}</div></div>'
        for v, lbl, s in stats
    )
    bar = ""
    if progress_pct is not None:
        width = max(0.0, min(100.0, progress_pct))
        bar = f'<div class="ac-bar"><div style="width:{width:.0f}%"></div></div>'
    icon = icon or _icon_for(label)
    tone = next((t for k, t in _CARD_TONE.items() if label.lower().startswith(k)), "blue")
    return _compact(f"""
<div class="ac-card t-{tone}">
  <div class="ac-card-head"><span class="ac-badge">{_e(icon)}</span><h4>{_e(label)}</h4></div>
  <div class="title">{_e(title)}</div>
  {f'<div class="desc">{_e(desc)}</div>' if desc else ""}
  {bar}
  {f'<div class="ac-stats">{stats_html}</div>' if stats_html else ""}
  {insights_list(insights)}
</div>""")


def section(title: str, note: str = "") -> str:
    return (
        f'<div class="ac-section"><h3><span class="ac-sec-ico">{_e(_icon_for(title))}</span>'
        f"{_e(title)}</h3><span>{_e(note)}</span></div>"
    )


def workout(
    family: str,
    icon: str,
    name: str,
    when: str,
    chips: Sequence[tuple[str, str]],
    tag: str | None,
    tag_extra: bool,
    insights: Iterable[Insight],
) -> str:
    chips_html = "".join(f'<span class="ac-chip">{_e(k)} <b>{_e(v)}</b></span>' for k, v in chips)
    tag_html = (
        f'<span class="ac-tag{" extra" if tag_extra else ""}">{_e(tag)}</span>' if tag else ""
    )
    return _compact(f"""
<div class="ac-workout {_e(family)}">
  <div class="ico">{_e(icon)}</div>
  <div>
    <div class="head"><div><div class="name">{_e(name)}</div>
    <div class="when">{_e(when)}</div></div>
    <div>{tag_html}</div></div>
    <div class="ac-chips">{chips_html}</div>
    {insights_list(insights)}
  </div>
</div>""")


_STATUS_ICON = {"done": "✓", "today": "●", "upcoming": "○", "missed": "✕", "rest": "–"}


def program_banner(
    kicker: str,
    headline: str,
    goal_line: str,
    weeks_label: str,
    weeks_pct: float,
    days: Sequence[dict[str, str]],
    km_label: str,
    km_pct: float | None,
    suggestions: Iterable[Insight],
) -> str:
    """days: dicts with keys day, date, title, detail, kind, status."""
    day_html = "".join(
        f'<div class="ac-day {_e(d["status"])}"><div class="d">'
        f"<span>{_e(d['day'])} {_e(d['date'])}</span>"
        f'<span class="s">{_e(_STATUS_ICON.get(d["status"], ""))}</span></div>'
        f'<div class="t">{_e(d["title"])}</div><div class="x">{_e(d["detail"])}</div>'
        f'<span class="k {_e(d["kind"])}">{_e(d["kind"])}</span></div>'
        for d in days
    )
    km_bar = ""
    if km_pct is not None:
        km_bar = (
            f'<div class="ac-sub">{_e(km_label)}</div><div class="ac-bar"><div style="width:'
            f'{max(0.0, min(100.0, km_pct)):.0f}%"></div></div>'
        )
    sugg = insights_list(suggestions)
    return _compact(f"""
<div class="ac-program">
  <div class="top">
    <div><div class="kicker">{_e(kicker)}</div><div class="headline">{_e(headline)}</div>
    <div class="goal">{_e(goal_line)}</div></div>
    <div class="weeks">{_e(weeks_label)}<div class="ac-bar">
    <div style="width:{max(0.0, min(100.0, weeks_pct)):.0f}%"></div></div></div>
  </div>
  <div class="ac-days">{day_html}</div>
  {km_bar}
  {f'<div class="ac-sub">Suggestions for this week</div>{sugg}' if sugg else ""}
</div>""")


def table(
    headers: Sequence[str],
    rows: Sequence[Sequence[object]],
    row_classes: Sequence[str] | None = None,
) -> str:
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join(
        f'<tr class="{_e(row_classes[i]) if row_classes else ""}">'
        + "".join(f"<td>{_e('' if c is None else c)}</td>" for c in row)
        + "</tr>"
        for i, row in enumerate(rows)
    )
    return (
        f'<div class="ac-table-wrap"><table class="ac-table"><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def heading(text: str, para: str = "") -> str:
    return f'<div class="ac-h">{_e(text)}</div>' + (
        f'<div class="ac-p">{_e(para)}</div>' if para else ""
    )


def _stats(stats: Sequence[tuple[str, str, str]]) -> str:
    """stats: (label, value, sub) triples."""
    cells = "".join(
        f'<div class="ac-stat"><div class="v">{_e(v)}</div><div class="l">{_e(lbl)}</div>'
        f'<div class="s">{_e(s)}</div></div>'
        for lbl, v, s in stats
    )
    return f'<div class="ac-stats">{cells}</div>' if cells else ""


def analysis_banner(
    kicker: str,
    tag: str,
    tone: str,
    headline: str,
    planned: str | None,
    stats: Sequence[tuple[str, str, str]],
    points: Iterable[Insight],
    next_step: Insight | None,
) -> str:
    """'Coach's take' on the latest workout."""
    nxt = (
        f'<div class="ac-next {_e(next_step.tone)}"><b>Next</b> {_e(next_step.text)}</div>'
        if next_step
        else ""
    )
    plan = f'<div class="plan">Planned: {_e(planned)}</div>' if planned else ""
    return _compact(f"""
<div class="ac-analysis {_e(tone)}">
  <div class="top"><div class="kicker">{_e(kicker)}</div>
  <span class="ac-verdict {_e(tone)}">{_e(tag)}</span></div>
  <div class="headline">{_e(headline)}</div>
  {plan}
  {_stats(stats)}
  {insights_list(points)}
  {nxt}
</div>""")


def review_card(
    kicker: str,
    headline: str,
    tone: str,
    stats: Sequence[tuple[str, str, str]],
    went_well: Iterable[Insight],
    watch: Iterable[Insight],
    next_title: str,
    next_lines: Sequence[tuple[str, str]],
    suggestions: Iterable[Insight],
    footer: str,
) -> str:
    """The weekly review: this week, next week's sessions, suggestions."""
    good_html, watch_html = insights_list(went_well), insights_list(watch)
    days = "".join(
        f'<div class="row"><span class="d">{_e(d)}</span><span>{_e(t)}</span></div>'
        for d, t in next_lines
    )
    cols = ""
    if good_html:
        cols += f'<div><div class="ac-sub">What went well</div>{good_html}</div>'
    if watch_html:
        cols += f'<div><div class="ac-sub">To watch</div>{watch_html}</div>'
    return _compact(f"""
<div class="ac-review {_e(tone)}">
  <div class="kicker">{_e(kicker)}</div>
  <div class="headline">{_e(headline)}</div>
  {_stats(stats)}
  {f'<div class="cols">{cols}</div>' if cols else ""}
  <div class="next">
    <div class="ac-sub">{_e(next_title)}</div>
    <div class="days">{days}</div>
  </div>
  <div class="ac-sub">Suggestions for next week</div>
  {insights_list(suggestions)}
  <div class="foot">{_e(footer)}</div>
</div>""")


# --- page header and sidebar -------------------------------------------------------------


def page_header(greeting: str, subtitle: str, badge: str) -> str:
    return _compact(f"""
<div class="ac-top">
  <div><div class="hello">{_e(greeting)}</div><div class="subt">{_e(subtitle)}</div></div>
  <span class="ac-free">{_e(badge)}</span>
</div>""")


def side_brand(name: str, tagline: str) -> str:
    return _compact(f"""
<div class="ac-brand">
  <div class="logo">🏃</div>
  <div><div class="name">{_e(name)}</div><div class="tag">{_e(tagline)}</div></div>
</div>""")


def side_week(
    kicker: str,
    title: str,
    km_done: float,
    km_target: float | None,
    weeks_pct: float,
    countdown: str,
    race: str,
) -> str:
    km_pct = min(100.0, 100 * km_done / km_target) if km_target else 0.0
    plan_pct = max(0.0, min(100.0, weeks_pct))
    target = f" / {km_target:g} km" if km_target else " km"
    return _compact(f"""
<div class="ac-sidecard">
  <div class="k">{_e(kicker)}</div>
  <div class="t">{_e(title)}</div>
  <div class="row"><span>This week</span><b>{km_done:.1f}{_e(target)}</b></div>
  <div class="ac-bar thin"><div style="width:{km_pct:.0f}%"></div></div>
  <div class="row"><span>Plan progress</span><b>{weeks_pct:.0f}%</b></div>
  <div class="ac-bar thin alt"><div style="width:{plan_pct:.0f}%"></div></div>
  <div class="count"><span class="n">{_e(countdown)}</span><span class="r">{_e(race)}</span></div>
</div>""")


def side_status(rows: Sequence[tuple[str, str, str]], title: str = "Live data") -> str:
    """rows: (label, status text, state) with state in ok | stale | off | err."""
    items = "".join(
        f'<div class="srow"><span class="dot {_e(state)}"></span><span class="lbl">{_e(lbl)}</span>'
        f'<span class="st">{_e(text)}</span></div>'
        for lbl, text, state in rows
    )
    return f'<div class="ac-sidecard status"><div class="k">{_e(title)}</div>{items}</div>'


def side_label(text: str, note: str = "") -> str:
    return (
        f'<div class="ac-sidelabel">{_e(text)}'
        + (f"<span>{_e(note)}</span>" if note else "")
        + "</div>"
    )


def brief_card(
    kicker: str,
    title: str,
    plan: Sequence[str],
    readiness: Sequence[str],
    adjustments: Iterable[Insight],
    focus: Insight | None,
) -> str:
    """The morning brief: the plan for today and how to approach it."""
    plan_html = "".join(f"<li>{_e(p)}</li>" for p in plan)
    ready = " · ".join(readiness)
    return _compact(f"""
<div class="ac-brief">
  <div class="kicker">{_e(kicker)}</div>
  <div class="headline">{_e(title)}</div>
  {f'<ul class="plan">{plan_html}</ul>' if plan_html else ""}
  {f'<div class="ready">🌙 {_e(ready)}</div>' if ready else ""}
  {insights_list(adjustments)}
  {f'<div class="ac-next {_e(focus.tone)}"><b>Focus</b> {_e(focus.text)}</div>' if focus else ""}
</div>""")


def checkin_done(summary: str) -> str:
    return (
        '<div class="ac-checked"><span class="tick">✓</span>'
        f"<span><b>Checked in today.</b> {_e(summary)}</span></div>"
    )

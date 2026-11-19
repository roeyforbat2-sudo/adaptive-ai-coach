"""App-wide CSS. Static, app-authored only: no data is ever interpolated here."""

from __future__ import annotations

import streamlit as st

# Design tokens (dark). Series colors come from the validated dataviz palette (dark steps).
RUN = "#3987e5"
STRENGTH = "#d95926"
GOOD = "#0ca30c"
WARN = "#fab219"
CRITICAL = "#d03b3b"

CSS = """
<style>
:root {
  --bg: #0e1014; --card: #16181d; --card-2: #1c1f26; --line: rgba(255,255,255,0.08);
  --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
  --run: #3987e5; --strength: #d95926; --good: #0ca30c; --warn: #fab219; --critical: #d03b3b;
  --accent-1: #3987e5; --accent-2: #9085e9; --accent-3: #d55181;
  --radius: 16px;
  --font: "Segoe UI Variable Display", "Segoe UI", system-ui, -apple-system, sans-serif;
}
html, body, [class*="css"] { font-family: var(--font); }
.stApp { background:
  radial-gradient(1200px 500px at 85% -10%, rgba(144,133,233,0.10), transparent 60%),
  radial-gradient(900px 400px at -10% 0%, rgba(57,135,229,0.10), transparent 60%),
  var(--bg); }
.block-container { padding-top: 1.6rem; max-width: 1280px; }
header[data-testid="stHeader"] { background: transparent; }
section[data-testid="stSidebar"] { background: #111318; border-right: 1px solid var(--line); }

/* tabs as pills (Streamlit 1.67: react-aria tabs) */
.stTabs [role="tablist"] { gap: 6px; border: none !important; box-shadow: none !important;
  flex-wrap: wrap; padding-bottom: 6px; }
.stTabs [data-testid="stTab"] { background: var(--card); border: 1px solid var(--line);
  border-radius: 999px; padding: 6px 16px; transition: all .15s ease; }
.stTabs [data-testid="stTab"]:hover { border-color: rgba(255,255,255,.22); }
.stTabs [data-testid="stTab"][aria-selected="true"] {
  background: linear-gradient(135deg, var(--accent-1), var(--accent-2)); border-color: transparent; }
.stTabs [data-testid="stTab"][aria-selected="true"] p { color: #fff !important; font-weight: 600; }
.stTabs .react-aria-SelectionIndicator { display: none !important; }

/* page title */
h1 { font-size: 1.7rem !important; letter-spacing: -0.02em; font-weight: 800 !important;
  background: linear-gradient(90deg, #f0efec, #c4bdf5); -webkit-background-clip: text;
  background-clip: text; color: transparent !important; }

/* native metrics as cards */
div[data-testid="stMetric"] { background: var(--card); border: 1px solid var(--line) !important;
  border-radius: var(--radius); padding: 14px 16px; }
div[data-testid="stMetricLabel"] p { color: var(--muted); font-size: 0.8rem;
  text-transform: uppercase; letter-spacing: .06em; }
div[data-testid="stMetricValue"] { font-weight: 700; }
div[data-testid="stPlotlyChart"], div[data-testid="stDataFrame"] { border-radius: var(--radius);
  overflow: hidden; border: 1px solid var(--line); }
div[data-testid="stExpander"] details { background: var(--card); border: 1px solid var(--line);
  border-radius: var(--radius); }

/* custom components */
.ac-hero { position: relative; overflow: hidden; border-radius: 24px; padding: 28px 32px;
  background: linear-gradient(120deg, #1f3b8a 0%, #3b2f8f 45%, #7a2a63 100%);
  box-shadow: 0 20px 60px rgba(0,0,0,.35); margin-bottom: 18px; display: flex;
  align-items: center; justify-content: space-between; gap: 24px; flex-wrap: wrap; }
.ac-hero::after { content: ""; position: absolute; right: -80px; top: -80px; width: 320px;
  height: 320px; border-radius: 50%; background: rgba(255,255,255,.06); }
.ac-hero .eyebrow { color: rgba(255,255,255,.75); font-size: .85rem; letter-spacing: .12em;
  text-transform: uppercase; }
.ac-hero .big { color: #fff; font-size: clamp(2.2rem, 5vw, 3.6rem); font-weight: 800;
  line-height: 1.05; margin: 6px 0 4px; }
.ac-hero .sub { color: rgba(255,255,255,.85); font-size: 1.05rem; }
.ac-hero .right { display: flex; align-items: center; gap: 22px; z-index: 1; }

.ac-ring { --pct: 0; width: 132px; height: 132px; border-radius: 50%;
  background: conic-gradient(#fff calc(var(--pct) * 1%), rgba(255,255,255,.18) 0);
  display: grid; place-items: center; }
.ac-ring > div { width: 104px; height: 104px; border-radius: 50%; background: #2a2a6e;
  display: grid; place-items: center; text-align: center; color: #fff; }
.ac-ring .v { font-size: 1.6rem; font-weight: 800; line-height: 1; }
.ac-ring .l { font-size: .68rem; color: rgba(255,255,255,.75); text-transform: uppercase;
  letter-spacing: .08em; margin-top: 4px; }

.ac-pill { display: inline-flex; align-items: center; gap: 8px; padding: 8px 14px;
  border-radius: 999px; font-weight: 700; font-size: .95rem; }
.ac-pill.green { background: rgba(12,163,12,.18); color: #7be07b; border: 1px solid rgba(12,163,12,.45); }
.ac-pill.amber { background: rgba(250,178,25,.16); color: #ffd27a; border: 1px solid rgba(250,178,25,.45); }
.ac-pill.red { background: rgba(208,59,59,.18); color: #ff9d9d; border: 1px solid rgba(208,59,59,.5); }
.ac-pill .dot { width: 9px; height: 9px; border-radius: 50%; background: currentColor; }

.ac-card { background: var(--card); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 18px 20px; height: 100%; min-width: 0; overflow: hidden; margin-bottom: 12px; }
.ac-card h4 { margin: 0 0 4px; font-size: .78rem; color: var(--muted); text-transform: uppercase;
  letter-spacing: .08em; font-weight: 600; }
.ac-card .title { font-size: 1.35rem; font-weight: 700; color: var(--ink); margin: 2px 0 6px; }
.ac-card .desc { color: var(--ink-2); font-size: .95rem; line-height: 1.45; }
.ac-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(92px, 1fr)); gap: 10px; margin-top: 10px; }
.ac-stat { background: var(--card-2); border-radius: 12px; padding: 10px 12px; min-width: 0; }
.ac-stat .v { white-space: nowrap; }
.ac-stat .v { font-size: 1.25rem; font-weight: 700; color: var(--ink); }
.ac-stat .l { font-size: .72rem; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; }
.ac-stat .s { font-size: .75rem; color: var(--ink-2); }

.ac-bar { height: 8px; border-radius: 99px; background: var(--card-2); overflow: hidden; margin: 10px 0 4px; }
.ac-bar > div { height: 100%; border-radius: 99px;
  background: linear-gradient(90deg, var(--accent-1), var(--accent-2), var(--accent-3)); }

.ac-insights { list-style: none; padding: 0 !important; margin: 10px 0 0 !important; display: grid; gap: 8px; }
.ac-insights li { margin: 0 !important; }
.ac-insights li { display: flex; gap: 10px; align-items: flex-start; padding: 9px 12px;
  border-radius: 12px; background: var(--card-2); color: var(--ink-2); font-size: .92rem; line-height: 1.4; }
.ac-insights li .i { flex: 0 0 auto; width: 22px; height: 22px; border-radius: 50%; display: grid;
  place-items: center; font-size: .8rem; font-weight: 800; color: #0e1014; margin-top: 1px; }
.ac-insights li.good .i { background: #4cc44c; }
.ac-insights li.note .i { background: #8fb8ee; }
.ac-insights li.warn .i { background: var(--warn); }

.ac-section { display: flex; align-items: baseline; justify-content: space-between;
  margin: 26px 0 10px; }
.ac-section h3 { margin: 0; font-size: 1.15rem; font-weight: 700; color: var(--ink); }
.ac-section span { color: var(--muted); font-size: .85rem; }

.ac-workout { display: grid; grid-template-columns: 56px 1fr; gap: 14px; background: var(--card);
  border: 1px solid var(--line); border-radius: var(--radius); padding: 16px 18px; margin-bottom: 10px;
  border-left: 4px solid var(--muted); }
.ac-workout.run { border-left-color: var(--run); }
.ac-workout.strength { border-left-color: var(--strength); }
.ac-workout .ico { width: 48px; height: 48px; border-radius: 14px; display: grid; place-items: center;
  font-size: 1.5rem; background: var(--card-2); }
.ac-workout .head { display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.ac-workout .name { font-weight: 700; color: var(--ink); font-size: 1.05rem; }
.ac-workout .when { color: var(--muted); font-size: .85rem; }
.ac-chips { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
.ac-chip { background: var(--card-2); border-radius: 999px; padding: 4px 10px; font-size: .82rem;
  color: var(--ink-2); }
.ac-chip b { color: var(--ink); }
.ac-tag { font-size: .72rem; padding: 3px 9px; border-radius: 999px; font-weight: 600;
  background: rgba(57,135,229,.16); color: #9ec5f4; }
.ac-tag.extra { background: rgba(144,133,233,.18); color: #c4bdf5; }

/* training program banner */
.ac-program { background: linear-gradient(180deg, rgba(57,135,229,.10), rgba(144,133,233,.06)), var(--card);
  border: 1px solid rgba(144,133,233,.30); border-radius: 20px; padding: 20px 22px; margin: 6px 0 14px; }
.ac-program .top { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; }
.ac-program .kicker { font-size: .78rem; color: #c4bdf5; text-transform: uppercase; letter-spacing: .1em; font-weight: 700; }
.ac-program .headline { font-size: 1.5rem; font-weight: 800; color: var(--ink); margin: 4px 0 2px; }
.ac-program .goal { color: var(--ink-2); font-size: .92rem; }
.ac-program .weeks { min-width: 180px; text-align: right; color: var(--muted); font-size: .8rem; }
.ac-days { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 8px; margin: 16px 0 6px; }
.ac-day { background: var(--card-2); border: 1px solid var(--line); border-radius: 14px; padding: 10px 10px 12px; min-width: 0; }
.ac-day .d { font-size: .72rem; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; display: flex; justify-content: space-between; }
.ac-day .t { font-weight: 700; color: var(--ink); font-size: .88rem; margin-top: 4px; line-height: 1.25; }
.ac-day .x { color: var(--ink-2); font-size: .75rem; margin-top: 4px; line-height: 1.3; overflow-wrap: anywhere; }
.ac-day.done { border-color: rgba(12,163,12,.55); }
.ac-day.today { border-color: var(--accent-1); box-shadow: 0 0 0 2px rgba(57,135,229,.25) inset; }
.ac-day.missed { border-color: rgba(208,59,59,.55); }
.ac-day.rest { opacity: .55; }
.ac-day .s { font-size: .9rem; }
.ac-day.done .s { color: #4cc44c; } .ac-day.missed .s { color: #ff8a8a; } .ac-day.today .s { color: #8fb8ee; }
.ac-day .k { display: inline-block; margin-top: 6px; font-size: .68rem; padding: 2px 7px; border-radius: 99px; background: rgba(255,255,255,.06); color: var(--ink-2); }
.ac-day .k.quality, .ac-day .k.test, .ac-day .k.race { background: rgba(213,81,129,.18); color: #f0a3c0; }
.ac-day .k.easy, .ac-day .k.long { background: rgba(57,135,229,.16); color: #9ec5f4; }
.ac-day .k.strength { background: rgba(217,89,38,.18); color: #f3a07c; }
@media (max-width: 900px) { .ac-days { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
@media (max-width: 560px) { .ac-days { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.ac-sub { font-size: .8rem; color: var(--muted); text-transform: uppercase; letter-spacing: .08em; margin: 14px 0 0; font-weight: 600; }

/* program tables */
.ac-table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 14px; margin: 8px 0 16px; }
table.ac-table { width: 100%; border-collapse: collapse; font-size: .86rem; }
table.ac-table th { text-align: left; color: var(--muted); font-weight: 600; text-transform: uppercase; font-size: .72rem;
  letter-spacing: .06em; background: var(--card-2); padding: 9px 12px; border-bottom: 1px solid var(--line); }
table.ac-table td { padding: 9px 12px; border-bottom: 1px solid var(--line); color: var(--ink-2); vertical-align: top; }
table.ac-table tr:last-child td { border-bottom: none; }
table.ac-table tr.current td { background: rgba(57,135,229,.12); color: var(--ink); }
table.ac-table tr.current td:first-child { box-shadow: inset 3px 0 0 var(--accent-1); }
table.ac-table tr.past td { opacity: .6; }
.ac-h { font-size: 1rem; font-weight: 700; color: var(--ink); margin: 18px 0 2px; }
.ac-p { color: var(--ink-2); font-size: .88rem; margin: 2px 0 6px; }

/* coach's take (latest workout) */
.ac-analysis { position: relative; border-radius: 20px; padding: 20px 22px; margin: 4px 0 16px;
  background: linear-gradient(135deg, rgba(57,135,229,.14), rgba(144,133,233,.08) 60%, rgba(213,81,129,.06)), var(--card);
  border: 1px solid rgba(144,133,233,.35); }
.ac-analysis.warn { border-color: rgba(250,178,25,.45); }
.ac-analysis .top { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
.ac-analysis .kicker, .ac-review .kicker { font-size: .78rem; color: #c4bdf5; text-transform: uppercase;
  letter-spacing: .1em; font-weight: 700; }
.ac-analysis .headline { font-size: 1.3rem; font-weight: 800; color: var(--ink); margin: 6px 0 2px; line-height: 1.3; }
.ac-analysis .plan { color: var(--muted); font-size: .88rem; }
.ac-verdict { font-size: .78rem; font-weight: 700; padding: 4px 11px; border-radius: 999px; }
.ac-verdict.good { background: rgba(12,163,12,.18); color: #7be07b; border: 1px solid rgba(12,163,12,.45); }
.ac-verdict.note { background: rgba(57,135,229,.16); color: #9ec5f4; border: 1px solid rgba(57,135,229,.4); }
.ac-verdict.warn { background: rgba(250,178,25,.16); color: #ffd27a; border: 1px solid rgba(250,178,25,.45); }
.ac-next { margin-top: 12px; padding: 10px 14px; border-radius: 12px; background: rgba(144,133,233,.12);
  color: var(--ink-2); font-size: .93rem; border-left: 3px solid var(--accent-2); }
.ac-next.warn { border-left-color: var(--warn); }
.ac-next b { color: var(--ink); margin-right: 6px; text-transform: uppercase; font-size: .75rem; letter-spacing: .08em; }

/* weekly review */
.ac-review { border-radius: 20px; padding: 20px 22px; margin: 4px 0 16px; background: var(--card);
  border: 1px solid var(--line); border-top: 3px solid var(--accent-2); }
.ac-review.good { border-top-color: var(--good); }
.ac-review.warn { border-top-color: var(--warn); }
.ac-review .headline { font-size: 1.25rem; font-weight: 800; color: var(--ink); margin: 6px 0 4px; }
.ac-review .cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }
.ac-review .next { margin: 6px 0 2px; }
.ac-review .days { display: grid; gap: 4px; margin-top: 8px; }
.ac-review .row { display: flex; gap: 12px; font-size: .9rem; color: var(--ink-2); padding: 6px 10px;
  background: var(--card-2); border-radius: 10px; }
.ac-review .row .d { color: var(--muted); min-width: 92px; font-variant-numeric: tabular-nums; }
.ac-review .foot { margin-top: 12px; color: var(--muted); font-size: .8rem; }

/* ===================== v2: eye-catching layer (overrides above) ===================== */
.stApp { background:
  radial-gradient(900px 520px at 92% -8%, rgba(213,81,129,.16), transparent 60%),
  radial-gradient(1100px 600px at 40% -20%, rgba(144,133,233,.16), transparent 60%),
  radial-gradient(800px 500px at -8% 10%, rgba(57,135,229,.18), transparent 60%),
  radial-gradient(700px 600px at 100% 100%, rgba(12,163,12,.06), transparent 60%),
  var(--bg) fixed; }
.block-container { padding-top: 1.1rem; }

/* page header */
.ac-top { display: flex; justify-content: space-between; align-items: center; gap: 12px;
  margin: 0 0 10px; flex-wrap: wrap; }
.ac-top .hello { font-size: 1.75rem; font-weight: 800; letter-spacing: -0.02em;
  background: linear-gradient(90deg, #ffffff 10%, #c4bdf5 55%, #f0a3c0); -webkit-background-clip: text;
  background-clip: text; color: transparent; line-height: 1.15; }
.ac-top .subt { color: var(--muted); font-size: .9rem; margin-top: 2px; }
.ac-free { font-size: .78rem; font-weight: 700; color: #c4bdf5; padding: 6px 12px; border-radius: 999px;
  background: rgba(144,133,233,.12); border: 1px solid rgba(144,133,233,.35); white-space: nowrap; }

/* tabs: icon pills with a glow on the active one */
.stTabs [role="tablist"] { gap: 8px; padding: 4px 0 10px; }
.stTabs [data-testid="stTab"] { background: rgba(22,24,29,.75); backdrop-filter: blur(6px);
  padding: 7px 16px; }
.stTabs [data-testid="stTab"] p { font-weight: 600; }
.stTabs [data-testid="stTab"][aria-selected="true"] {
  background: linear-gradient(135deg, #3987e5, #9085e9 60%, #d55181);
  box-shadow: 0 6px 22px rgba(144,133,233,.35); }

/* hero: slow-moving gradient, glow, quick-glance chips */
@keyframes ac-shift { 0% { background-position: 0% 50%; } 50% { background-position: 100% 50%; }
  100% { background-position: 0% 50%; } }
.ac-hero { background: linear-gradient(120deg, #1f3b8a, #3b2f8f, #7a2a63, #3b2f8f, #1f3b8a);
  background-size: 300% 300%; animation: ac-shift 18s ease infinite;
  box-shadow: 0 24px 70px rgba(59,47,143,.45), inset 0 1px 0 rgba(255,255,255,.12);
  border: 1px solid rgba(255,255,255,.10); }
.ac-hero::before { content: ""; position: absolute; left: -60px; bottom: -120px; width: 260px; height: 260px;
  border-radius: 50%; background: rgba(57,135,229,.25); filter: blur(30px); }
.ac-hero .big { text-shadow: 0 4px 30px rgba(0,0,0,.25); }
.ac-hero > div:first-child { flex: 1 1 380px; min-width: 0; }
.ac-hero .right { flex: 0 0 auto; }
@media (prefers-reduced-motion: reduce) {
  .ac-hero { animation: none; }
  .ac-card, .ac-workout, .ac-day { transition: none; }
}
.ac-hchips { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
.ac-hchip { display: inline-flex; gap: 6px; align-items: center; font-size: .85rem; font-weight: 600;
  color: #fff; padding: 6px 12px; border-radius: 999px; background: rgba(255,255,255,.12);
  border: 1px solid rgba(255,255,255,.18); backdrop-filter: blur(6px); }
.ac-ring { box-shadow: 0 0 40px rgba(255,255,255,.18); }
.ac-ring > div { background: radial-gradient(circle at 30% 30%, #3a3a8e, #24245f); }

/* cards: gradient border, icon badge, hover lift */
.ac-card { --c1: rgba(57,135,229,.55); --c2: rgba(144,133,233,.12);
  background: linear-gradient(180deg, rgba(255,255,255,.025), transparent 40%) padding-box,
    linear-gradient(var(--card), var(--card)) padding-box,
    linear-gradient(140deg, var(--c1), var(--c2) 45%, rgba(255,255,255,.04)) border-box;
  border: 1px solid transparent; border-radius: 18px;
  box-shadow: 0 10px 30px rgba(0,0,0,.25); transition: transform .18s ease, box-shadow .18s ease; }
.ac-card:hover { transform: translateY(-2px); box-shadow: 0 16px 40px rgba(0,0,0,.35); }
.ac-card.t-violet { --c1: rgba(144,133,233,.6); }
.ac-card.t-green { --c1: rgba(12,163,12,.55); --c2: rgba(12,163,12,.08); }
.ac-card.t-pink { --c1: rgba(213,81,129,.55); --c2: rgba(213,81,129,.08); }
.ac-card-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.ac-card-head h4 { margin: 0 !important; }
.ac-badge { width: 32px; height: 32px; border-radius: 10px; display: grid; place-items: center;
  font-size: 1rem; background: linear-gradient(135deg, rgba(57,135,229,.35), rgba(144,133,233,.25));
  box-shadow: inset 0 1px 0 rgba(255,255,255,.15); flex: 0 0 auto; }
.ac-card.t-green .ac-badge { background: linear-gradient(135deg, rgba(12,163,12,.4), rgba(57,135,229,.2)); }
.ac-card.t-pink .ac-badge { background: linear-gradient(135deg, rgba(213,81,129,.45), rgba(144,133,233,.25)); }
.ac-stat { background: linear-gradient(180deg, rgba(255,255,255,.04), rgba(255,255,255,.01)), var(--card-2);
  border: 1px solid rgba(255,255,255,.05); }
.ac-stat .v { font-size: 1.3rem; }

/* sections */
.ac-section h3 { display: flex; align-items: center; gap: 10px; font-size: 1.2rem; }
.ac-sec-ico { width: 30px; height: 30px; border-radius: 9px; display: grid; place-items: center; font-size: .95rem;
  background: linear-gradient(135deg, rgba(144,133,233,.35), rgba(213,81,129,.25)); }

/* coach's take + review get the same gradient border treatment */
.ac-analysis { background:
    linear-gradient(135deg, rgba(57,135,229,.16), rgba(144,133,233,.10) 55%, rgba(213,81,129,.08)) padding-box,
    linear-gradient(var(--card), var(--card)) padding-box,
    linear-gradient(120deg, #3987e5, #9085e9 50%, #d55181) border-box;
  border: 1px solid transparent; box-shadow: 0 18px 50px rgba(57,135,229,.15); }
.ac-analysis.warn { background:
    linear-gradient(135deg, rgba(250,178,25,.10), rgba(144,133,233,.08) 55%, transparent) padding-box,
    linear-gradient(var(--card), var(--card)) padding-box,
    linear-gradient(120deg, #fab219, #d55181 60%, #9085e9) border-box; }
.ac-analysis .headline { font-size: 1.4rem; }
.ac-review { box-shadow: 0 14px 40px rgba(0,0,0,.25); }

/* workouts: coloured icon tiles */
.ac-workout { box-shadow: 0 8px 24px rgba(0,0,0,.2); transition: transform .18s ease; }
.ac-workout:hover { transform: translateY(-2px); }
.ac-workout.run .ico { background: linear-gradient(135deg, rgba(57,135,229,.45), rgba(144,133,233,.25)); }
.ac-workout.strength .ico { background: linear-gradient(135deg, rgba(217,89,38,.5), rgba(213,81,129,.25)); }

/* program banner day tiles */
.ac-day { transition: transform .15s ease; }
.ac-day:hover { transform: translateY(-2px); }
.ac-day.done { background: linear-gradient(180deg, rgba(12,163,12,.12), transparent 70%), var(--card-2); }
.ac-day.today { background: linear-gradient(180deg, rgba(57,135,229,.22), transparent 80%), var(--card-2);
  box-shadow: 0 0 0 2px rgba(57,135,229,.35) inset, 0 8px 26px rgba(57,135,229,.25); }

/* native metrics (other tabs) */
div[data-testid="stMetric"] { background:
    linear-gradient(var(--card), var(--card)) padding-box,
    linear-gradient(140deg, rgba(57,135,229,.45), rgba(144,133,233,.1) 50%, rgba(255,255,255,.04)) border-box !important;
  border: 1px solid transparent !important; box-shadow: 0 8px 24px rgba(0,0,0,.2); }
div[data-testid="stMetricValue"] { font-size: 1.7rem; }

/* morning brief + check-in */
.ac-brief { border-radius: 20px; padding: 18px 22px; margin: 4px 0 16px;
  background: linear-gradient(135deg, rgba(250,178,25,.12), rgba(213,81,129,.08) 60%, transparent) padding-box,
    linear-gradient(var(--card), var(--card)) padding-box,
    linear-gradient(120deg, #fab219, #d55181 55%, #9085e9) border-box;
  border: 1px solid transparent; box-shadow: 0 16px 44px rgba(250,178,25,.10); }
.ac-brief .kicker { font-size: .78rem; color: #ffd27a; text-transform: uppercase; letter-spacing: .1em; font-weight: 700; }
.ac-brief .headline { font-size: 1.35rem; font-weight: 800; color: var(--ink); margin: 6px 0 8px; }
.ac-brief .plan { margin: 0 0 8px 1.1rem; padding: 0; color: var(--ink-2); font-size: .93rem; line-height: 1.55; }
.ac-brief .ready { color: var(--muted); font-size: .88rem; margin: 6px 0; }
.ac-checked { display: flex; gap: 12px; align-items: center; padding: 12px 16px; border-radius: 14px; margin: 4px 0 14px;
  background: rgba(12,163,12,.10); border: 1px solid rgba(12,163,12,.35); color: var(--ink-2); font-size: .93rem; }
.ac-checked .tick { width: 26px; height: 26px; border-radius: 50%; display: grid; place-items: center;
  background: #2fd12f; color: #0e1014; font-weight: 800; flex: 0 0 auto; }
div[data-testid="stForm"] { border-radius: 18px !important; border: 1px solid rgba(144,133,233,.35) !important;
  background: linear-gradient(135deg, rgba(57,135,229,.08), rgba(144,133,233,.05)), var(--card); }

/* ---------- sidebar ---------- */
section[data-testid="stSidebar"] { background:
  radial-gradient(400px 300px at 0% 0%, rgba(57,135,229,.16), transparent 70%),
  radial-gradient(400px 300px at 100% 100%, rgba(213,81,129,.10), transparent 70%), #0f1116; }
section[data-testid="stSidebar"] .block-container, section[data-testid="stSidebar"] [data-testid="stSidebarContent"] { padding-top: .6rem; }
.ac-brand { display: flex; align-items: center; gap: 12px; margin: 4px 0 14px; }
.ac-brand .logo { width: 44px; height: 44px; border-radius: 14px; display: grid; place-items: center; font-size: 1.4rem;
  background: linear-gradient(135deg, #3987e5, #9085e9 55%, #d55181); box-shadow: 0 8px 24px rgba(144,133,233,.4); }
.ac-brand .name { font-weight: 800; font-size: 1.15rem; color: var(--ink); letter-spacing: -0.01em; }
.ac-brand .tag { color: var(--muted); font-size: .78rem; }
.ac-sidecard { border-radius: 16px; padding: 14px 14px 12px; margin: 0 0 12px;
  background: linear-gradient(var(--card), var(--card)) padding-box,
    linear-gradient(140deg, rgba(57,135,229,.5), rgba(144,133,233,.1) 50%, rgba(213,81,129,.35)) border-box;
  border: 1px solid transparent; }
.ac-sidecard .k { font-size: .7rem; color: #c4bdf5; text-transform: uppercase; letter-spacing: .1em; font-weight: 700; }
.ac-sidecard .t { font-size: 1.1rem; font-weight: 800; color: var(--ink); margin: 2px 0 8px; }
.ac-sidecard .row { display: flex; justify-content: space-between; font-size: .8rem; color: var(--muted); margin-top: 6px; }
.ac-sidecard .row b { color: var(--ink); }
.ac-bar.thin { height: 6px; margin: 4px 0 2px; }
.ac-bar.alt > div { background: linear-gradient(90deg, #0ca30c, #3987e5); }
.ac-sidecard .count { display: flex; flex-direction: column; gap: 0; margin-top: 12px; padding-top: 10px;
  border-top: 1px solid var(--line); }
.ac-sidecard .count .n { line-height: 1.1; white-space: nowrap; }
.ac-sidecard .count .n { font-size: 1.5rem; font-weight: 800; background: linear-gradient(90deg, #fff, #f0a3c0);
  -webkit-background-clip: text; background-clip: text; color: transparent; }
.ac-sidecard .count .r { color: var(--muted); font-size: .8rem; }
.ac-sidecard.status .srow { display: flex; align-items: center; gap: 8px; font-size: .84rem; margin-top: 8px; }
.ac-sidecard.status .lbl { color: var(--ink-2); flex: 1; }
.ac-sidecard.status .st { color: var(--muted); font-size: .78rem; text-align: right; }
.ac-sidecard .dot { width: 9px; height: 9px; border-radius: 50%; flex: 0 0 auto; background: #555; }
.ac-sidecard .dot.ok { background: #2fd12f; box-shadow: 0 0 10px rgba(47,209,47,.8); }
.ac-sidecard .dot.stale { background: var(--warn); box-shadow: 0 0 8px rgba(250,178,25,.7); }
.ac-sidecard .dot.err { background: var(--critical); box-shadow: 0 0 8px rgba(208,59,59,.8); }
.ac-sidelabel { font-size: .72rem; color: #c4bdf5; text-transform: uppercase; letter-spacing: .1em; font-weight: 700;
  margin: 6px 0 6px; display: flex; flex-direction: column; gap: 2px; }
.ac-sidelabel span { color: var(--muted); text-transform: none; letter-spacing: 0; font-weight: 400; font-size: .75rem; }
section[data-testid="stSidebar"] button[kind="primary"], section[data-testid="stSidebar"] [data-testid="stBaseButton-primary"] {
  background: linear-gradient(135deg, #3987e5, #9085e9 60%, #d55181) !important; border: none !important;
  box-shadow: 0 8px 22px rgba(144,133,233,.35); font-weight: 700; }
section[data-testid="stSidebar"] [data-testid="stButtonGroup"] button { border-radius: 999px !important; }
section[data-testid="stSidebar"] [data-testid="stExpander"] details { background: transparent; border-color: var(--line); }

/* phone */
@media (max-width: 640px) {
  .block-container { padding: 3.2rem .75rem 2rem !important; }
  h1 { font-size: 1.35rem !important; }
  .stTabs [role="tablist"] { flex-wrap: nowrap; overflow-x: auto; scrollbar-width: none; }
  .stTabs [role="tablist"]::-webkit-scrollbar { display: none; }
  .stTabs [data-testid="stTab"] { flex: 0 0 auto; padding: 6px 12px; }
  .ac-hero { padding: 20px 18px; border-radius: 18px; gap: 16px; }
  .ac-hero .sub { font-size: .95rem; }
  .ac-hero .right { width: 100%; justify-content: space-between; gap: 12px; }
  .ac-ring { width: 104px; height: 104px; }
  .ac-ring > div { width: 82px; height: 82px; }
  .ac-ring .v { font-size: 1.3rem; }
  .ac-card { padding: 14px 15px; }
  .ac-card .title { font-size: 1.15rem; }
  .ac-program { padding: 16px 14px; }
  .ac-program .headline { font-size: 1.2rem; }
  .ac-program .weeks { min-width: 0; width: 100%; text-align: left; }
  .ac-workout { grid-template-columns: 40px 1fr; gap: 10px; padding: 12px 14px; }
  .ac-workout .ico { width: 38px; height: 38px; font-size: 1.2rem; border-radius: 11px; }
  .ac-section { flex-wrap: wrap; gap: 4px; }
  table.ac-table { font-size: .8rem; }
  div[data-testid="stMetric"] { padding: 10px 12px; }
}
</style>
"""


def inject() -> None:
    st.markdown(CSS, unsafe_allow_html=True)

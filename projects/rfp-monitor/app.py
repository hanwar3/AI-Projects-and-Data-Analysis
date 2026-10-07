"""
app.py -- the RFP Monitor dashboard.

Nothing runs on a timer. A check happens when you press Refresh here, or
when you run `python monitor.py`; both go through monitor.run_cycle().
"""
import hashlib
import html
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

import config
import db
import exports
from monitor import run_cycle
from sources import ALL_SOURCES

st.set_page_config(page_title="RFP Monitor", page_icon="📡", layout="wide")

ISO_DATE = r"^\d{4}-\d{2}-\d{2}$"
DB_COLS = ["date_found", "deadline", "score", "source", "agency", "title", "detail", "country",
           "type", "matched", "ref", "url", "posted", "triage", "notice_id"]
GRID_COLS = ["triage", "score", "closes_in", "deadline", "date_found", "source", "agency",
             "title", "country", "type", "matched", "url"]

CSS = """
<style>
@import url('https://api.fontshare.com/v2/css?f[]=satoshi@400,500,700,900&display=swap');
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&display=swap');

:root {
  --ink: #12110F; --surface: #1A1815; --surface-2: #211E1A; --line: #2E2A25;
  --text: #EDE8E0; --muted: #9A9288; --accent: #E0913F; --accent-soft: rgba(224,145,63,.13);
}
html, body, .stApp, button, input, textarea, select, [class*="st-"] {
  font-family: 'Satoshi', ui-sans-serif, system-ui, 'Segoe UI', sans-serif;
  font-variant-numeric: tabular-nums;
}
/* keep Streamlit's own glyphs on the icon font, or they print as words */
[data-testid="stIconMaterial"], [class*="material-symbols"], [class*="material-icons"],
[data-testid*="Icon"] > span, .stApp [data-testid="stExpanderIcon"] {
  font-family: 'Material Symbols Rounded', 'Material Icons', sans-serif !important;
}
.stApp { background: var(--ink); color: var(--text); }
/* barely-there grain so large flat areas are not sterile */
.stApp::before {
  content: ''; position: fixed; inset: 0; pointer-events: none; z-index: 0; opacity: .04;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3'/%3E%3C/filter%3E%3Crect width='160' height='160' filter='url(%23n)' opacity='.5'/%3E%3C/svg%3E");
}
[data-testid="stHeader"] { background: transparent; }
#MainMenu, footer { visibility: hidden; }
.block-container { padding-top: 2rem; padding-bottom: 3.5rem; max-width: 1560px; }

.app-head { display: flex; align-items: baseline; gap: .85rem; flex-wrap: wrap; }
.wordmark { font-size: 1.7rem; font-weight: 900; letter-spacing: -.035em; line-height: 1; }
.wordmark em { font-style: normal; color: var(--accent); }
.tagline { color: var(--muted); font-size: .85rem; }
.rule { height: 1px; background: var(--line); margin: 1.1rem 0 1.4rem; }

.stat-row {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(158px, 1fr)); gap: 1px;
  background: var(--line); border: 1px solid var(--line); border-radius: 14px;
  overflow: hidden; margin-bottom: 1.6rem;
}
.stat { background: var(--surface); padding: 1rem 1.15rem 1.05rem; display: flex; flex-direction: column; gap: .4rem; }
.stat-label { font-size: .68rem; letter-spacing: .15em; text-transform: uppercase; color: var(--muted); }
.stat-value { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 1.8rem; font-weight: 500; line-height: 1; letter-spacing: -.02em; }
.stat-note { font-size: .74rem; color: var(--muted); }
.stat.is-alert .stat-value, .stat.is-alert .stat-note { color: var(--accent); }

.chips { display: flex; flex-wrap: wrap; gap: .4rem; margin: .1rem 0 1rem; }
.chip { font-size: .72rem; padding: .28rem .58rem; border-radius: 8px; border: 1px solid var(--line);
        background: var(--surface); color: var(--muted); }
.chip.bad { color: var(--accent); border-color: rgba(224,145,63,.45); background: var(--accent-soft); }

.empty { border: 1px dashed var(--line); border-radius: 16px; padding: 2.2rem 2rem 2.4rem; max-width: 64ch; }
.empty h3 { font-size: 1.15rem; margin: 0 0 .5rem; letter-spacing: -.01em; }
.empty p { color: var(--muted); font-size: .9rem; line-height: 1.6; margin: 0 0 .4rem; }

.stButton > button {
  border-radius: 10px; border: 1px solid var(--line); background: var(--surface-2); color: var(--text);
  font-weight: 500; transition: transform .18s cubic-bezier(.2,.8,.2,1), background .18s, border-color .18s;
}
.stButton > button:hover { background: #282420; border-color: #3B352E; transform: translateY(-1px); }
.stButton > button:active { transform: translateY(1px) scale(.99); }
.stButton > button[kind="primary"], .stButton > button[data-testid="stBaseButton-primary"] {
  background: var(--accent); border-color: var(--accent); color: #1B1204; font-weight: 700;
}
.stButton > button[kind="primary"]:hover { background: #EDA354; border-color: #EDA354; }
.stDownloadButton > button { border-radius: 10px; border: 1px solid var(--line); background: var(--surface-2); color: var(--text); }

[data-testid="stSidebar"] { background: var(--surface); border-right: 1px solid var(--line); }
[data-testid="stSidebar"] h4 { font-size: .68rem; letter-spacing: .15em; text-transform: uppercase;
  color: var(--muted); margin: 1.1rem 0 .1rem; font-weight: 500; }
[data-testid="stDataFrame"], [data-testid="stDataEditor"] { border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }
[data-testid="stExpander"] { border: 1px solid var(--line); border-radius: 12px; background: var(--surface); }
</style>
"""


def load_notices() -> pd.DataFrame:
    df = pd.DataFrame(db.all_notices(), columns=DB_COLS)
    for col in DB_COLS:
        if col != "score":
            df[col] = df[col].fillna("").astype(str)
    df["score"] = pd.to_numeric(df["score"], errors="coerce").fillna(0).astype(int)
    today = date.today()

    def days_left(value):
        try:
            return (date.fromisoformat(value) - today).days
        except (ValueError, TypeError):
            return None

    df["closes_in"] = pd.array([days_left(v) for v in df["deadline"]], dtype="Int64")
    return df.set_index("notice_id", drop=False)


@st.cache_data(show_spinner=False)
def xlsx_bytes(frame: pd.DataFrame) -> bytes:
    return exports.to_xlsx(frame)


def pretty(ts: str) -> str:
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").strftime("%d %b, %H:%M")
    except (TypeError, ValueError):
        return "never"


def stat(label, value, note="", alert=False):
    return (f'<div class="stat{" is-alert" if alert else ""}">'
            f'<span class="stat-label">{html.escape(str(label))}</span>'
            f'<span class="stat-value">{html.escape(str(value))}</span>'
            f'<span class="stat-note">{html.escape(str(note))}</span></div>')


def run_refresh():
    with st.status("Checking every source. This takes a few minutes.", expanded=True) as box:
        def show(r):
            mark = {"ok": "✓", "partial": "!"}.get(r["status"], "×")
            box.write(f"{mark}  **{r['source']}** — {r['new']} new, {r['matched']} matching ({r['seconds']:.0f}s)")

        summary = run_cycle(trigger="manual", progress=show)
        box.update(label="Check finished", state="complete", expanded=False)
    st.session_state["flash"] = ("A check was already running; its results are in."
                                 if summary["skipped"] else f"{summary['new']} new notice(s) found.")
    st.rerun()


# --------------------------------------------------------------------- page

st.markdown(CSS, unsafe_allow_html=True)
df = load_notices()
runs = pd.DataFrame(db.recent_runs(40))
today = date.today().isoformat()
now = datetime.now()
last_run = db.last_run_time()

head, action = st.columns([0.74, 0.26], vertical_alignment="center")
with head:
    st.markdown('<div class="app-head"><span class="wordmark">RFP <em>Monitor</em></span>'
                '<span class="tagline">Disaster risk, resilience and urban planning opportunities · '
                f'{len(ALL_SOURCES) - len(config.DISABLED_SOURCES)} sources</span></div>', unsafe_allow_html=True)
with action:
    if st.button("Refresh now", type="primary", width="stretch"):
        run_refresh()
    st.caption(f"Last checked {pretty(last_run)}" if last_run else "Not checked yet")
st.markdown('<div class="rule"></div>', unsafe_allow_html=True)

if "flash" in st.session_state:
    st.toast(st.session_state.pop("flash"))

expired = df["deadline"].str.match(ISO_DATE, na=False) & (df["deadline"] < today)
closing = df["closes_in"].notna() & (df["closes_in"] >= 0) & (df["closes_in"] <= 7)
fresh = df["date_found"] >= (last_run or "9999")
last_cycle = runs[runs["run_time"] == runs["run_time"].max()] if not runs.empty else runs
failed = last_cycle[last_cycle["status"] != "ok"] if not last_cycle.empty else last_cycle

st.markdown('<div class="stat-row">'
            + stat("Open now", int((~expired).sum()), f"{int(expired.sum())} closed")
            + stat("From last check", int(fresh.sum()), "newly found")
            + stat("Closing in 7 days", int(closing.sum()), "act on these first", alert=bool(closing.sum()))
            + stat("Sources", f"{len(last_cycle) - len(failed)}/{len(last_cycle)}" if len(last_cycle) else "—",
                   "all reporting" if len(last_cycle) and not len(failed) else f"{len(failed)} with problems",
                   alert=bool(len(failed)))
            + '</div>', unsafe_allow_html=True)

if len(failed):
    st.markdown('<div class="chips">' + "".join(
        f'<span class="chip bad">{html.escape(str(r.source))} · {html.escape(str(r.status))}</span>'
        for r in failed.itertuples()) + '</div>', unsafe_allow_html=True)

with st.sidebar:
    st.markdown("#### Search")
    query = st.text_input("Search", placeholder="early warning, Pakistan…", label_visibility="collapsed")
    st.markdown("#### Relevance")
    min_score = st.slider("Minimum score", 0, 100, 0, 5, label_visibility="collapsed",
                          help="Keyword-weighted score; the exact formula is at the top of matching.py.")
    st.markdown("#### Timing")
    open_only = st.toggle("Hide closed", value=True)
    soon_only = st.toggle("Closing within 30 days", value=False)
    window = st.selectbox("Found", ["Any time", "Last 24 hours", "Last 7 days", "Last 30 days"],
                          label_visibility="collapsed")
    st.markdown("#### Filter")
    hide_dismissed = st.toggle("Hide 'not relevant'", value=True)
    pick_sources = st.multiselect("Source", sorted(s for s in df["source"].unique() if s))
    pick_countries = st.multiselect("Country", sorted(c for c in df["country"].unique() if c))

view = df
if open_only:
    view = view[~expired.reindex(view.index, fill_value=False)]
if soon_only:
    view = view[view["closes_in"].notna() & (view["closes_in"] >= 0) & (view["closes_in"] <= 30)]
if hide_dismissed:
    view = view[view["triage"] != "not relevant"]
if min_score:
    view = view[view["score"] >= min_score]
if window != "Any time":
    days = {"Last 24 hours": 1, "Last 7 days": 7, "Last 30 days": 30}[window]
    view = view[view["date_found"] >= (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")]
if pick_sources:
    view = view[view["source"].isin(pick_sources)]
if pick_countries:
    view = view[view["country"].isin(pick_countries)]
if query.strip():
    q = query.strip()
    view = view[view["title"].str.contains(q, case=False, regex=False)
                | view["detail"].str.contains(q, case=False, regex=False)
                | view["agency"].str.contains(q, case=False, regex=False)]

if df.empty:
    st.markdown('<div class="empty"><h3>Nothing collected yet</h3>'
                '<p>Press <strong>Refresh now</strong> to check every source. The first pass takes a few '
                'minutes and stores whatever matches the keywords in config.py.</p>'
                '<p>Checks only ever run when you ask for one.</p></div>', unsafe_allow_html=True)
elif view.empty:
    st.markdown('<div class="empty"><h3>No notices match these filters</h3>'
                f'<p>{len(df)} notices are stored. Widen the relevance score or clear a filter '
                'in the sidebar.</p></div>', unsafe_allow_html=True)
else:
    grid = view[GRID_COLS].copy()
    grid["date_found"] = grid["date_found"].str[:10]
    filters = repr((query, min_score, window, open_only, soon_only, hide_dismissed, pick_sources, pick_countries))
    key = f"grid-{hashlib.md5(filters.encode()).hexdigest()[:8]}-{st.session_state.setdefault('grid_version', 0)}"
    st.caption(f"{len(view)} of {len(df)} notices · newest first · set **Triage** to shortlist or dismiss")
    edited = st.data_editor(
        grid, key=key, hide_index=True, width="stretch", height=600, disabled=GRID_COLS[1:], placeholder="",
        column_config={
            "triage": st.column_config.SelectboxColumn("Triage", options=db.TRIAGE_OPTIONS, width="small"),
            "score": st.column_config.ProgressColumn("Relevance", min_value=0, max_value=100, format="%d",
                                                     width="small"),
            "closes_in": st.column_config.NumberColumn("Closes in", format="%d d", width="small",
                                                       help="Days until the deadline"),
            "deadline": st.column_config.TextColumn("Deadline", width="small"),
            "date_found": st.column_config.TextColumn("Found", width="small"),
            "source": st.column_config.TextColumn("Source", width="small"),
            "agency": st.column_config.TextColumn("Agency"),
            "title": st.column_config.TextColumn("Title", width="large"),
            "country": st.column_config.TextColumn("Country"),
            "type": st.column_config.TextColumn("Type"),
            "matched": st.column_config.TextColumn("Matched keywords"),
            "url": st.column_config.LinkColumn("Link", display_text="Open"),
        })
    changed = edited.index[edited["triage"].fillna("") != grid["triage"]]
    if len(changed):
        for notice_id in changed:
            db.set_triage(notice_id, edited.at[notice_id, "triage"] or "")
        st.session_state["grid_version"] += 1
        st.rerun()

    export = exports.spreadsheet_safe(view[DB_COLS].reset_index(drop=True))
    stamp = now.strftime("%Y%m%d_%H%M")
    e1, e2, _ = st.columns([1, 1, 5])
    e1.download_button("Excel", xlsx_bytes(export), f"rfp_notices_{stamp}.xlsx", width="stretch",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    e2.download_button("CSV", exports.to_csv(export), f"rfp_notices_{stamp}.csv", mime="text/csv",
                       width="stretch")

with st.expander("Run log — every source, every check", expanded=False):
    if runs.empty:
        st.write("No checks yet.")
    else:
        st.dataframe(runs.head(20), hide_index=True, width="stretch", column_config={
            "run_time": "Time", "source": "Source", "trigger": "Trigger", "fetched": "Fetched",
            "matched": "Matched", "new_count": "New", "status": "Status", "error": "Error",
            "seconds": st.column_config.NumberColumn("Secs", format="%.0f")})

st.caption(f"Database {config.DB_PATH.name} · keywords and sources in config.py · checks run only on Refresh")

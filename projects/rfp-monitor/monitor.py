"""
monitor.py -- the one ingestion path.

run_cycle() is what the background scheduler, the dashboard's "Run now"
button and the command line all call. There is no second copy of this loop,
so every trigger filters, dedups and logs identically.

    python monitor.py      # run one full check now and print a summary
"""
import html
import logging
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from logging.handlers import RotatingFileHandler

import config
import db
from matching import is_iso_date, norm_date, score_notice
from sources import ALL_SOURCES, PartialFetch

log = logging.getLogger("rfp_monitor")
_RUN_LOCK = threading.Lock()  # a click during a scheduled check skips instead of doubling up

FIELDS = ("source", "type", "title", "detail", "country", "deadline", "ref", "url", "agency", "posted")
_LIMITS = {"title": 500, "detail": 2000, "url": 1000, "ref": 500}
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # also illegal in .xlsx cells


def label_of(fetcher) -> str:
    return getattr(fetcher, "label", fetcher.__name__)


def _clean(value, limit=300) -> str:
    s = _CONTROL.sub(" ", html.unescape(str(value if value is not None else "")))
    return re.sub(r"\s+", " ", s).strip()[:limit]


def prepare(rows, today=None) -> list[dict]:
    """Clean raw fetcher rows, compute notice_id, apply the keyword and
    expired-deadline rules and score. No I/O, so it is directly testable."""
    today = today or date.today().isoformat()
    kept, seen = [], set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        r = {k: _clean(raw.get(k), _LIMITS.get(k, 300)) for k in FIELDS}
        r["notice_id"] = f"{r['source']}::{r['ref'] or r['url'] or r['title']}"
        if r["notice_id"] in seen or not r["title"]:
            continue
        seen.add(r["notice_id"])
        if not r["url"].lower().startswith(("http://", "https://")):
            r["url"] = ""  # scraped values are untrusted: only real web links survive
        r["deadline"] = norm_date(r["deadline"]) or r["deadline"][:60]
        r["posted"] = norm_date(r["posted"])
        if config.SKIP_EXPIRED and is_iso_date(r["deadline"]) and r["deadline"] < today:
            continue
        r["score"], matched = score_notice(r["title"], r["detail"], r["type"])
        if not matched:
            continue  # keyword gate: at least one KEYWORDS term in title or detail
        r["matched"] = ", ".join(matched)
        kept.append(r)
    return kept


def _fetch(fetcher):
    """Run one fetcher -> (rows, status, error, seconds). Never raises."""
    t0 = time.monotonic()
    try:
        rows = fetcher()
        if not isinstance(rows, list):
            raise TypeError(f"fetcher returned {type(rows).__name__}, expected list")
        status, error = "ok", None
    except PartialFetch as e:
        rows, status, error = list(e.rows), "partial", str(e)
        log.warning("%s partial: %s", label_of(fetcher), e)
    except Exception as e:
        rows, status, error = [], "error", f"{type(e).__name__}: {e}"
        log.exception("%s failed", label_of(fetcher))
    return rows, status, error[:1000] if error else None, round(time.monotonic() - t0, 1)


def run_cycle(trigger="manual", sources=None, progress=None) -> dict:
    """Check every enabled source once and store the new matching notices.

    Writes one run_log row per source per run -- failures and zero-new
    successes included. Returns {"skipped", "run_time", "new", "results"}.
    """
    _setup_logging()
    if not _RUN_LOCK.acquire(blocking=False):
        log.info("%s check skipped: another check is still running", trigger)
        return {"skipped": True, "run_time": None, "new": 0, "results": []}
    try:
        db.init_db()
        fetchers = [f for f in (sources or ALL_SOURCES) if f.__name__ not in config.DISABLED_SOURCES]
        run_time = db.now_str()
        log.info("%s check started: %d sources", trigger, len(fetchers))
        results, new_rows = [], []
        with ThreadPoolExecutor(max_workers=max(1, min(8, len(fetchers)))) as pool:
            futures = {pool.submit(_fetch, f): f for f in fetchers}
            for fut in as_completed(futures):
                label = label_of(futures[fut])
                rows, status, error, seconds = fut.result()
                try:
                    kept = prepare(rows)
                    new = db.insert_notices(kept)
                except Exception as e:  # a storage problem for one source still gets logged
                    log.exception("%s: storing results failed", label)
                    kept, new, status, error = [], [], "error", f"storing results failed: {type(e).__name__}: {e}"
                unique = len({r.get("ref") or r.get("url") for r in rows if isinstance(r, dict)})
                db.log_run(run_time, label, len(new), status, error, trigger, unique, len(kept), seconds)
                new_rows += new
                res = {"source": label, "status": status, "fetched": unique, "matched": len(kept),
                       "new": len(new), "error": error, "seconds": seconds}
                results.append(res)
                log.info("%-36s %-7s fetched=%d matched=%d new=%d %.1fs %s",
                         label, status, unique, len(kept), len(new), seconds, error or "")
                if progress:
                    try:
                        progress(res)
                    except Exception:
                        log.warning("progress callback failed", exc_info=True)
        log.info("%s check finished: %d new", trigger, len(new_rows))
        _notify(new_rows)
        return {"skipped": False, "run_time": run_time, "new": len(new_rows), "results": results}
    finally:
        _RUN_LOCK.release()


def _setup_logging():
    if log.handlers:
        return
    config.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(config.LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


# Windows toast via the built-in WinRT API. Scraped titles reach PowerShell
# only through environment variables, never through the command text.
_TOAST_PS = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$t = $xml.GetElementsByTagName('text')
$null = $t.Item(0).AppendChild($xml.CreateTextNode($env:RFP_TOAST_TITLE))
$null = $t.Item(1).AppendChild($xml.CreateTextNode($env:RFP_TOAST_BODY))
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"""


def _notify(new_rows):
    if config.NOTIFY_MIN_SCORE is None or sys.platform != "win32":
        return
    hits = sorted((r for r in new_rows if r.get("score", 0) >= config.NOTIFY_MIN_SCORE), key=lambda r: -r["score"])
    if not hits:
        return
    title = f"{len(hits)} new RFP{'' if len(hits) == 1 else 's'} in your field"
    body = " | ".join(f"{r['source']}: {r['title'][:70]}" for r in hits[:3])
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST_PS],
                       env={**os.environ, "RFP_TOAST_TITLE": title, "RFP_TOAST_BODY": body},
                       capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        log.warning("desktop notification failed", exc_info=True)


def _print_progress(res):
    print(f"  {res['source']:<36} {res['status']:<7} fetched {res['fetched']:>5}  "
          f"matched {res['matched']:>4}  new {res['new']:>4}  {res['seconds']:>6.1f}s")
    if res["error"]:
        print(f"      ! {res['error'][:300]}")


if __name__ == "__main__":
    if sys.stdout:  # None under pythonw (Task Scheduler)
        sys.stdout.reconfigure(errors="replace", line_buffering=True)
    print(f"Checking {len(ALL_SOURCES) - len(config.DISABLED_SOURCES)} sources ...")
    summary = run_cycle(trigger="cli", progress=_print_progress)
    print("Skipped: another check is already running." if summary["skipped"]
          else f"Done: {summary['new']} new notice(s). Database: {config.DB_PATH}")

"""
browser_fetch.py -- a real browser for the sources that block plain HTTP.

Some sites cannot be read with requests: AfDB sits behind a Cloudflare
challenge, IUCN added Drupal's antibot, IFRC answers 403, and the Green
Climate Fund's tender list is an Oracle page that only exists after
JavaScript runs. This drives the Edge or Chrome already installed on the
machine through Playwright -- no browser download, no API key, no LLM.

Only the fetchers that need it import this; everything else stays on
requests, which is far faster.
"""
from contextlib import contextmanager

# Headless Edge advertises itself as headless, which the bot checks on these
# very sites reject, so the context claims an ordinary desktop Chrome.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _launch(p):
    last = None
    for opts in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
        try:
            return p.chromium.launch(headless=True, **opts)
        except Exception as e:  # noqa: PERF203 - try the next browser
            last = e
    raise RuntimeError(f"no browser available for Playwright ({last}); "
                       "install Microsoft Edge or run: playwright install chromium")


class _Browser:
    """Thin helper around one browser context."""

    def __init__(self, ctx):
        self._ctx = ctx

    @contextmanager
    def _page(self, url, wait_ms, wait_for, wait_until):
        page = self._ctx.new_page()
        try:
            page.goto(url, wait_until=wait_until)
            if wait_for:
                page.wait_for_selector(wait_for)
            if wait_ms:
                page.wait_for_timeout(wait_ms)
            yield page
        finally:
            page.close()

    def html(self, url, wait_ms=4000, wait_for=None, wait_until="domcontentloaded") -> str:
        with self._page(url, wait_ms, wait_for, wait_until) as page:
            return page.content()

    def rows(self, url, js, wait_ms=4000, wait_for=None, wait_until="domcontentloaded"):
        """Evaluate a JS function in the page and return its (JSON-able) result."""
        with self._page(url, wait_ms, wait_for, wait_until) as page:
            return page.evaluate(js)

    def post_json(self, page_url, path, payload, wait_ms=3000):
        """POST from inside the site's own origin, so cookies and bot checks pass."""
        with self._page(page_url, wait_ms, None, "domcontentloaded") as page:
            return page.evaluate(
                """async ([path, body]) => {
                    const r = await fetch(path, {method: 'POST', credentials: 'include',
                        headers: {'Content-Type': 'application/json; charset=UTF-8',
                                  'X-Requested-With': 'XMLHttpRequest'}, body});
                    return {status: r.status, text: await r.text()};
                }""", [path, payload])


@contextmanager
def browser_session(timeout_ms=90_000):
    """Yield a _Browser backed by the installed Edge/Chrome, closed on exit."""
    from playwright.sync_api import sync_playwright  # optional dependency, imported lazily

    with sync_playwright() as p:
        engine = _launch(p)
        try:
            ctx = engine.new_context(locale="en-US", user_agent=UA,
                                     viewport={"width": 1600, "height": 1200})  # tall: grids render only visible rows
            ctx.set_default_timeout(timeout_ms)
            yield _Browser(ctx)
        finally:
            engine.close()

"""Link-chain resolver: aggregator job page -> employer / official-ATS apply URL, via a TinyFish cloud browser.

DECISIONS D12/D13: this is the ONLY place a paid browser is used, and ONLY to follow the apply button's redirect chain.
No descriptions, dates or parsing happen here. Sessions are metered ($0.002/min) against a hard lifetime cap that
defaults to $0; the maximum billable time per job is reserved in the database BEFORE the session starts.
Logs: fixed outcome codes and counts only (public repo).
"""
import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.request

from pipeline import budget, classify

API = "https://api.browser.tinyfish.ai"
APPLY_LABEL = re.compile(r"\bapply\b", re.I)
EASY_APPLY = re.compile(r"easy\s*apply", re.I)
MAX_LABEL_LEN = 40


class ChainError(RuntimeError):
    """Fixed codes only."""


def clean_label(text):
    return " ".join((text or "").split())


def is_apply_control(label):
    """A short control label that looks like an apply button ('Apply', 'Apply now', 'Apply on company site')."""
    label = clean_label(label)
    return 0 < len(label) <= MAX_LABEL_LEN and bool(APPLY_LABEL.search(label))


def decide(label, final_url, job_url):
    """Outcome for a clicked control. Returns (kind, url). Easy Apply is final on the aggregator itself (D3 rule 3)."""
    if EASY_APPLY.search(label or ""):
        return "easy_apply", job_url
    kind = classify.apply_kind(final_url)
    return ("aggregator" if kind == "aggregator" else kind), final_url


def _call(method, url, key, body=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"X-API-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raise ChainError(f"BROWSER_HTTP_{error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise ChainError("BROWSER_NETWORK") from None


def create_session(key, url, inactivity_seconds=60):
    data = _call("POST", API, key, {"url": url, "timeout_seconds": inactivity_seconds})
    if not data.get("cdp_url") or not data.get("session_id"):
        raise ChainError("BROWSER_BAD_SESSION")
    return data["session_id"], data["cdp_url"]


def delete_session(key, session_id):
    for _ in range(3):                                  # termination must be confirmed; retry a couple of times
        try:
            _call("DELETE", f"{API}/{session_id}", key, timeout=30)
            return True
        except ChainError:
            time.sleep(2)
    return False


async def _follow(cdp_url, job_url, settle_ms=2500, click_wait_ms=15000):
    """Click the apply control and report where the chain ends. Uses Playwright over the remote browser (lazy import)."""
    from playwright.async_api import async_playwright        # noqa: PLC0415 - only needed when a paid session runs
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(cdp_url)
        try:
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = next((p for p in context.pages if p.url not in ("", "about:blank")), None) or context.pages[0]
            await page.wait_for_timeout(settle_ms)
            controls = await page.query_selector_all("a, button, [role=button]")
            target, label = None, ""
            for element in controls:
                text = clean_label(await element.inner_text())
                if is_apply_control(text):
                    target, label = element, text
                    break
            if target is None:
                return {"outcome": "no_apply_control"}
            if EASY_APPLY.search(label):
                return {"outcome": "easy_apply", "label": label, "url": job_url}
            async with context.expect_page(timeout=click_wait_ms) as popup:
                await target.click()
            landed = await popup.value
            await landed.wait_for_load_state("domcontentloaded", timeout=click_wait_ms)
            await landed.wait_for_timeout(1500)
            return {"outcome": "landed", "label": label, "url": landed.url}
        finally:
            await browser.close()


def resolve(connection, job_url, environ=os.environ):
    """One job. Returns {'outcome': code, 'kind': ..., 'url': ...}; never raises for site-level failures."""
    cap = budget.browser_cap_usd(environ)
    if cap <= 0:
        return {"outcome": "paid_browser_disabled"}             # default: spend nothing
    key = (environ.get("TINYFISH_API_KEY") or "").strip()
    if not key:
        return {"outcome": "key_missing"}
    if not budget.reserve_browser(connection, cap):
        return {"outcome": "budget_exhausted"}
    started, session_id = time.monotonic(), None
    try:
        session_id, cdp_url = create_session(key, job_url)
        result = asyncio.run(_follow(cdp_url, job_url))
    except ChainError as error:
        result = {"outcome": str(error)}
    except Exception:                                           # noqa: BLE001 - site/driver failure; counted, never logged
        result = {"outcome": "chain_failed"}
    finally:
        if session_id:
            delete_session(key, session_id)
        used = int(time.monotonic() - started) + 1
        budget.refund_browser(connection, budget.SESSION_MAX_SECONDS - used)
    if result.get("outcome") in ("landed", "easy_apply"):
        kind, url = decide(result.get("label", ""), result.get("url", ""), job_url)
        result.update({"kind": kind, "url": url})
    return result

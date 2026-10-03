"""Jobright final apply link: log in with the saved secrets, click Apply, read where the chain lands (D12/D14).

Free: Chromium runs inside the GitHub Actions runner (no paid browser). Selectors are the ones proven in V2.
Only the link chain is done here - no descriptions, dates or parsing. Logs: fixed outcome codes and counts only.
"""
import asyncio
import os
import re
from urllib.parse import urlsplit

from lifeos.jobs import classify

HOME = "https://jobright.ai/"
SIGN_IN = re.compile(r"^\s*(sign\s*in|log\s*in)\s*$", re.I)
APPLY_XPATH = ("xpath=//button[contains(translate(normalize-space(.), 'APPLY', 'apply'), 'apply') "
               "or contains(@class, 'apply-button')]")
LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]


def is_internal(url):
    host = (urlsplit(url or "").hostname or "").lower().removeprefix("www.")
    return host == "jobright.ai" or host.endswith(".jobright.ai")


def outcome_for(url, used_popup):
    """('employer'|'ats'|'aggregator', url) for a landing URL, or ('internal', None) when it never left Jobright."""
    if not url or is_internal(url):
        return "internal", None
    return classify.apply_kind(url), url


async def login(page, email, password):
    await page.goto(HOME, wait_until="domcontentloaded")
    for label in ("Accept", "Allow", "Got it"):
        button = page.get_by_role("button", name=label)
        if await button.count():
            try:
                await button.first.click(timeout=2000)
            except Exception:                                   # noqa: BLE001 - consent banner is optional
                pass
            break
    stage, seen = "start", []
    for attempt in range(3):
        try:
            stage = "sign_in_click"
            await page.get_by_text(SIGN_IN, exact=False).locator("visible=true").first.click(timeout=20000)
            stage = "form_fill"
            await page.locator("#basic_email").fill(email, timeout=15000)
            await page.locator("#basic_password").fill(password, timeout=15000)
            stage = "submit"
            await page.locator("#basic_password").press("Enter")
            stage = "profile_wait"
            await page.locator("xpath=//span[text()='Profile']").first.wait_for(timeout=25000)
            return "ok"
        except Exception:                                       # noqa: BLE001
            seen.append(stage)
            if attempt < 2:
                await page.goto(HOME, wait_until="domcontentloaded")
    try:                                                        # value-free page facts for the public log
        html = (await page.content()).lower()
        texts = await page.evaluate("""() => [...document.querySelectorAll('a,button,span,div')]
            .filter(e => e.children.length === 0 && /sign|log ?in|join|continue/i.test(e.textContent || ''))
            .map(e => e.tagName + ':' + e.textContent.trim().slice(0, 24)).slice(0, 8)""")
        seen.append(page.url.split("?")[0].split("//")[-1].split("/")[0] + (await page.title())[:30] + "|" + ";".join(texts))
        marks = [m for m in ("captcha", "turnstile", "recaptcha", "incorrect", "invalid", "verify") if m in html]
    except Exception:                                           # noqa: BLE001
        marks = []
    return "login_failed:" + "/".join(seen) + ":" + ",".join(marks)


DATA_KEYS = ("originalUrl", "applyLink", "companyApplyUrl", "externalApplyUrl", "jobApplyLink")
DATA_JS = """(keys) => { const out = [];
  const walk = (o, d) => { if (!o || d > 12) return;
    if (Array.isArray(o)) { o.forEach(x => walk(x, d + 1)); return; }
    if (typeof o === 'object') for (const [k, v] of Object.entries(o)) {
      if (keys.includes(k) && typeof v === 'string' && v.startsWith('http')) out.push(v); else walk(v, d + 1); } };
  const el = document.getElementById('__NEXT_DATA__');
  if (el) { try { walk(JSON.parse(el.textContent), 0); } catch (e) {} }
  return out; }"""


STATE_JS = """() => { const out = new Set();
  const want = /expir|clos|status|active|publish|poste?d|live|valid|open|stale|remov/i;
  const work = /work.?(model|type|place|mode|arrangement)|remote|on.?site|hybrid|workplace/i;
  const walk = (o, d) => { if (!o || d > 12 || out.size > 60) return;
    if (Array.isArray(o)) { o.slice(0, 3).forEach(x => walk(x, d + 1)); return; }
    if (typeof o === 'object') for (const [k, v] of Object.entries(o)) {
      if ((want.test(k) || work.test(k)) && typeof v !== 'object') {
        const shown = typeof v === 'boolean' || (work.test(k) && typeof v === 'string' && /^[A-Za-z _-]{2,20}$/.test(v));
        out.add(shown ? k + '=' + v : k + ':' + typeof v); } else walk(v, d + 1); } };
  const el = document.getElementById('__NEXT_DATA__');
  if (el) { try { walk(JSON.parse(el.textContent), 0); } catch (e) {} }
  return Array.from(out); }"""


async def page_state_keys(page):
    """Diagnostic only: NAMES of the job page's own liveness-like and work-model-like fields (a boolean shows its value, a work-model field its short word such as Remote / Hybrid;
    everything else shows only its type). No titles, text or urls.
    Tells us whether Jobright says if a job is still open, so its description could stand in for an unreadable employer page."""
    try:
        return [str(k)[:40] for k in (await page.evaluate(STATE_JS))][:40]
    except Exception:                                           # noqa: BLE001
        return []


def strip_tracking(url):
    """Drop Jobright's own tracking parameter (jr_id) from an employer URL."""
    parts = urlsplit(url)
    query = [q for q in parts.query.split("&") if q and not q.startswith("jr_id=")]
    return parts._replace(query="&".join(query)).geturl()


async def original_post_link(page):
    """href of the 'Original Job Post' anchor (no click), or None."""
    try:
        anchor = page.locator("a", has_text=re.compile(r"original\s+job\s+post", re.I)).first
        await anchor.wait_for(timeout=8000)
        href = await anchor.get_attribute("href")
        return href if href and href.startswith("http") and not is_internal(href) else None
    except Exception:                                           # noqa: BLE001
        return None


async def page_data_link(page):
    """Employer URL carried in the logged-in job page's own data (no click), or None."""
    try:
        for url in await page.evaluate(DATA_JS, list(DATA_KEYS)):
            if not is_internal(url):
                return url
    except Exception:                                           # noqa: BLE001
        pass
    return None


async def follow(context, page, job_url, wait_ms=12000):
    await page.goto(job_url, wait_until="domcontentloaded")
    found = await original_post_link(page)
    via = "original_post"
    if not found:
        found, via = await page_data_link(page), "page_data"
    if found:
        kind, final = outcome_for(strip_tracking(found), False)
        return {"outcome": "landed", "via": via, "kind": kind, "url": final, "page_keys": await page_state_keys(page)}
    try:
        await page.locator(APPLY_XPATH).first.wait_for(timeout=wait_ms)
    except Exception:                                           # noqa: BLE001
        return {"outcome": "apply_unavailable"}
    before = set(context.pages)
    label = ""
    try:
        label = " ".join((await page.locator(APPLY_XPATH).first.inner_text()).split())[:24]
    except Exception:                                           # noqa: BLE001
        pass
    try:
        await page.locator(APPLY_XPATH).first.click(timeout=wait_ms)
    except Exception:                                           # noqa: BLE001
        return {"outcome": "apply_click_error"}
    deadline = asyncio.get_event_loop().time() + wait_ms / 1000
    while asyncio.get_event_loop().time() < deadline:
        for other in set(context.pages) - before:
            await other.wait_for_load_state("domcontentloaded", timeout=wait_ms)
            url = other.url
            await other.close()
            kind, final = outcome_for(url, True)
            return {"outcome": "landed" if final else "internal_target", "kind": kind, "url": final}
        if not is_internal(page.url):
            kind, final = outcome_for(page.url, False)
            return {"outcome": "landed", "kind": kind, "url": final}
        await page.wait_for_timeout(250)
    try:
        dialog = await page.locator("[role=dialog], .ant-modal, .ant-drawer").count()
        page_text = (await page.inner_text("body")).lower()
        marks = [m for m in ("no longer", "expired", "closed", "unavailable", "upgrade", "limit", "verify") if m in page_text]
        labels = await page.evaluate("""() => [...document.querySelectorAll('[role=dialog] button, .ant-modal button, .ant-modal a, [role=dialog] a')]
            .map(e => (e.textContent || '').trim().slice(0, 22)).filter(Boolean).slice(0, 6)""")
        marks += ["btn=" + "|".join(labels)]
    except Exception:                                           # noqa: BLE001
        dialog, marks = -1, []
    return {"outcome": f"target_timeout:{label}:dialog={dialog}:pages={len(context.pages)}:{','.join(marks)}"}


async def resolve_many(urls, environ=os.environ, pause_ms=1500):
    """One login, many jobs. Returns a list of result dicts aligned with `urls` (fixed codes only)."""
    email, password = environ.get("JOBRIGHT_EMAIL", ""), environ.get("JOBRIGHT_PASSWORD", "")
    if not email or not password:
        return [{"outcome": "auth_unavailable"} for _ in urls]
    from playwright.async_api import async_playwright        # noqa: PLC0415 - only when the browser path runs
    results = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=LAUNCH_ARGS)
        try:
            context = await browser.new_context(viewport={"width": 1920, "height": 1080})
            page = await context.new_page()
            state = await login(page, email, password)
            if state != "ok":
                return [{"outcome": state} for _ in urls]
            for url in urls:
                try:
                    results.append(await follow(context, page, url))
                except Exception:                               # noqa: BLE001 - site failure; counted, never logged
                    results.append({"outcome": "chain_failed"})
                await page.wait_for_timeout(pause_ms)
        finally:
            await browser.close()
    return results


def resolve_many_sync(urls, environ=os.environ):
    return asyncio.run(resolve_many(urls, environ))

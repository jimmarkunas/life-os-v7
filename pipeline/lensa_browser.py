"""Lensa final apply link via free local Chromium (D12: link chain only). Fixed outcome codes, counts only in logs."""
import asyncio
import re
from urllib.parse import urlsplit

from pipeline import classify
from pipeline.jobright_browser import LAUNCH_ARGS

APPLY = re.compile(r"\bapply\b|company site|view job|continue", re.I)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")


def is_lensa(url):
    host = (urlsplit(url or "").hostname or "").lower()
    return host == "lensa.com" or host.endswith(".lensa.com")


def outcome_for(url):
    if not url or is_lensa(url):
        return "internal", None
    return classify.apply_kind(url), url


async def follow(context, job_url, wait_ms=15000):
    page = await context.new_page()
    hops = []
    page.on("framenavigated", lambda f: hops.append(f.url) if f == page.main_frame else None)
    try:
        response = await page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
        status = response.status if response else 0
    except Exception:                                           # noqa: BLE001
        return {"outcome": "goto_failed"}
    await page.wait_for_timeout(3000)
    if not is_lensa(page.url):                                  # redirect chain already left Lensa
        kind, url = outcome_for(page.url)
        return {"outcome": "landed", "via": "redirect", "kind": kind, "url": url, "status": status}
    try:
        labels = await page.evaluate("""() => [...document.querySelectorAll('a,button')]
            .map(e => (e.textContent || '').trim().replace(/\\s+/g, ' ')).filter(t => t && t.length < 40)""")
    except Exception:                                           # noqa: BLE001
        labels = []
    candidates = [t for t in labels if APPLY.search(t)]
    before = set(context.pages)
    if candidates:
        try:
            await page.get_by_text(candidates[0], exact=True).first.click(timeout=8000)
        except Exception:                                       # noqa: BLE001
            return {"outcome": f"click_failed:{candidates[0][:20]}", "status": status}
        for _ in range(int(wait_ms / 250)):
            for other in set(context.pages) - before:
                await other.wait_for_load_state("domcontentloaded", timeout=wait_ms)
                kind, url = outcome_for(other.url)
                return {"outcome": "landed", "via": "popup", "kind": kind, "url": url, "status": status}
            if not is_lensa(page.url):
                kind, url = outcome_for(page.url)
                return {"outcome": "landed", "via": "navigate", "kind": kind, "url": url, "status": status}
            await page.wait_for_timeout(250)
    try:
        text = (await page.inner_text("body")).lower()
    except Exception:                                           # noqa: BLE001
        text = ""
    marks = [m for m in ("sign in", "log in", "register", "captcha", "verify", "access denied", "expired", "not found") if m in text]
    return {"outcome": f"stuck:st={status}:hops={len(hops)}:cand={len(candidates)}:{','.join(marks)}"
                       f":btn={'|'.join(candidates[:3])[:60]}"}


async def resolve_many(urls, pause_ms=1500):
    from playwright.async_api import async_playwright        # noqa: PLC0415
    results = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=LAUNCH_ARGS)
        try:
            context = await browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 800})
            for url in urls:
                try:
                    results.append(await follow(context, url))
                except Exception:                               # noqa: BLE001
                    results.append({"outcome": "chain_failed"})
                for extra in context.pages[1:]:
                    await extra.close()
                await asyncio.sleep(pause_ms / 1000)
        finally:
            await browser.close()
    return results


def resolve_many_sync(urls):
    return asyncio.run(resolve_many(urls))

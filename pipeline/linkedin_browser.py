"""LinkedIn external-apply chain via free local Chromium, NO login (D14; ported from V2 linkedin_browser.py).
Scan anchors for an external URL, else click the Apply control and follow. Fixed outcome codes only."""
import asyncio
import re

from pipeline import classify, li_apply
from pipeline.jobright_browser import LAUNCH_ARGS
from pipeline.lensa_browser import UA

APPLY_XPATH = "xpath=//*[self::a or self::button][contains(translate(normalize-space(.), 'APPLY', 'apply'), 'apply')]"


async def follow(context, job_url, wait_ms=9000):
    page = await context.new_page()
    try:
        response = await page.goto(li_apply.GUEST_API + (li_apply.job_id(job_url) or ""), wait_until="domcontentloaded",
                                   timeout=25000)
        status = response.status if response else 0
    except Exception:                                           # noqa: BLE001
        return {"outcome": "goto_failed"}
    if status != 200:
        return {"outcome": f"http_{status}"}
    kind, target = li_apply.read(await page.content())
    if kind == "external" and target:
        return {"outcome": "landed", "via": "page", "kind": classify.apply_kind(target), "url": target}
    if kind == "easy_apply":
        return {"outcome": "easy_apply", "kind": "easy_apply", "url": job_url}
    if kind == "closed":
        return {"outcome": "closed"}
    before = set(context.pages)
    try:
        await page.locator(APPLY_XPATH).first.click(timeout=6000)
    except Exception:                                           # noqa: BLE001
        return {"outcome": f"no_apply_control:{kind}"}
    for _ in range(int(wait_ms / 250)):
        for other in set(context.pages) - before:
            try:
                await other.wait_for_load_state("domcontentloaded", timeout=wait_ms)
            except Exception:                                   # noqa: BLE001
                pass
            return _landing(other.url)
        if "linkedin.com" not in page.url:
            return _landing(page.url)
        await page.wait_for_timeout(250)
    return {"outcome": "target_timeout:" + _wall(page.url)}


def _wall(url):
    for token in ("signup", "authwall", "login", "checkpoint", "uas"):
        if token in url:
            return token
    return "other"


def _landing(url):
    if "linkedin.com" in url:
        return {"outcome": "linkedin_wall:" + _wall(url)}
    return {"outcome": "landed", "via": "click", "kind": classify.apply_kind(url), "url": url}


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

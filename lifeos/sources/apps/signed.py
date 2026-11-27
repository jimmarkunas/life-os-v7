"""What every signed Close page shares: signing, result page, refusal wrapper, POST guard and one verified write.

A leaf module: it imports no app. Fixed codes only.
"""
import hashlib
import hmac
import sys
from html import escape as e

from lifeos.hiring import close_screen as S, decision as D
from lifeos.platform.notion_client import NotionError

HTML = "text/html; charset=utf-8"
STEPS = ("cancel", "edit", "review", "confirm")


def norm(value):
    return str(value).replace("\r\n", "\n").replace("\r", "\n")


def mac(secret, *parts):
    return hmac.new(secret, "\x1f".join(map(norm, parts)).encode(), hashlib.sha256).hexdigest()


def same(a, b):
    return hmac.compare_digest(str(a).encode(), str(b).encode())


def stopper(sub, path, tab):
    def stop(status, eyebrow, title, text, detail, css="alert"):
        return status, HTML, S.page(title, sub, f'<section class="section"><div class="eyebrow">{e(eyebrow)}</div><h3 class="step-title">{e(title)}</h3><p class="lead">{e(text)}</p>'
                                                f'<div class="notice {css}">{e(detail)}</div></section>', path, tab), ()
    return stop


def refused(code):
    print(f"refused: {code}", file=sys.stderr, flush=True)


def invalid(stop, reload, title="This form is no longer valid", code="FORM_ALTERED"):
    refused(code)
    return stop(403, "Not accepted", title, reload, "Nothing was changed.", "danger")


def expired_form(stop, reload):
    refused("FORM_EXPIRED")
    return stop(403, "Not accepted", "This form expired", f"Forms are good for 15 minutes. {reload}", "Nothing was changed.", "danger")


def unverified(stop, title, done="it was recorded", code="not verified"):
    return stop(500, "Unavailable", title, f"Do not assume {done} or left unchanged.", f"Status: DEGRADED · {code}", "danger")


def written(stop, call, verify, refused=(), refusal=("", ""), what="it was recorded"):
    """Run one write -> (its read-back result, None) or (None, the screen to show): a code in `refused` is a clear
    409; any other Notion error or unverified result is 'cannot prove it'.
    """
    try:
        done = call()
    except NotionError as error:
        code = str(error)[:60]
        return None, (stop(409, "Not changed", refusal[0], refusal[1], f"Refused: {code}. Nothing was changed.") if code in refused else unverified(stop, verify, what, code))
    return (done, None) if done.get("verified") is True else (None, unverified(stop, verify, what))


def handler(get, post, stop, title):
    def handle(req, who, secrets):
        try:
            return (get if req["method"] == "GET" else post)(req, who, secrets)
        except NotionError as error:
            return stop(409, "Not changed", title, "The request was refused before anything was written.", f"Refused: {str(error)[:60]}")
    return handle


def signed(form, secret, tag, login, pid, st, steps=STEPS, mid=(), tail=()):
    """True for a form with a page id, one of `steps` (None: any), a whole-number expiry and a valid token `t`,
    whatever the time.
    """
    exp = form.get("exp", "")
    return bool(pid) and (steps is None or form.get("step") in steps) and exp.isascii() and exp.isdigit() and same(form.get("t"), mac(secret, tag, login, pid, *mid, st, exp, *tail))


def live(form, secret, tag, login, pid, st, now, steps=STEPS, mid=(), tail=()):
    return signed(form, secret, tag, login, pid, st, steps, mid, tail) and int(form.get("exp", "")) >= now.timestamp()


def gate(form, secret, login, now, tag, key, stop, reload, cancel=("No changes made", "Nothing was recorded.")):
    pid, step, exp, st = D._page_id(form.get(key)), form.get("step"), form.get("exp", ""), form.get("st", "")
    if not live(form, secret, tag, login, pid, st, now):
        if signed(form, secret, tag, login, pid, st):
            return pid, step, exp, st, expired_form(stop, reload)
        return pid, step, exp, st, invalid(stop, reload)
    return pid, step, exp, st, stop(200, "Cancelled", cancel[0], cancel[1], "Nothing was written.", "") if step == "cancel" else None

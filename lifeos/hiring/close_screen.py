"""LIFE-47 S2 (Career): the accepted Close Opportunity screen as server-rendered pages (reason, review, result) plus its stylesheet. Pure: strings in, bytes out; every dynamic value is HTML-escaped.
The Edge gate's CSP is `default-src 'self'`, so there is no inline script, no <style> and no style attribute: the stylesheet is served at /close.css, the one script at /close.js, and the three panes are three responses (the prototype's own markup and CSS).
The script only marks the pressed button busy (a spinner) and ignores a second press while the page loads; every page works the same without it."""
from html import escape as e

from . import models as M

CSS = """:root {font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color-scheme:light; --canvas:#f5f6f7; --paper:#fff; --ink:#171b24; --muted:#657080; --border:#e0e4e8; --border-strong:#bec6d0; --light:#fafbfc; --accent:#273d62; --accent-ink:#fff; --focus:#467dce; --signal:#305b46; --signal-bg:#eaf2ee; --alert:#97562b; --alert-bg:#fff6ed; --danger:#9a3d3d; --danger-bg:#fff1f1; }
*{box-sizing:border-box}
html,body{min-height:100%;margin:0}
body{background:var(--canvas);color:var(--ink);font-size:14px;line-height:1.5}
button,textarea,select{font:inherit}
button{cursor:pointer}
button:focus-visible,textarea:focus-visible,select:focus-visible,a:focus-visible{outline:3px solid var(--focus);outline-offset:3px}
.wrap{max-width:910px;margin:0 auto;padding:40px 24px 70px}
.mast{display:flex;justify-content:space-between;align-items:center;gap:18px;margin-bottom:30px}
.brand{display:flex;align-items:center;gap:12px;text-decoration:none;color:inherit}
.brandmark{width:33px;height:33px;border-radius:10px;background:#242c3a;color:#fff;display:grid;place-items:center;font-size:16px;font-weight:750;letter-spacing:-1px}
.brand strong{display:block;font-size:14px;letter-spacing:.085em;font-weight:740}
.brand span{display:block;font-size:12px;color:var(--muted);margin-top:-2px}
.pagehead{margin-bottom:18px}
.path{font-size:12px;color:var(--muted);margin-bottom:9px}
h1{margin:0;font-size:28px;font-weight:730;line-height:1.22;letter-spacing:-.85px}
.subtitle{color:var(--muted);margin:8px 0 0;font-size:13px}
.surface{background:var(--paper);border:1px solid var(--border);border-radius:12px;overflow:hidden;box-shadow:0 8px 36px rgba(15,24,41,.035)}
.section{padding:24px 27px;border-bottom:1px solid var(--border)}
.section:last-child{border-bottom:none}
.eyebrow{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);font-weight:740;margin-bottom:9px}
.jobhead{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}
h2{font-size:21px;letter-spacing:-.45px;line-height:1.22;margin:0 0 4px;font-weight:710}
.company{color:var(--muted);font-size:13px}
.status{background:var(--light);border:1px solid var(--border);color:#3f5066;white-space:nowrap;border-radius:20px;padding:5px 10px;font-size:12px;font-weight:680}
.facts{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-top:21px}
.fact small{display:block;font-size:11px;color:var(--muted);margin-bottom:3px}.fact b{font-size:12px;font-weight:600}
.lead{margin:0 0 18px;color:var(--muted);font-size:13px}
.reasons{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;margin-bottom:18px}
.people{display:grid;grid-template-columns:1fr;gap:9px;margin-bottom:18px}.people .fine{display:block;margin:2px 0 0;font-weight:400}
.reason{display:flex;align-items:center;gap:9px;min-height:48px;padding:11px 12px;border:1px solid var(--border);border-radius:7px;background:var(--paper);font-weight:610;font-size:12px;cursor:pointer;transition:border-color .1s,background .1s}
.reason:has(input:checked){border-color:#617491;background:#f3f6fa;box-shadow:inset 0 0 0 1px #617491}
.reason:has(input:focus-visible){outline:3px solid var(--focus);outline-offset:2px}
.reason input{accent-color:var(--accent);margin:0;flex:none}
label.fieldname{display:block;font-size:12px;font-weight:680;margin-bottom:7px}
.fine{font-size:11px;color:var(--muted);margin:6px 0 0}
textarea{width:100%;resize:vertical;min-height:76px;padding:11px 12px;border:1px solid var(--border-strong);border-radius:7px;color:var(--ink);background:var(--paper);font-size:13px;line-height:1.5}
textarea::placeholder{color:#86909d}
.actions{padding:19px 27px;display:flex;justify-content:flex-end;align-items:center;gap:9px;background:var(--light);border-top:1px solid var(--border)}
.btn{border:1px solid var(--border-strong);border-radius:7px;padding:10px 16px;background:var(--paper);font-size:12px;font-weight:710;min-height:40px;color:var(--ink)}
.btn.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}
.btn:disabled{cursor:not-allowed;opacity:.43}
.btn.link{border-color:transparent;background:transparent;color:var(--muted)}
.notice{border-left:3px solid var(--border-strong);background:var(--light);padding:11px 14px;font-size:12px;color:#4d5867;margin-top:17px;border-radius:0 5px 5px 0}
.notice.alert{border-color:#d99458;background:var(--alert-bg);color:var(--alert)}
.notice.danger{border-color:#bf6868;background:var(--danger-bg);color:var(--danger)}
.notice.ok{border-color:#6d9a83;background:var(--signal-bg);color:var(--signal)}
.step-title{font-size:15px;font-weight:690;margin:0 0 12px}
.review{border:1px solid var(--border);border-radius:7px;padding:16px 18px;background:var(--light);display:grid;grid-template-columns:145px 1fr;row-gap:12px;column-gap:12px;font-size:13px}
.review dt{color:var(--muted);margin:0}.review dd{font-weight:620;margin:0;overflow-wrap:anywhere}
.foot{color:var(--muted);text-align:center;font-size:11px;margin-top:25px;line-height:1.6}
.sr-only{position:absolute;height:1px;width:1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap}
@media(prefers-color-scheme:dark){:root{color-scheme:dark;--canvas:#11151b;--paper:#1c222b;--ink:#f2f4f7;--muted:#9ba7b6;--border:#343d49;--border-strong:#526071;--light:#222934;--accent:#c4d4ee;--accent-ink:#1e2837;--focus:#7eabf0;--signal:#a2d8b9;--signal-bg:#1c3529;--alert:#f2c99a;--alert-bg:#443125;--danger:#e9a7a7;--danger-bg:#412528}.brandmark{background:#dce6f5;color:#192330}.status{color:#d7dfed}.reason:has(input:checked){border-color:#8499b9;background:#263345}.notice{color:#b2bbca}textarea::placeholder{color:#8995a6}}
@media(max-width:690px){.wrap{padding:22px 14px 40px}.mast{align-items:flex-start}.pagehead h1{font-size:25px}.section{padding:20px 18px}.actions{padding:17px 18px}.reasons{grid-template-columns:repeat(2,1fr)}.facts{grid-template-columns:1fr 1fr;row-gap:15px}.review{grid-template-columns:100px 1fr}.jobhead{flex-direction:column}.demo{align-items:stretch}select{width:100%}}
@media(max-width:400px){.reasons{grid-template-columns:1fr}.facts{grid-template-columns:1fr 1fr}.mast{gap:8px}}
fieldset{border:0;margin:0;padding:0}.inl{display:inline}.btn{text-decoration:none;display:inline-flex;align-items:center;gap:8px;transition:background-color .12s,border-color .12s,color .12s,transform .06s}
.btn:not(:disabled):hover{border-color:var(--ink);background:var(--border);box-shadow:0 2px 6px rgba(0,0,0,.14)}.btn.primary:not(:disabled):hover{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 80%,var(--paper))}.btn.link:not(:disabled):hover{border-color:transparent;background:transparent;box-shadow:none;color:var(--ink);text-decoration:underline}
.btn:not(:disabled):active{transform:translateY(1px)}.busy .btn{pointer-events:none}.busy .btn:not(.spin){opacity:.43}.btn.spin{pointer-events:none}
.btn.spin::before{content:"";width:12px;height:12px;flex:none;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:spin .7s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}@media(prefers-reduced-motion:reduce){.btn.spin::before{animation-duration:2.4s}}
"""
JS = """"use strict";function idle(){document.querySelectorAll(".busy,.spin").forEach(function(n){n.classList.remove("busy","spin")})}function busy(n,b){n.classList.add("busy");if(b)b.classList.add("spin");setTimeout(idle,15000)}
document.addEventListener("submit",function(ev){var f=ev.target;if(f.classList.contains("busy")){ev.preventDefault();return}busy(f,ev.submitter)});
document.addEventListener("click",function(ev){var a=ev.target.closest&&ev.target.closest("a.btn");if(!a||a.target||a.hasAttribute("download")||ev.defaultPrevented||ev.metaKey||ev.ctrlKey||ev.shiftKey||ev.altKey)return;busy(document.body,a)});
window.addEventListener("pageshow",idle);
"""
NONE = "Not recorded"


def _hidden(fields):
    return "".join(f'<input type="hidden" name="{e(k)}" value="{e(v)}">' for k, v in fields.items())


def page(title, sub, body, path="Daily Report / Active Opportunities / Close", tab="Close opportunity"):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="light dark">'
            f'<title>LIFE OS · {e(tab)}</title><link rel="stylesheet" href="/close.css"><script src="/close.js" defer></script></head><body><div class="wrap"><header class="mast"><div class="brand"><div class="brandmark" aria-hidden="true">L</div>'
            f'<div><strong>LIFE OS</strong><span>Hiring · Opportunity actions</span></div></div></header><div class="pagehead"><div class="path">{e(path)}</div><h1>{e(title)}</h1>'
            f'<p class="subtitle">{e(sub)}</p></div><main class="surface">{body}</main><div class="foot">LIFE OS · Closing a LinkedIn contact is separate from closing a hiring opportunity.</div></div></body></html>').encode()


def _job(opp):
    source = "Job Ledger application" if opp["kind"] == "ledger" else "Hiring Pipeline Notes page"
    return (f'<section class="section"><div class="eyebrow">Opportunity to be changed</div><div class="jobhead"><div><h2>{e(opp["role"])}</h2><div class="company">{e(opp["company"])} · Requisition {NONE.lower()}</div></div>'
            f'<span class="status">{e(source)}</span></div><div class="facts"><div class="fact"><small>Canonical job</small><b>{e(opp["company"])}</b></div><div class="fact"><small>Current stage</small>'
            f'<b>Unavailable on this screen</b></div><div class="fact"><small>Source</small><b>{e(source)}</b></div></div></section>')


def form(opp, fields, picked="", note="", notice=""):
    reasons = "".join(f'<label class="reason"><input type="radio" name="outcome" value="{e(r)}" required{" checked" if r == picked else ""}>{e(r)}</label>' for r in (tuple(M.LEAD_REASONS) if opp["kind"] == "notes" else ()) + M.OUTCOMES)
    warn = f'<div class="notice alert">{e(notice)}</div>' if notice else ""
    return page("Close opportunity", "Review the exact opportunity before making a decision.", _job(opp) + (
        f'<form method="post" action="/">{_hidden(fields)}<section class="section"><div class="eyebrow">Action · Close</div><p class="lead">Choose why you\'re closing this opportunity. Nothing is changed simply by opening this page.</p>'
        f'<fieldset><legend class="step-title">Closure reason <span aria-label="Required">*</span></legend><div class="reasons">{reasons}</div></fieldset>'
        f'<label class="fieldname" for="note">Context or closure note <span class="fine inl">(required for Other)</span></label>'
        f'<textarea id="note" name="notes" maxlength="200" placeholder="Optional details or supporting evidence…">{e(note)}</textarea>'
        f'<div class="fine">If you choose Other, a short explanation is required. Notes are capped at 200 characters.</div>{warn}'
        '<div class="notice">Before the write, LIFE OS rechecks your identity, the exact job, its current state and the permitted closure.</div></section>'
        '<div class="actions"><button class="btn link" name="step" value="cancel" formnovalidate>Cancel</button><button class="btn primary" name="step" value="review">Review closure →</button></div></form>'))


def review(opp, fields, outcome, note):
    return page("Review closure", "Your confirmation is required before the action can proceed.", (
        f'<form method="post" action="/">{_hidden(fields)}<section class="section"><div class="eyebrow">Review action</div><p class="step-title">Confirm that this is the exact job you intend to close.</p>'
        f'<dl class="review"><dt>Company</dt><dd>{e(opp["company"])}</dd><dt>Role</dt><dd>{e(opp["role"])}</dd><dt>Requisition</dt><dd>{NONE}</dd><dt>Reason</dt><dd>{e(outcome)}</dd>'
        f'<dt>Note</dt><dd>{e(note) or "None supplied"}</dd></dl><div class="notice alert">Confirming is a consequential action: the closure is shown as done only after the canonical record is read back.</div></section>'
        '<div class="actions"><button class="btn" name="step" value="edit" formnovalidate>← Edit reason</button><button class="btn primary" name="step" value="confirm">Confirm closure</button></div></form>'))


def result(eyebrow, title, text, detail, css="ok"):
    return page(title, "See the status below. A closure counts as done only after the canonical record is read back.", f'<section class="section"><div class="eyebrow">{e(eyebrow)}</div><h3 class="step-title">{e(title)}</h3><p class="lead">{e(text)}</p><div class="notice {css}">{e(detail)}</div></section>')

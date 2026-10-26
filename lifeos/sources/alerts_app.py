"""LIFE-75 (Jim, Oct 26 2026): the Alert settings screen on the private Close service (Oracle; only Jim's iPhone and MBP are admitted by the Edge gate). GET /alerts shows the master
switch (alerts on or off), the mute, and one row per registered alert with a Page/Info badge and three channel boxes (Phone, Telegram, WhatsApp). Saving is a signed review and
confirm, like the other screens: the proposed change is shown against a fresh read, and the write is reported only after the three GitHub repository variables read back as sent.
The settings live in repository variables, so every run already receives them as environment: ALERTS_ENABLED ("on" or "off"), ALERTS_MUTED_UNTIL (a time, or "off"), ALERT_SETTINGS
(lifeos/platform/alert_settings.py: only the choices that differ from the registry). The Edge gate passes the optional `ghvars` credential (a token that can edit this repository's
variables and nothing else) to this path only; without it the screen says so and changes nothing. A PAGE alert's phone box is fixed on. Fixed codes only; no alert text, topic or token is shown."""
import hashlib
import json
import urllib.error
import urllib.request
from datetime import datetime
from html import escape as e
from zoneinfo import ZoneInfo

from lifeos.hiring import close_screen as S
from lifeos.platform import alert_registry as registry, alert_settings as settings, alerts
from . import close_app as C

API = "https://api.github.com/repos/jimmarkunas/life-os-v7/actions/variables"
PATH, TAB = "LIFE OS / Command Center / Alert settings", "Alert settings"
SWITCH, MUTE, DOC = settings.ENABLED_VARIABLE, alerts.MUTE_VARIABLE, settings.SETTINGS_VARIABLE
LABELS = {"ntfy": "Phone", "telegram": "Telegram", "whatsapp": "WhatsApp"}
CENTRAL = ZoneInfo("America/Chicago")
CSS = """.switchrow{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 0;border-top:1px solid var(--border)}.switchrow:first-of-type{border-top:0}
.switchrow strong{display:block}.switchrow small{color:var(--muted)}
.sw{position:relative;display:inline-block;width:52px;height:30px;flex:none}.sw input{position:absolute;inset:0;width:100%;height:100%;margin:0;opacity:0;cursor:pointer;z-index:2}
.sw i{position:absolute;inset:0;background:var(--border-strong);border-radius:30px;transition:background .15s}.sw i::after{content:"";position:absolute;top:3px;left:3px;width:24px;height:24px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.35);transition:transform .15s}
.sw input:checked+i{background:var(--signal)}.sw input:checked+i::after{transform:translateX(22px)}.sw input:focus-visible+i{outline:3px solid var(--focus);outline-offset:3px}
table.alerts{width:100%;border-collapse:collapse;margin-top:6px}table.alerts th,table.alerts td{text-align:left;padding:10px 8px;border-top:1px solid var(--border);vertical-align:top}
table.alerts th{font-size:12px;color:var(--muted);font-weight:650;border-top:0}table.alerts td.ch{text-align:center;width:74px}table.alerts th.ch{text-align:center}
table.alerts input[type=checkbox]{width:20px;height:20px;accent-color:var(--accent)}table.alerts .meaning{color:var(--muted);font-size:12.5px}
.badge{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.04em;padding:2px 8px;border-radius:10px}.badge.page{background:var(--alert-bg);color:var(--alert)}.badge.info{background:var(--signal-bg);color:var(--signal)}
.group td{background:var(--light);font-size:12px;font-weight:700;letter-spacing:.06em;color:var(--muted);text-transform:uppercase}
@media(max-width:690px){table.alerts .meaning{display:none}table.alerts td.ch{width:56px}}
"""


def _shell(html):
    """The Close screens' page, with this screen's own stylesheet and its own words in the header and footer."""
    return (html.replace(b'<link rel="stylesheet" href="/close.css">', b'<link rel="stylesheet" href="/close.css"><link rel="stylesheet" href="/alerts.css">')
            .replace(b"Hiring \xc2\xb7 Opportunity actions", b"Alerts \xc2\xb7 Settings")
            .replace(b"LIFE OS \xc2\xb7 Closing a LinkedIn contact is separate from closing a hiring opportunity.", b"LIFE OS \xc2\xb7 Saved as repository variables; the next run uses them."))


def _stop(status, eyebrow, title, text, detail, css="alert"):
    return status, C.HTML, _shell(S.result(eyebrow, title, text, detail, css)), ()


def handle_css(req, who, secrets):
    return 200, "text/css; charset=utf-8", CSS.encode(), ()


def _call(token, method, name="", body=None, opener=urllib.request.urlopen):
    request = urllib.request.Request(API + (f"/{name}" if name else ""), None if body is None else json.dumps(body).encode(), method=method, headers={
        "Authorization": "Bearer " + token.decode(), "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "lifeos-close"})
    with opener(request, timeout=10) as response:
        return json.loads(response.read() or b"{}")


def read(token, opener=urllib.request.urlopen):
    """-> {variable: value or None} for the three variables; any GitHub failure other than 'not found' raises."""
    out = {}
    for name in (SWITCH, MUTE, DOC):
        try:
            out[name] = str(_call(token, "GET", name, opener=opener).get("value") or "")
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            out[name] = None
    return out


def write(token, values, opener=urllib.request.urlopen):
    """Create or update each variable (update first, create on 'not found')."""
    for name, value in values.items():
        try:
            _call(token, "PATCH", name, {"name": name, "value": value}, opener)
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            _call(token, "POST", "", {"name": name, "value": value}, opener)


def model(values, now):
    """The screen's state from the variables: {"enabled", "mute" (Central 'YYYY-MM-DDTHH:MM' or ''), "channels": {registry key: tuple}}."""
    mute = ""
    raw = (values.get(MUTE) or "").strip()
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        when = when.replace(tzinfo=CENTRAL) if when.tzinfo is None else when.astimezone(CENTRAL)
        mute = when.strftime("%Y-%m-%dT%H:%M") if when > now else ""
    except ValueError:
        pass
    saved = settings.parse(values.get(DOC) or "")
    chosen = {key: (tuple(saved[key]) if key in saved else tuple(spec.channels)) for key, spec in registry.REGISTRY.items()}
    return {"enabled": settings.enabled({SWITCH: values.get(SWITCH) or ""}), "mute": mute,
            "channels": {key: (("ntfy",) + tuple(c for c in c_ if c != "ntfy") if registry.REGISTRY[key].severity == registry.PAGE else c_) for key, c_ in chosen.items()}}


def from_form(form):
    """The proposed state from a posted form, or None when the mute is not a time."""
    mute = (form.get("mute") or "").strip()
    if mute:
        try:
            datetime.strptime(mute, "%Y-%m-%dT%H:%M")
        except ValueError:
            return None
    channels = {}
    for key, spec in registry.REGISTRY.items():
        chosen = tuple(c for c in registry.CHANNELS if form.get(f"ch:{key}:{c}") == "1")
        channels[key] = ("ntfy",) + tuple(c for c in chosen if c != "ntfy") if spec.severity == registry.PAGE else chosen
    return {"enabled": form.get("enabled") == "1", "mute": mute, "channels": channels}


def values_for(state):
    """The three variable values a state is saved as."""
    mute = "off"
    if state["mute"]:
        mute = datetime.strptime(state["mute"], "%Y-%m-%dT%H:%M").replace(tzinfo=CENTRAL).isoformat()
    return {SWITCH: "on" if state["enabled"] else "off", MUTE: mute, DOC: settings.document(state["channels"]) or "{}"}


def digest(state):
    return hashlib.sha256(json.dumps(values_for(state), sort_keys=True).encode()).hexdigest()


def changes(old, new):
    """Plain-language lines for what a save would change."""
    out = []
    if old["enabled"] != new["enabled"]:
        out.append("Alerts will be turned " + ("ON: pushes reach your phone from the next run." if new["enabled"] else "OFF: nothing is sent."))
    if old["mute"] != new["mute"]:
        out.append("Mute " + (f"until {new['mute'].replace('T', ' ')} Central." if new["mute"] else "cleared."))
    for key, spec in registry.REGISTRY.items():
        if tuple(old["channels"][key]) != tuple(new["channels"][key]):
            out.append(f"{key.replace('_', ' ').replace('*', '(each)')}: " + (", ".join(LABELS[c] for c in new["channels"][key]) or "no channel"))
    return out


def _switch(name, label, note, on):
    return (f'<div class="switchrow"><div><strong>{e(label)}</strong><small>{e(note)}</small></div><label class="sw"><input type="checkbox" name="{e(name)}" value="1"'
            f'{" checked" if on else ""}><i></i></label></div>')


def fixed(channel, spec):
    """A PAGE alert's phone box is always on and cannot be unticked."""
    return channel == "ntfy" and spec.severity == registry.PAGE


def _rows(state):
    by_owner = {}
    for key, spec in registry.REGISTRY.items():
        by_owner.setdefault(spec.owner, []).append((key, spec))
    html = ['<table class="alerts"><thead><tr><th>Alert</th>' + "".join(f'<th class="ch">{e(LABELS[c])}</th>' for c in registry.CHANNELS) + "</tr></thead><tbody>"]
    for owner in sorted(by_owner):
        html.append(f'<tr class="group"><td colspan="{1 + len(registry.CHANNELS)}">{e(owner)}</td></tr>')
        for key, spec in sorted(by_owner[owner]):
            chosen = state["channels"][key]
            cells = "".join(
                f'<td class="ch"><input type="checkbox" name="ch:{e(key)}:{c}" value="1" aria-label="{e(LABELS[c])} for {e(key)}"{" checked" if (c in chosen or fixed(c, spec)) else ""}'
                f'{" disabled" if fixed(c, spec) else ""}></td>' for c in registry.CHANNELS)
            html.append(f'<tr><td><strong>{e(key.replace("_", " ").replace("*", "(each)"))}</strong> <span class="badge {e(spec.severity)}">{e(spec.severity.upper())}</span>'
                        f'<div class="meaning">{e(spec.meaning)}</div></td>{cells}</tr>')
    html.append("</tbody></table>")
    return "".join(html)


def _fields(state):
    """Hidden copies of a proposed state, for the review step."""
    out = {"enabled": "1"} if state["enabled"] else {}
    out["mute"] = state["mute"]
    for key, chosen in state["channels"].items():
        for c in chosen:
            out[f"ch:{key}:{c}"] = "1"
    return out


def _page(state, secret, login, now, notice=""):
    exp = str(int(now.timestamp()) + C.TTL)
    token = C._mac(secret, "alerts", login, exp)
    body = (f'<form method="post" action="/alerts"><input type="hidden" name="exp" value="{exp}"><input type="hidden" name="t" value="{token}">'
            f'<section class="section"><div class="eyebrow">Master switches</div>'
            + _switch("enabled", "Alerts", "Off means nothing is sent to any channel. It also stays off if this setting is ever missing.", state["enabled"])
            + f'<div class="switchrow"><div><strong>Mute until</strong><small>Central time. Leave empty for no mute. A mute silences every channel.</small></div>'
              f'<input type="datetime-local" name="mute" value="{e(state["mute"])}"></div></section>'
              f'<section class="section"><div class="eyebrow">Channels by alert</div><p class="lead">Tick where each alert should go. A PAGE alert always reaches the phone.</p>{_rows(state)}</section>'
              f'{notice}<div class="actions"><button class="btn primary" name="step" value="review">Review changes →</button></div></form>')
    return 200, C.HTML, _shell(S.page("Alert settings", "What gets sent, and where.", body, PATH, TAB)), ()


def _get(req, who, secrets, opener):
    token = secrets.get("ghvars")
    if not token:
        return _stop(503, "Not set up", "The settings credential is not installed", "This screen cannot read or save settings yet.", "Nothing was changed.")
    try:
        values = read(token, opener)
    except Exception:                                                              # noqa: BLE001 - fixed text only; never the token, URL or reply
        return _stop(503, "Unavailable", "GitHub did not answer", "The settings could not be read.", "Nothing was changed.", "danger")
    return _page(model(values, C._now()), secrets["link"], who["login"], C._now())


def _post(req, who, secrets, opener):
    form, secret, login, token = req["form"], secrets["link"], who["login"], secrets.get("ghvars")
    exp, now = form.get("exp", ""), C._now()
    if not token:
        return _stop(503, "Not set up", "The settings credential is not installed", "This screen cannot save settings yet.", "Nothing was changed.")
    state = from_form(form)
    if state is None or not (exp.isascii() and exp.isdigit()) or int(exp) < now.timestamp():
        return _stop(403, "Not accepted", "This form is no longer valid", "Reload Alert settings.", "Nothing was changed.", "danger")
    step = form.get("step")
    if step == "review":
        if not C._same(form.get("t"), C._mac(secret, "alerts", login, exp)):
            return _stop(403, "Not accepted", "This form is no longer valid", "Reload Alert settings.", "Nothing was changed.", "danger")
        try:
            current = model(read(token, opener), now)
        except Exception:                                                          # noqa: BLE001
            return _stop(503, "Unavailable", "GitHub did not answer", "The settings could not be read.", "Nothing was changed.", "danger")
        lines = changes(current, state)
        fields = _fields(state) | {"exp": exp, "t": C._mac(secret, "alerts-confirm", login, exp, digest(state))}
        shown = "".join(f"<li>{e(line)}</li>" for line in lines) or "<li>No change.</li>"
        return 200, C.HTML, _shell(S.page("Review changes", "Nothing is saved until you confirm.", (
            f'<form method="post" action="/alerts">{S._hidden(fields)}<section class="section"><div class="eyebrow">Review</div><ul class="lead">{shown}</ul>'
            '<div class="notice alert">Confirming saves these as repository variables; the next run uses them. The change is shown as done only after GitHub reports the same values back.</div></section>'
            '<div class="actions"><button class="btn link" name="step" value="cancel" formnovalidate>Cancel</button>'
            f'<button class="btn primary" name="step" value="confirm"{" disabled" if not lines else ""}>Save</button></div></form>'), PATH, TAB)), ()
    if step == "cancel":
        return _stop(200, "Cancelled", "Nothing saved", "The settings stay as they were.", "Nothing was written.", "")
    if step != "confirm" or not C._same(form.get("t"), C._mac(secret, "alerts-confirm", login, exp, digest(state))):
        return _stop(403, "Not accepted", "This form is no longer valid", "Reload Alert settings.", "Nothing was changed.", "danger")
    want = values_for(state)
    try:
        write(token, want, opener)
        got = read(token, opener)
    except Exception:                                                              # noqa: BLE001
        return _stop(500, "Unavailable", "Could not verify the save", "Do not assume the settings were saved or left unchanged. Open Alert settings again.", "Status: DEGRADED · GitHub error", "danger")
    if any((got.get(name) or "") != value for name, value in want.items()):
        return _stop(500, "Unavailable", "Could not verify the save", "Do not assume the settings were saved or left unchanged. Open Alert settings again.", "Status: DEGRADED · not read back", "danger")
    return _stop(200, "Saved", "Alert settings saved", "GitHub reports the same values back; the next run uses them.",
                 f"Alerts {'ON' if state['enabled'] else 'OFF'} · {('muted until ' + state['mute'].replace('T', ' ') + ' Central') if state['mute'] else 'no mute'}.", "ok")


def handle(req, who, secrets, opener=urllib.request.urlopen):
    if req["method"] == "GET":
        return _get(req, who, secrets, opener)
    return _post(req, who, secrets, opener)

"""The Honcho line (Oracle card) and the two Anthropic rows with a summary line (AI Capacity card).

A reading that cannot be had is None and prints UNVERIFIED, never zero. RESERVED (a hold against the meter's cap) and
ACTUAL (billed spend) are never swapped. Red only for ALERT and STOPPED."""

import json
import socket
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.platform import credit_window

TZ = ZoneInfo("America/Chicago")
PROMO_USD = credit_window.PROMO_USD
NEAR_REMAINING = 25
PORT, MAX_REPLY, WAIT, TTL = 47341, 4096, 100, 600
FLAGS = ("health", "failed", "exhausted", "stopped")
RED = "red"


def _num(v):
    return v is None or (type(v) in (int, float) and v >= 0)


def _stamp(v):
    if v is None:
        return True
    try:
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return isinstance(v, str) and len(v) <= 40
    except (ValueError, AttributeError):
        return False


def honcho_ok(h):
    if h is None:
        return True
    backup, meter = (h.get("backup"), h.get("meter")) if isinstance(h, dict) else (0, 0)
    return (
        isinstance(h, dict)
        and set(h) == set(FLAGS) | {"backup", "meter"}
        and all(h[k] in (None, True, False) for k in FLAGS)
        and (
            backup is None
            or (
                isinstance(backup, dict)
                and set(backup) == {"at", "ok"}
                and type(backup["ok"]) is bool
                and isinstance(backup["at"], str)
                and _stamp(backup["at"])
            )
        )
        and (
            meter is None
            or (
                isinstance(meter, dict)
                and set(meter) == {"cap", "reserved"}
                and _num(meter["cap"])
                and _num(meter["reserved"])
                and meter["cap"] is not None
                and meter["reserved"] is not None
            )
        )
    )


def anthropic_ok(a):
    return a is None or (
        isinstance(a, dict)
        and set(a) == {"billed_usd", "honcho_usd", "as_of"}
        and _num(a["billed_usd"])
        and _num(a["honcho_usd"])
        and _stamp(a["as_of"])
    )


def _moment(iso, now):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TZ)


def _clock(moment):
    return f"{moment.hour % 12 or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'} CT"


def _day(moment):
    return f"{moment.strftime('%b')} {moment.day}, {_clock(moment)}"


def state(h):
    """-> "healthy" | "ALERT" | "STOPPED" | "UNVERIFIED"."""
    if h is None or any(h[k] is None for k in FLAGS):
        return "UNVERIFIED"
    if h["exhausted"] or h["stopped"]:
        return "STOPPED"
    if h["failed"] or h["health"] is False:
        return "ALERT"
    return "healthy"


def open_alerts(h):
    return 0 if h is None else sum(bool(h[k]) for k in ("failed", "exhausted", "stopped")) + int(h["health"] is False)


def line(h, now):
    """-> the Oracle card's one Honcho line as [(text, color or None)], placed under `Services:`."""
    read = f"read {_clock(now.astimezone(TZ))}"
    word = state(h)
    if word == "UNVERIFIED":
        return [(f"Honcho: UNVERIFIED · could not read status · {read}", None)]
    backup = h["backup"]
    when = _day(_moment(backup["at"], now)) if backup else None
    if word == "STOPPED":
        return [
            ("Honcho: STOPPED ✗", RED),
            (f" · meter {'EXHAUSTED' if h['exhausted'] else 'STOPPED'} · open alerts {open_alerts(h)} · {read}", None),
        ]
    if word == "ALERT":
        why = f"backup FAILED{' ' + when if when else ''}" if h["failed"] else "API not answering"
        return [("Honcho: ALERT ✗", RED), (f" · {why} · open alerts {open_alerts(h)} · {read}", None)]
    last = f"last backup {when} ✓" if backup and backup["ok"] else "last backup UNVERIFIED"
    return [(f"Honcho: healthy ✓ · {last} · open alerts 0 · {read}", None)]


def _bar(pct):
    filled = min(10, max(0, int(pct / 10 + 0.5)))
    return "▰" * filled + "▱" * (10 - filled)


def _money(v, places=2):
    return f"${v:.{places}f}"


def ranked(h, a, now):
    """-> [(percent left or 101 when unknown, cells)] for the promo credit and the Honcho meter."""
    end = credit_window.window(now)[1]
    resets = f"{end.strftime('%a %b')} {end.day}, {_clock(end)}"
    billed = a["billed_usd"] if a else None
    if billed is None:
        promo = (101, ["Anthropic · promo credit", "UNVERIFIED", "UNVERIFIED", "monthly", resets, "UNAVAILABLE"])
    else:
        left = max(0.0, PROMO_USD - billed)
        status = "EXHAUSTED" if left <= 0 else "NEAR LIMIT" if left / PROMO_USD * 100 <= NEAR_REMAINING else "AVAILABLE"
        promo = (
            left / PROMO_USD * 100,
            [
                "Anthropic · promo credit",
                f"{_money(left)} of ${PROMO_USD:.0f}",
                f"{_bar(billed / PROMO_USD * 100)} {_money(billed)} billed",
                "monthly",
                resets,
                status,
            ],
        )
    meter = h["meter"] if h else None
    actual = a["honcho_usd"] if a else None
    if meter is None:
        row = (101, ["Anthropic · Honcho meter", "UNVERIFIED", "UNVERIFIED", "monthly cap", resets, "UNAVAILABLE"])
    else:
        cap, reserved = meter["cap"], meter["reserved"]
        stopped = bool(h["exhausted"] or h["stopped"]) or reserved >= cap
        left = max(0.0, (cap - reserved) / cap * 100) if cap else 0.0
        status = "EXHAUSTED" if stopped else "NEAR LIMIT" if left <= NEAR_REMAINING else "AVAILABLE"
        used = f"{_bar(reserved / cap * 100 if cap else 100)} {_money(reserved, 3)} reserved · " + (
            f"{_money(actual)} actual" if actual is not None else "actual UNVERIFIED"
        )
        row = (
            left,
            [
                "Anthropic · Honcho meter",
                f"{_money(max(0.0, cap - reserved))} of {_money(cap)} cap",
                used,
                "monthly cap",
                resets,
                status,
            ],
        )
    return [promo, row]


def rows(h, a, now):
    return [cells for _, cells in ranked(h, a, now)]


def summary(h, a, now):
    read = _clock(now.astimezone(TZ))
    billed = a["billed_usd"] if a else None
    meter = h["meter"] if h else None
    first = (
        f"{_money(billed)} billed of ${PROMO_USD:.0f} promo credit this window (Anthropic report, read {read})"
        if billed is not None
        else "billed spend UNVERIFIED (Anthropic report not read)"
    )
    if meter is None:
        second = "Honcho meter UNVERIFIED"
    else:
        actual = a["honcho_usd"] if a else None
        second = (
            f"Honcho meter {_money(meter['reserved'], 3)} reserved, "
            + (f"{_money(actual)} actual" if actual is not None else "actual UNVERIFIED")
            + f" of {_money(meter['cap'])} monthly cap"
        )
    return f"Anthropic API · {first} · {second}"


def fetch(host, port=PORT):
    data = b""
    with socket.create_connection((host, port), timeout=WAIT) as s:
        s.sendall(b"oracle1\n")
        while len(data) <= MAX_REPLY:
            chunk = s.recv(MAX_REPLY + 1 - len(data))
            if not chunk:
                break
            data += chunk
    return data


def read(host, now, send=fetch):
    """-> (honcho, anthropic) from Oracle's `oracle1` stats, or (None, None) when they cannot be had. Never raises."""
    try:
        raw = send(host)
        if not raw or len(raw) > MAX_REPLY:
            return None, None
        p = json.loads(raw)
        seen = datetime.fromisoformat(p["observed_at"].replace("Z", "+00:00"))
        if seen.tzinfo is None or abs((now - seen).total_seconds()) > TTL:
            return None, None
        h, a = p.get("honcho"), p.get("anthropic")
        return (h if honcho_ok(h) else None), (a if anthropic_ok(a) else None)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None, None

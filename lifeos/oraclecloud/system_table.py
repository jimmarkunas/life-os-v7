"""The System callout's table (sixteen rows, five columns) as pure functions of the readings and `now`."""

# Bars: filled = share used (budgets, capacity), share healthy (Services / Backup / Connections) or days left of
# 7 (sign-ins). Orange at 70% used or 2 days left; red at 90%, 1 day left, a service down or a connection
# failing. A row is stale when its reading is over two hours old or missing: the page's previous values are kept
# and the status says `STALE since <when>`.
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.oraclecloud import backup_watch
from lifeos.platform import credit_window, honcho_status

TZ = ZoneInfo("America/Chicago")
BAR = 10
STALE_AFTER = timedelta(hours=2)
ORANGE_AT, RED_AT = 70, 90
SIGNIN_DAYS = 7
HEADER = ("System", "Usage", "Reading", "Resets / expires", "Status")
LABELS = (
    "Oracle · Memory",
    "Oracle · Disk",
    "Oracle · CPU (24 h avg)",
    "Oracle · Cores",
    "Oracle · Cost",
    "Oracle · Services",
    "Honcho · Backup",
    "Codex · 5-hour",
    "Codex · Week",
    "Anthropic · Promo credit",
    "Honcho · Monthly cap",
    "OpenRouter · Key budget",
    "Cloud Fit · Daily guard",
    "Connections",
    "Sign-in · Usage meter",
    "Sign-in · Cloud Fit",
)
SERVICES = (("gateway", "Gateway"), ("telegram", "Telegram"), ("whatsapp", "WhatsApp"), ("alerts", "Alerts"))
STALE_WORDS = re.compile(r"STALE since (.+?)(?: ·|$)")


def when(moment, now):
    moment, now = moment.astimezone(TZ), now.astimezone(TZ)
    return (
        f"{moment.hour % 12 or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"
        if moment.date() == now.date()
        else f"{moment.strftime('%b')} {moment.day}"
    )


def day_time(moment):
    moment = moment.astimezone(TZ)
    return f"{moment.strftime('%a %b')} {moment.day}, {moment.hour % 12 or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def short_day_time(moment):
    return day_time(moment).split(" ", 1)[1]


def reset_of(iso, now, label=None):
    """A provider's reset time (ISO) as `Tue Nov 24, 4:10 PM`; one already past rolls forward by whole windows."""
    if not iso:
        return "—"
    try:
        at = datetime.fromisoformat(iso).astimezone(TZ)
    except ValueError:
        return "—"
    step = {"5h": timedelta(hours=5), "Week": timedelta(days=7)}.get(label)
    if at <= now.astimezone(TZ):
        if step is None:
            return "—"
        at += step * (int((now.astimezone(TZ) - at) / step) + 1)
    return day_time(at)


def colour_used(pct):
    return "red" if pct >= RED_AT else "orange" if pct >= ORANGE_AT else "green"


def colour_days(days):
    return "red" if days <= 1 else "orange" if days <= 2 else "green"


def bar(filled, colour):
    filled = min(BAR, max(0, int(filled + 0.5)))
    return [(text, c) for text, c in (("█" * filled, colour), ("█" * (BAR - filled), "gray")) if text]


def blocks_of(share):
    return max(1, round(share / 100 * BAR)) if share > 0 else 0


def used_bar(pct):
    return bar(blocks_of(pct), colour_used(pct))


class Row:
    def __init__(self, label, usage=None, reading="—", resets="—", reason=None, as_of=None, have=True):
        self.label, self.usage, self.reading, self.resets, self.reason, self.as_of, self.have = (
            label,
            usage or [("█" * BAR, "gray")],
            reading,
            resets,
            reason,
            as_of,
            have,
        )


def _money(v, places=2):
    return f"${v:,.{places}f}"


def _pct_row(label, pct, as_of, suffix="%"):
    if pct is None:
        return Row(label, have=False)
    return Row(label, used_bar(pct), f"{pct:.0f}{suffix}", "—", "NEAR LIMIT" if pct >= RED_AT else None, as_of)


def _capacity_rows(stats, as_of, now):
    if stats is None:
        return [Row(label, have=False) for label in LABELS[:7]]
    a1, cost, services = stats["a1_cores"], stats["cost"], stats["services"]
    rows = [_pct_row(LABELS[0], stats["memory_pct"], as_of), _pct_row(LABELS[1], stats["disk_pct"], as_of)]
    cpu = stats["cpu_24h_pct"]
    rows.append(
        Row(LABELS[2], have=False)
        if cpu is None
        else Row(LABELS[2], used_bar(cpu), f"{cpu:.0f}%", "—", "NEAR LIMIT" if cpu >= RED_AT else None, as_of)
    )
    if a1 is None:
        rows.append(Row(LABELS[3], have=False))
    else:
        total = a1["used"] + a1["available"]
        share = a1["used"] / total * 100 if total else 100
        colour = "orange" if a1["available"] == 0 else colour_used(share)
        rows.append(Row(LABELS[3], bar(blocks_of(share), colour), f"{a1['used']} of {total} in use", "—", None, as_of))
    if cost is None:
        rows.append(Row(LABELS[4], have=False))
    else:
        amount = cost["amount"]
        symbol = "$" if cost["currency"] == "USD" else cost["currency"] + " "
        rows.append(
            Row(
                LABELS[4],
                bar(0, "gray") if amount == 0 else bar(BAR, "red"),
                f"{symbol}{amount:,.2f} this month",
                "—",
                None if amount == 0 else "CHARGES",
                as_of,
            )
        )
    backup = backup_watch.healthy(stats["backup"])
    states = [services[k] for k, _ in SERVICES] + [backup]
    if any(v is None for v in states):
        rows.append(Row(LABELS[5], have=False))
    else:
        up = sum(bool(v) for v in states)
        down = [label for (key, label) in SERVICES if services[key] is False] + (["Backup"] if backup is False else [])
        rows.append(
            Row(
                LABELS[5],
                bar(up / len(states) * BAR, "green" if up == len(states) else "red"),
                f"{up} of {len(states)} up",
                "—",
                None if not down else "DOWN " + ", ".join(down),
                as_of,
            )
        )
    honcho = stats.get("honcho")
    word = honcho_status.state(honcho) if stats.get("honcho_reported") else "UNVERIFIED"
    if word == "UNVERIFIED":
        rows.append(Row(LABELS[6], have=False))
    else:
        backup_at = honcho["backup"]["at"] if honcho and honcho.get("backup") else None
        last = (
            f" · last {short_day_time(datetime.fromisoformat(backup_at.replace('Z', '+00:00')))}" if backup_at else ""
        )
        if word == "healthy":
            rows.append(Row(LABELS[6], bar(BAR, "green"), "healthy" + last, "—", None, as_of))
        else:
            rows.append(Row(LABELS[6], bar(0, "red"), word.lower() + last, "—", "DOWN", as_of))
    return rows


def _window(pool, label):
    return next((w for w in (pool or {}).get("windows", []) if w["label"] == label), None)


def _budget_rows(stats, stats_at, limits, limits_at, now):
    out = []
    pool = next((p for p in (limits or {}).get("pools", []) if p["provider"] == "openai"), None)
    for label, window in ((LABELS[7], "5h"), (LABELS[8], "Week")):
        w = _window(pool, window)
        if w is None:
            out.append(Row(label, have=False))
            continue
        used = w["used"]
        out.append(
            Row(
                label,
                used_bar(used),
                f"{used:g}% used",
                reset_of(w["reset_at"], now, window),
                "NEAR LIMIT" if used >= RED_AT else None,
                limits_at,
            )
        )
    anth = stats.get("anthropic") if stats else None
    end = credit_window.window(now)[1]
    reset = day_time(end)
    if anth is None or anth["billed_usd"] is None:
        out.append(Row(LABELS[9], have=False))
    else:
        billed = anth["billed_usd"]
        share = billed / credit_window.PROMO_USD * 100
        as_of = datetime.fromisoformat(anth["as_of"].replace("Z", "+00:00")) if anth.get("as_of") else None
        out.append(
            Row(
                LABELS[9],
                used_bar(share),
                f"{_money(billed)} of ${credit_window.PROMO_USD:g}",
                reset,
                "NEAR LIMIT" if share >= RED_AT else None,
                as_of,
            )
        )
    meter = (stats.get("honcho") or {}).get("meter") if stats else None
    if not meter or not meter["cap"]:
        out.append(Row(LABELS[10], have=False))
    else:
        share = meter["reserved"] / meter["cap"] * 100
        out.append(
            Row(
                LABELS[10],
                used_bar(share),
                f"{_money(meter['reserved'])} of ${meter['cap']:g}",
                reset,
                "NEAR LIMIT" if share >= RED_AT else None,
                stats_at,
            )
        )
    router = next((p for p in (limits or {}).get("pools", []) if p["provider"] == "openrouter"), None)
    w = (router or {}).get("windows", [None])[0] if router and router.get("windows") else None
    out.append(
        Row(LABELS[11], have=False)
        if w is None
        else Row(
            LABELS[11],
            used_bar(w["used"]),
            f"{w['used']:g}% used",
            "monthly",
            "NEAR LIMIT" if w["used"] >= RED_AT else None,
            limits_at,
        )
    )
    cap = (limits or {}).get("safety_cap")
    out.append(
        Row(LABELS[12], have=False)
        if not cap or not cap["limit"]
        else Row(
            LABELS[12],
            used_bar(cap["runs_today"] / cap["limit"] * 100),
            f"{cap['runs_today']:,} of {cap['limit']:,} runs",
            "midnight",
            "NEAR LIMIT" if cap["runs_today"] / cap["limit"] * 100 >= RED_AT else None,
            limits_at,
        )
    )
    return out


def connections_row(summary, now):
    """summary: None (System health database unreadable) or {"total", "connected", "down": [names], "off": n,
    "stale_since": ...}."""
    if summary is None:
        return Row(LABELS[13], have=False)
    total, connected, down = summary["total"], summary["connected"], summary["down"]
    reading = (
        f"{connected} of {total} connected"
        + "".join(f" · {name} not connected" for name in down)
        + f" · {summary['off']} off by design"
    )
    reason = " · ".join(f"{name.upper()} DOWN" for name in down) or None
    return Row(
        LABELS[13],
        bar(connected / total * BAR if total else 0, "green" if connected == total else "red"),
        reading,
        "—",
        reason,
        summary.get("stale_since"),
    )


def signin_rows(limits, limits_at, now):
    signins = (limits or {}).get("signins") or {}
    out = []
    for label, key in ((LABELS[14], "meter"), (LABELS[15], "fit")):
        entry = signins.get(key)
        if entry is None:
            out.append(Row(label, have=False))
            continue
        words = {"EXPIRED": "EXPIRED", "COOLDOWN": "FAILING", "UNREADABLE": "UNREADABLE"}
        if entry["state"] != "OK":
            out.append(Row(label, bar(0, "red"), words[entry["state"]].lower(), "—", words[entry["state"]], limits_at))
            continue
        if not entry["expires"]:
            out.append(Row(label, have=False))
            continue
        at = datetime.fromisoformat(entry["expires"]).astimezone(TZ)
        left = (at - now.astimezone(TZ)).total_seconds() / 86400
        days = int(left) if left > 0 else 0
        reason = None if days > 2 else f"EXPIRES {day_time(at)}" if left > 0 else "EXPIRED"
        out.append(
            Row(
                label,
                bar(min(days, SIGNIN_DAYS) / SIGNIN_DAYS * BAR, colour_days(days)),
                f"{days} days left" if days != 1 else "1 day left",
                day_time(at),
                reason,
                limits_at,
            )
        )
    return out


def readings(stats, stats_at, limits, limits_at, connections, now):
    """-> the sixteen Rows in fixed order. `stats`: validated `oracle1` readings or None; `limits`: Oracle's
    `limits` or None."""
    rows = (
        _capacity_rows(stats, stats_at, now)
        + _budget_rows(stats, stats_at, limits, limits_at, now)
        + [connections_row(connections, now)]
        + signin_rows(limits, limits_at, now)
    )
    for row in rows:
        if row.as_of is not None and row.as_of.tzinfo is None:
            row.as_of = row.as_of.replace(tzinfo=TZ)
    return rows


def previous_of(table):
    out = {}
    for cells in table[1:]:
        if len(cells) == 5:
            label = "".join(t for t, _ in cells[0])
            out[label] = (
                cells[1],
                "".join(t for t, _ in cells[2]),
                "".join(t for t, _ in cells[3]),
                "".join(t for t, _ in cells[4]),
            )
    return out


def finish(rows, previous, previous_stamp, now):
    """Rows -> the 17 table rows (header first), applying the stale rule. `previous_stamp`: when the page last said
    it was written, or None."""
    out = [[[(h, "bold")] for h in HEADER]]
    for row in rows:
        old = previous.get(row.label)
        carried = STALE_WORDS.search(old[3]).group(1) if old and STALE_WORDS.search(old[3]) else None
        if not row.have:
            usage, reading, resets = (old[0], old[1], old[2]) if old else ([("█" * BAR, "gray")], "unavailable", "—")
            since = carried or (when(previous_stamp, now) if previous_stamp else "unknown")
            status = [(f"STALE since {since}", "red")]
        else:
            usage, reading, resets = row.usage, row.reading, row.resets
            reasons = [row.reason] if row.reason else []
            if row.as_of is not None and now - row.as_of > STALE_AFTER:
                reasons.append(f"STALE since {when(row.as_of, now)}")
            status = [(" · ".join(reasons), "red")] if reasons else [("OK", "green")]
        out.append([[(row.label, None)], usage, [(reading, None)], [(resets, None)], status])
    return out


def stale_count(table):
    return sum(any("STALE" in t for t, _ in cells[4]) for cells in table[1:])

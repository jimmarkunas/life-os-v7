"""step 4d: the System table's bars, colours, status words, fixed row order and stale rule (pure functions)."""

import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from lifeos.oraclecloud import system_table as st

CT = ZoneInfo("America/Chicago")
NOW = dt.datetime(2026, 11, 25, 9, 0, tzinfo=CT)
SVC = {"gateway": True, "telegram": True, "whatsapp": True, "alerts": True}


def stats(**over):
    base = {
        "cost": {"amount": 0, "currency": "USD"},
        "cpu_24h_pct": 5.4,
        "memory_pct": 23.0,
        "disk_pct": 66.0,
        "a1_cores": {"used": 4, "available": 0},
        "reclaim": "NONE",
        "services": dict(SVC),
        "chatgpt": None,
        "backup": {"ok": True, "code": "ok", "age_h": 3},
        "workers": None,
        "backups": None,
        "honcho_reported": True,
        "honcho": {
            "health": True,
            "failed": False,
            "exhausted": False,
            "stopped": False,
            "backup": {"at": "2026-11-24T09:30:00Z", "ok": True},
            "meter": {"cap": 25, "reserved": 0.7},
        },
        "anthropic": {"billed_usd": 0.02, "honcho_usd": 0.1, "as_of": "2026-11-25T14:30:00Z"},
    }
    return {**base, **over}


def limits(**over):
    base = {
        "read_at": "2026-11-25T14:55:00Z",
        "unavailable": [],
        "safety_cap": {"runs_today": 54, "limit": 5000},
        "pools": [
            {
                "provider": "openai",
                "stop": 50,
                "windows": [
                    {"label": "5h", "used": 7, "reset_at": "2026-11-25T20:10:00Z"},
                    {"label": "Week", "used": 3, "reset_at": "2026-11-27T09:10:00Z"},
                ],
            },
            {"provider": "openrouter", "windows": [{"label": "month", "used": 0, "reset_at": None}]},
        ],
        "signins": {
            "meter": {"state": "OK", "expires": "2026-11-29T04:35:00Z"},
            "fit": {"state": "OK", "expires": "2026-11-29T19:01:00Z"},
        },
    }
    return {**base, **over}


CONN = {"total": 12, "connected": 11, "down": ["Concord"], "off": 3, "stale_since": None}


def build(stats_=None, limits_=None, conn=CONN, now=NOW, previous=None, stamp=None, stats_at=None, limits_at=None):
    built = st.readings(
        stats() if stats_ is None else (None if stats_ is False else stats_),
        stats_at or now,
        limits() if limits_ is None else (None if limits_ is False else limits_),
        limits_at or now,
        conn,
        now,
    )
    return st.finish(built, previous or {}, stamp, now)


def text(cell):
    return "".join(t for t, _ in cell)


class Bars(unittest.TestCase):
    def test_ten_blocks_filled_in_the_colour_and_the_rest_gray(self):
        self.assertEqual(st.used_bar(23), [("██", "green"), ("████████", "gray")])
        self.assertEqual(st.used_bar(66), [("███████", "green"), ("███", "gray")])
        self.assertEqual(st.used_bar(100), [("██████████", "red")])  # a full bar has no gray part
        self.assertEqual(st.used_bar(0), [("██████████", "gray")])
        self.assertEqual(
            st.used_bar(2), [("█", "green"), ("█████████", "gray")]
        )  # a share above zero always shows one block

    def test_the_colour_turns_orange_at_70_and_red_at_90_percent_used(self):
        self.assertEqual(
            [st.colour_used(p) for p in (0, 69.9, 70, 89.9, 90, 100)],
            ["green", "green", "orange", "orange", "red", "red"],
        )

    def test_the_days_left_colour_is_orange_at_two_days_and_red_at_one(self):
        self.assertEqual([st.colour_days(d) for d in (7, 3, 2, 1, 0)], ["green", "green", "orange", "red", "red"])


class Rows(unittest.TestCase):
    def test_sixteen_rows_in_the_fixed_order_with_five_columns(self):
        table = build()
        self.assertEqual([text(r[0]) for r in table[1:]], list(st.LABELS))
        self.assertEqual(len(table), 17)
        self.assertEqual([text(c) for c in table[0]], list(st.HEADER))
        self.assertTrue(all(len(r) == 5 for r in table))
        self.assertEqual(
            build(stats(memory_pct=99), limits(), CONN)[1][0], [("Oracle · Memory", None)]
        )  # the order never depends on the readings

    def test_a_healthy_page_reads_like_the_approved_wireframe(self):
        rows = {text(r[0]): r for r in build()[1:]}
        memory = rows["Oracle · Memory"]
        self.assertEqual(
            (memory[1], text(memory[2]), text(memory[4])), ([("██", "green"), ("████████", "gray")], "23%", "OK")
        )
        cores = rows["Oracle · Cores"]
        self.assertEqual(
            (cores[1], text(cores[2]), cores[4]), ([("██████████", "orange")], "4 of 4 in use", [("OK", "green")])
        )  # no headroom stays orange, with an OK
        self.assertEqual(text(rows["Oracle · Cost"][2]), "$0.00 this month")
        self.assertEqual(
            (text(rows["Oracle · Services"][2]), rows["Oracle · Services"][1]), ("5 of 5 up", [("██████████", "green")])
        )
        self.assertEqual(text(rows["Honcho · Backup"][2]), "healthy · last Nov 24, 3:30 AM")
        self.assertEqual(
            (text(rows["Codex · 5-hour"][2]), text(rows["Codex · 5-hour"][3])), ("7% used", "Wed Nov 25, 2:10 PM")
        )
        self.assertEqual(
            (text(rows["Anthropic · Promo credit"][2]), text(rows["Anthropic · Promo credit"][3])),
            ("$0.02 of $100", "Sat Dec 5, 6:00 PM"),
        )
        self.assertEqual(text(rows["Honcho · Monthly cap"][2]), "$0.70 of $25")
        self.assertEqual(
            (text(rows["OpenRouter · Key budget"][2]), text(rows["OpenRouter · Key budget"][3])), ("0% used", "monthly")
        )
        self.assertEqual(
            (text(rows["Cloud Fit · Daily guard"][2]), text(rows["Cloud Fit · Daily guard"][3])),
            ("54 of 5,000 runs", "midnight"),
        )
        self.assertEqual({text(r[4]) for k, r in rows.items() if k != "Connections"}, {"OK"})

    def test_a_status_is_a_green_ok_or_a_red_reason(self):
        table = build(
            stats(memory_pct=95, services={**SVC, "alerts": False}),
            limits(
                pools=[
                    {
                        "provider": "openai",
                        "windows": [
                            {"label": "5h", "used": 91, "reset_at": "2026-11-25T20:10:00Z"},
                            {"label": "Week", "used": 3, "reset_at": None},
                        ],
                    }
                ]
            ),
        )
        rows = {text(r[0]): r for r in table[1:]}
        self.assertEqual(rows["Oracle · Memory"][4], [("NEAR LIMIT", "red")])
        self.assertEqual(rows["Oracle · Memory"][1][0], ("██████████", "red"))
        self.assertEqual(rows["Oracle · Services"][4], [("DOWN Alerts", "red")])
        self.assertEqual((rows["Oracle · Services"][1][0][1], text(rows["Oracle · Services"][2])), ("red", "4 of 5 up"))
        self.assertEqual(rows["Codex · 5-hour"][4], [("NEAR LIMIT", "red")])
        self.assertEqual(rows["Oracle · Disk"][4], [("OK", "green")])

    def test_orange_budgets_stay_ok_until_ninety_percent(self):
        rows = {text(r[0]): r for r in build(stats(disk_pct=75))[1:]}
        self.assertEqual(
            (rows["Oracle · Disk"][1][0], rows["Oracle · Disk"][4]), (("████████", "orange"), [("OK", "green")])
        )

    def test_sign_ins_show_days_left_out_of_seven_and_expire_loudly(self):
        rows = {text(r[0]): r for r in build()[1:]}
        meter, fit = rows["Sign-in · Usage meter"], rows["Sign-in · Cloud Fit"]
        self.assertEqual(
            (text(meter[2]), text(meter[3]), meter[4]), ("3 days left", "Sat Nov 28, 10:35 PM", [("OK", "green")])
        )
        self.assertEqual((text(fit[2]), fit[1]), ("4 days left", [("██████", "green"), ("████", "gray")]))
        soon = limits(
            signins={
                "meter": {"state": "OK", "expires": "2026-11-26T20:00:00Z"},
                "fit": {"state": "OK", "expires": "2026-11-27T20:00:00Z"},
            }
        )
        rows = {text(r[0]): r for r in build(limits_=soon)[1:]}
        self.assertEqual(
            (rows["Sign-in · Usage meter"][1][0][1], text(rows["Sign-in · Usage meter"][2])), ("red", "1 day left")
        )
        self.assertTrue(text(rows["Sign-in · Usage meter"][4]).startswith("EXPIRES Thu Nov 26"))
        self.assertEqual(rows["Sign-in · Cloud Fit"][1][0][1], "orange")
        gone = limits(
            signins={"meter": {"state": "EXPIRED", "expires": None}, "fit": {"state": "COOLDOWN", "expires": None}}
        )
        rows = {text(r[0]): r for r in build(limits_=gone)[1:]}
        self.assertEqual(
            (rows["Sign-in · Usage meter"][4], rows["Sign-in · Cloud Fit"][4]),
            ([("EXPIRED", "red")], [("FAILING", "red")]),
        )

    def test_the_connections_row_counts_what_should_work_and_names_what_is_down(self):
        row = {text(r[0]): r for r in build()[1:]}["Connections"]
        self.assertEqual(text(row[2]), "11 of 12 connected · Concord not connected · 3 off by design")
        self.assertEqual(
            (row[1], row[4]), ([("█████████", "red"), ("█", "gray")], [("CONCORD DOWN", "red")])
        )  # a failing connection turns the bar red
        whole = {
            text(r[0]): r
            for r in build(conn={"total": 12, "connected": 12, "down": [], "off": 3, "stale_since": None})[1:]
        }["Connections"]
        self.assertEqual(
            (whole[1], whole[4], text(whole[2])),
            ([("██████████", "green")], [("OK", "green")], "12 of 12 connected · 3 off by design"),
        )


class Stale(unittest.TestCase):
    def test_a_reading_more_than_two_hours_old_is_stale_since_its_own_time(self):
        old = NOW - dt.timedelta(hours=3)
        rows = {text(r[0]): r for r in build(limits_at=old)[1:]}
        self.assertEqual(rows["Codex · 5-hour"][4], [("STALE since 6:00 AM", "red")])
        self.assertEqual(text(rows["Codex · 5-hour"][2]), "7% used")
        exactly = {text(r[0]): r for r in build(limits_at=NOW - dt.timedelta(hours=2))[1:]}
        self.assertEqual(exactly["Codex · 5-hour"][4], [("OK", "green")])  # exactly two hours is not stale
        days = {text(r[0]): r for r in build(limits_at=dt.datetime(2026, 11, 16, 20, 30, tzinfo=CT))[1:]}
        self.assertEqual(days["Cloud Fit · Daily guard"][4], [("STALE since Nov 16", "red")])

    def test_a_reading_that_cannot_be_had_keeps_the_pages_last_values_and_says_stale_since_the_previous_run(self):
        first = build()
        previous = st.previous_of([[[(h, "bold")] for h in st.HEADER]] + [r for r in first[1:]])
        stamp = dt.datetime(2026, 11, 25, 8, 2, tzinfo=CT)
        later = NOW + dt.timedelta(minutes=58)
        rows = {text(r[0]): r for r in build(limits_=False, previous=previous, stamp=stamp, now=later)[1:]}
        self.assertEqual(rows["Codex · 5-hour"][4], [("STALE since 8:02 AM", "red")])
        self.assertEqual((text(rows["Codex · 5-hour"][2]), rows["Codex · 5-hour"][1]), ("7% used", first[8][1]))
        self.assertEqual(rows["Oracle · Memory"][4], [("OK", "green")])  # Oracle answered, so its rows are fresh
        again = st.previous_of(
            [[[(h, "bold")] for h in st.HEADER]]
            + [r for r in build(limits_=False, previous=previous, stamp=stamp, now=later)[1:]]
        )
        rows = {
            text(r[0]): r
            for r in build(limits_=False, previous=again, stamp=later, now=later + dt.timedelta(hours=1))[1:]
        }
        self.assertEqual(rows["Codex · 5-hour"][4], [("STALE since 8:02 AM", "red")])

    def test_with_no_previous_row_a_missing_reading_says_unavailable(self):
        rows = {text(r[0]): r for r in build(stats_=False, previous={}, stamp=None)[1:]}
        self.assertEqual(
            (text(rows["Oracle · Memory"][2]), rows["Oracle · Memory"][4]),
            ("unavailable", [("STALE since unknown", "red")]),
        )

    def test_the_connections_row_goes_stale_with_the_database_that_backs_it(self):
        row = {
            text(r[0]): r
            for r in build(
                conn={
                    "total": 12,
                    "connected": 5,
                    "down": ["Jira"],
                    "off": 3,
                    "stale_since": dt.datetime(2026, 11, 16, 20, 30, tzinfo=CT),
                }
            )[1:]
        }["Connections"]
        self.assertEqual(row[4], [("JIRA DOWN · STALE since Nov 16", "red")])

    def test_the_stale_count_counts_rows_not_words(self):
        self.assertEqual(st.stale_count(build()), 0)
        self.assertEqual(st.stale_count(build(limits_=False, previous={}, stamp=None)), 6)


class BarWidthTests(unittest.TestCase):
    def test_every_bar_is_exactly_ten_full_block_cells_whatever_the_fill(self):
        """empty cells are gray."""
        for filled in range(0, 11):
            for colour in ("green", "orange", "red"):
                pieces = st.bar(filled, colour)
                self.assertEqual("".join(text for text, _ in pieces), "\u2588" * 10)
                self.assertEqual(
                    sum(len(text) for text, c in pieces if c == "gray"), 10 - filled if colour != "gray" else 10
                )
                self.assertEqual(sum(len(text) for text, c in pieces if c == colour), filled)
        self.assertEqual(st.used_bar(0), [("\u2588" * 10, "gray")])
        self.assertEqual("".join(t for t, _ in st.used_bar(66)), "\u2588" * 10)

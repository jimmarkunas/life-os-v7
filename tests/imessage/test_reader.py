import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from lifeos.imessage import attention_mcp as m

NOW = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)
H, D = timedelta(hours=1), timedelta(days=1)
ALICE, BOB, SHORT = "+15550100001", "bob@example.com", "262966"           # invented handles


def typedstream(text):
    """The shape macOS writes to message.attributedBody when `text` is empty."""
    raw = text.encode()
    size = bytes([len(raw)]) if len(raw) < 0x80 else b"\x81" + len(raw).to_bytes(2, "little")
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+"
            + size + raw + b"\x86\x84\x02iI\x01\x01\x92\x84\x84\x84\x0cNSDictionary\x00")


class Fixture:
    """A synthetic chat.db with the columns the reader relies on, plus a Contacts database."""

    def __init__(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "chat.db")
        con = sqlite3.connect(self.db)
        con.executescript("""
            CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT, service TEXT);
            CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, style INTEGER, display_name TEXT, chat_identifier TEXT);
            CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER, message_date INTEGER);
            CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, attributedBody BLOB, handle_id INTEGER, service TEXT, date INTEGER,
                                  is_from_me INTEGER, is_read INTEGER, item_type INTEGER, associated_message_type INTEGER DEFAULT 0,
                                  date_retracted INTEGER DEFAULT 0, date_edited INTEGER DEFAULT 0, cache_has_attachments INTEGER DEFAULT 0);
        """)
        con.executemany("INSERT INTO handle VALUES (?, ?, ?)", [(1, ALICE, "iMessage"), (2, BOB, "iMessage"), (3, SHORT, "SMS")])
        con.executemany("INSERT INTO chat VALUES (?, ?, ?, ?, ?)", [(10, "c-alice", 45, "", ALICE), (11, "c-bob", 45, None, BOB), (12, "c-group", 43, "Family", "chat99"),
                                                                    (13, "c-short", 45, None, SHORT)])
        self.con = con
        self.rowid = 100

    def add(self, chat, age, handle=1, text="hello", attributed=None, mine=0, read=0, kind=0, assoc=0, retracted=0, edited=0, attach=0, service="iMessage",
            guid=None, also=None):
        self.rowid += 1
        guid = guid or "g%d" % self.rowid
        self.con.execute("INSERT INTO message (ROWID, guid, text, attributedBody, handle_id, service, date, is_from_me, is_read, item_type, associated_message_type,"
                         " date_retracted, date_edited, cache_has_attachments) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (self.rowid, guid, text, attributed, 0 if mine else handle, service, m._ticks(NOW - age), mine, read, kind, assoc, retracted, edited, attach))
        for c in [chat] + ([also] if also else []):
            self.con.execute("INSERT INTO chat_message_join VALUES (?, ?, ?)", (c, self.rowid, 0))
        self.con.commit()
        return self.rowid

    def contacts(self):
        root = os.path.join(self.dir, "AddressBook")
        os.makedirs(os.path.join(root, "Sources", "s1"))
        con = sqlite3.connect(os.path.join(root, "Sources", "s1", "AddressBook-v22.abcddb"))
        con.executescript("""
            CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME TEXT, ZLASTNAME TEXT, ZORGANIZATION TEXT);
            CREATE TABLE ZABCDPHONENUMBER (ZOWNER INTEGER, ZFULLNUMBER TEXT);
            CREATE TABLE ZABCDEMAILADDRESS (ZOWNER INTEGER, ZADDRESS TEXT);
            INSERT INTO ZABCDRECORD VALUES (1, 'Alice', 'Example', NULL), (2, NULL, NULL, 'Example Corp');
            INSERT INTO ZABCDPHONENUMBER VALUES (1, '+1 (555)0100001');
            INSERT INTO ZABCDEMAILADDRESS VALUES (2, 'BOB@example.com');
        """)
        con.commit()
        con.close()
        return root


class Counts(unittest.TestCase):
    def test_counting_definitions_exclusions_and_windows(self):
        f = Fixture()
        f.add(10, H)                                                  # unread, last 24 h
        f.add(10, 2 * H, read=1)                                      # read: excluded
        f.add(11, 3 * D, handle=2)                                    # unread, last 7 days
        f.add(13, 40 * D, handle=3, service="SMS")                    # unread, historical, SMS
        f.add(12, 2 * H, handle=2, service="RCS")                     # unread in a group
        f.add(10, H, assoc=2000)                                      # a reaction: excluded
        f.add(10, H, retracted=1)                                     # unsent: excluded
        f.add(10, H, mine=1)                                          # from Jim: excluded
        f.add(10, H, kind=1)                                          # system row: excluded
        f.add(10, 5 * H, guid="dup", also=12)                         # one message joined to two chats: counted once
        out = m.counts(f.db, NOW)
        self.assertEqual((out["health"], out["sync"], out["count_reliable"]), ("CURRENT", "UNVERIFIED", False))
        c = out["counts"]
        self.assertEqual(c["unread"]["last_24_hours"], {"messages": 3, "conversations": 2})
        self.assertEqual(c["unread"]["last_7_days"], {"messages": 4, "conversations": 3})
        self.assertEqual(c["unread"]["all_time"], {"messages": 5, "conversations": 4})
        self.assertEqual(c["unread_messages_by_service_all_time"], {"iMessage": 3, "RCS": 1, "SMS": 1})
        self.assertEqual(c["unread_conversations_all_time"], {"group": 1, "direct": 3})
        self.assertIn("badge", out["definitions"])
        self.assertIn("iPhone", out["limitation"])

    def test_unreadable_or_off_schema_database_has_no_counts(self):
        self.assertEqual((m.counts("/nonexistent/chat.db", NOW)["health"], m.counts("/nonexistent/chat.db", NOW)["counts"]), ("DISCONNECTED", None))
        path = os.path.join(tempfile.mkdtemp(), "chat.db")
        with sqlite3.connect(path) as con:
            con.execute("CREATE TABLE message (ROWID INTEGER PRIMARY KEY, date INTEGER)")
        self.assertEqual((m.counts(path, NOW)["health"], m.counts(path, NOW)["counts"]), ("DEGRADED", None))


class Recent(unittest.TestCase):
    def test_newest_incoming_with_sender_text_and_reference(self):
        f = Fixture()
        f.add(10, 30 * H)                                             # outside a 24 h window
        a = f.add(10, 2 * H, text=None, attributed=typedstream("Can you call me before 5pm?"))
        b = f.add(11, H, handle=2, text="Invoice attached", attach=1, edited=1, read=1)
        f.add(10, H, mine=1, text="mine")                             # outgoing: not in recent
        f.add(12, 3 * H, handle=1, text="dinner at 7", service="RCS")
        f.add(13, 4 * H, handle=3, text=None, attributed=typedstream("x" * 300), service="SMS")
        out = m.recent(24, f.db, NOW, f.contacts())
        self.assertEqual((out["health"], out["contacts"], out["hours"]), ("CURRENT", "CURRENT", 24))
        msgs = out["messages"]
        self.assertEqual([x["ref"] for x in msgs][:2], ["m%d" % b, "m%d" % a])
        first, second = msgs[0], msgs[1]
        self.assertEqual(first["sender"], {"handle": BOB, "name": "Example Corp"})
        self.assertEqual((first["unread"], first["edited"], first["attachment"], first["text"]), (False, True, True, "Invoice attached"))
        self.assertEqual(second["sender"], {"handle": ALICE, "name": "Alice Example"})
        self.assertEqual((second["text"], second["chat"], second["group"], second["unread"]), ("Can you call me before 5pm?", "c10", False, True))
        group = [x for x in msgs if x["group"]][0]
        self.assertEqual((group["chat_name"], group["service"]), ("Family", "RCS"))
        long = [x for x in msgs if x["service"] == "SMS"][0]
        self.assertEqual((len(long["text"]), long["sender"]["name"]), (300, None))
        self.assertTrue(all(not x["from_me"] for x in msgs))
        self.assertEqual(msgs[0]["sent_at"], "2026-10-14T14:00:00Z")

    def test_bounded_and_deduplicated(self):
        f = Fixture()
        for i in range(70):
            f.add(10, timedelta(minutes=i + 1), text="t%d" % i)
        f.add(10, timedelta(seconds=30), guid="dup", text="once", also=12)
        msgs = m.recent(24, f.db, NOW, f.contacts())["messages"]
        self.assertEqual(len(msgs), 50)
        self.assertEqual([x["text"] for x in msgs].count("once"), 1)
        long = Fixture()
        long.add(10, H, text="y" * 5000)
        one = m.recent(24, long.db, NOW, "/nonexistent")
        self.assertEqual((len(one["messages"][0]["text"]), one["messages"][0]["text_truncated"], one["contacts"]), (2000, True, "UNAVAILABLE"))

    def test_failures_return_no_messages(self):
        out = m.recent(24, "/nonexistent/chat.db", NOW)
        self.assertEqual((out["health"], out["messages"]), ("DISCONNECTED", None))


class Conversation(unittest.TestCase):
    def test_last_twenty_of_one_chat_both_directions(self):
        f = Fixture()
        for i in range(25):
            f.add(10, timedelta(minutes=60 - i), text="a%d" % i, mine=i % 2)
        f.add(11, timedelta(minutes=1), handle=2, text="other chat")
        out = m.conversation(10, f.db, NOW, f.contacts())
        msgs = out["messages"]
        self.assertEqual((out["chat"], len(msgs), msgs[0]["text"]), ("c10", 20, "a24"))
        self.assertTrue(all(x["chat"] == "c10" for x in msgs))
        self.assertEqual({x["from_me"] for x in msgs}, {True, False})
        self.assertTrue(all(x["sender"] is None for x in msgs if x["from_me"]))
        self.assertEqual(m.conversation(999, f.db, NOW)["messages"], [])


class Arguments(unittest.TestCase):
    def call(self, name, args):
        ops = {"imessage_counts": lambda a: {"health": "CURRENT"}, "imessage_recent": lambda a: {"health": "CURRENT", "hours": a["hours"]},
               "imessage_conversation": lambda a: {"health": "CURRENT", "chat": a["chat"]}}
        return m.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}, ops=ops)

    def test_only_bounded_integers_are_accepted(self):
        self.assertEqual(self.call("imessage_recent", {"hours": 72})["result"]["structuredContent"]["hours"], 72)
        self.assertEqual(self.call("imessage_conversation", {"chat": 10})["result"]["structuredContent"]["chat"], 10)
        self.assertIn("result", self.call("imessage_counts", {}))
        for name, args in (("imessage_recent", {"hours": 0}), ("imessage_recent", {"hours": 73}), ("imessage_recent", {"hours": "24"}),
                           ("imessage_recent", {"hours": True}), ("imessage_recent", {"hours": 1.5}), ("imessage_recent", {}),
                           ("imessage_recent", {"hours": 1, "query": "SELECT text FROM message"}), ("imessage_conversation", {"chat": 0}),
                           ("imessage_conversation", {"chat": "c10"}), ("imessage_conversation", {"chat": -1}), ("imessage_conversation", {"path": "/etc"}),
                           ("imessage_counts", {"window": "all"}), ("imessage_send", {}), ("imessage_mark_read", {"chat": 1})):
            self.assertEqual(self.call(name, args)["error"], {"code": -32602, "message": "unsupported tool or arguments"}, (name, args))


class ReadOnly(unittest.TestCase):
    def test_no_tool_changes_the_database(self):
        f = Fixture()
        f.add(10, H)
        f.con.close()
        with open(f.db, "rb") as fh:
            before = fh.read()
        m.counts(f.db, NOW)
        m.recent(24, f.db, NOW)
        m.conversation(10, f.db, NOW)
        with open(f.db, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(sorted(os.listdir(f.dir)), ["chat.db"])


if __name__ == "__main__":
    unittest.main()


class Urgency(unittest.TestCase):
    from lifeos.imessage import openclaw_imessage as w

    def level(self, text, name="Alice Example", handle=ALICE, group=False, context=()):
        return self.w.classify({"text": text, "sender": {"handle": handle, "name": name}, "group": group}, context)

    def test_levels(self):
        cases = [("Mom is in the hospital, call me now", "TIME_CRITICAL", False),
                 ("URGENT: need the signed form today by 3pm", "TIME_CRITICAL", False),
                 ("The recruiter wants to schedule a call for your interview", "ACTION_REQUIRED", False),
                 ("Can you send the invoice by Friday?", "ACTION_REQUIRED", False),
                 ("Are we still on for dinner?", "ATTENTION", False),
                 ("Haha nice photo", "INFORMATIONAL", False),
                 ("this is urgent", "ATTENTION", True)]
        for text, level, review in cases:
            v = self.level(text)
            self.assertEqual((v["level"], v["review"]), (level, review), text)
            self.assertTrue(v["next_action"])

    def test_codes_short_codes_and_marketing_never_escalate(self):
        self.assertEqual(self.level("Your verification code is 482913. Do not share it.", name=None, handle="+15550100009")["level"], "INFORMATIONAL")
        self.assertEqual(self.level("URGENT: your account expires today, reply now", name=None, handle="262966")["level"], "INFORMATIONAL")

    def test_unknown_senders_and_groups_are_reviewed_not_promoted(self):
        v = self.level("There was an accident, call me now", name=None, handle="+15550100009")
        self.assertEqual((v["level"], v["review"]), ("ACTION_REQUIRED", True))
        v = self.level("Emergency at the school pickup", group=True)
        self.assertEqual((v["level"], v["review"]), ("ACTION_REQUIRED", True))
        self.assertEqual(self.level("Can someone bring chairs?", group=True)["level"], "INFORMATIONAL")
        self.assertEqual(self.level("Jim can you bring chairs?", group=True)["level"], "ATTENTION")

    def test_context_carries_a_deadline_or_scheduling_thread(self):
        self.assertEqual(self.level("Does that work?", context=["The interview panel moved to Thursday"])["level"], "ACTION_REQUIRED")
        self.assertEqual(self.level("Does that work?")["level"], "ATTENTION")

    def test_missing_text_is_reviewed(self):
        v = self.w.classify({"text": None, "sender": {"handle": ALICE, "name": "Alice Example"}, "group": False})
        self.assertEqual((v["level"], v["review"]), ("INFORMATIONAL", True))

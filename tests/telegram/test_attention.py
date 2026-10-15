import unittest

from lifeos.telegram import attention as a

ME, BOT, FRIEND, OTHER = 1, 50, 7, 8
MAIN = [{"list": {"@type": "chatListMain"}, "order": "100"}]
ARCHIVE = [{"list": {"@type": "chatListArchive"}, "order": "100"}]
LOUD = {"use_default_mute_for": False, "mute_for": 0}
QUIET = {"use_default_mute_for": False, "mute_for": 600000}


def msg(sender, date, outgoing=False, chat_sender=False):
    return {"is_outgoing": outgoing, "date": date,
            "sender_id": {"@type": "messageSenderChat", "chat_id": -5} if chat_sender else {"@type": "messageSenderUser", "user_id": sender}}


def chat(i, unread=0, last=None, positions=MAIN, notes=LOUD, kind="chatTypePrivate", channel=False, marked=False):
    t = {"@type": kind, **({"is_channel": channel} if kind == "chatTypeSupergroup" else {})}
    return {"id": i, "type": t, "positions": positions, "unread_count": unread, "is_marked_as_unread": marked, "notification_settings": notes, "last_message": last}


class FakeSession:
    def __init__(self, chats, history=None, users=None):
        self.chats, self.history, self.me = {c["id"]: c for c in chats}, history or {}, ME
        self.users = users or {}
        self.calls = []

    def call(self, request, deadline):
        self.calls.append(request["@type"])
        if request["@type"] == "getChatHistory":
            return {"messages": self.history.get(request["chat_id"], [])}
        if request["@type"] == "getUser":
            return self.users.get(request["user_id"], {"first_name": "", "type": {"@type": "userTypeRegular"}})
        raise AssertionError(request)


class Counts(unittest.TestCase):
    def test_main_list_only_with_muted_chats_counted_separately(self):
        chats = {c["id"]: c for c in (chat(1, 5), chat(2, 3, notes=QUIET), chat(3, 0, marked=True), chat(4, 9, positions=ARCHIVE), chat(5, 0),
                                      chat(6, 2, notes={"use_default_mute_for": True}, kind="chatTypeSupergroup", channel=True))}
        scopes = {"notificationSettingsScopeChannelChats": 600000}                       # channels muted by default
        self.assertEqual(a.counts(chats, scopes), {"chats_main": 5, "unread_messages": 10, "unread_messages_unmuted": 5,
                                                   "unread_chats": 4, "unread_chats_unmuted": 2})


class LatestIncoming(unittest.TestCase):
    def test_newest_message_from_another_person_wins(self):
        s = FakeSession([chat(1, last=msg(FRIEND, 100)), chat(2, last=msg(OTHER, 200)), chat(3, last=msg(FRIEND, 300), positions=ARCHIVE)])
        self.assertEqual(a.latest_incoming(s, 0), (OTHER, 200))
        self.assertEqual(s.calls, [])

    def test_my_own_newer_message_does_not_replace_the_sender_and_its_chat_history_is_checked(self):
        s = FakeSession([chat(1, last=msg(ME, 500, outgoing=True)), chat(2, last=msg(OTHER, 200))],
                        history={1: [msg(ME, 500, outgoing=True), msg(FRIEND, 400), msg(OTHER, 50)]})
        self.assertEqual(a.latest_incoming(s, 0), (FRIEND, 400))
        self.assertEqual(s.calls, ["getChatHistory"])

    def test_channels_bots_service_and_chat_senders_are_not_people(self):
        s = FakeSession([chat(1, last=msg(BOT, 900)), chat(2, last=msg(a.TELEGRAM_SERVICE, 800)), chat(3, last=msg(0, 700, chat_sender=True)),
                         chat(4, last=msg(FRIEND, 600), kind="chatTypeSupergroup", channel=True), chat(5, last=msg(OTHER, 100))],
                        users={BOT: {"type": {"@type": "userTypeBot"}}})
        self.assertEqual(a.latest_incoming(s, 0), (OTHER, 100))

    def test_no_incoming_at_all_is_none(self):
        s = FakeSession([chat(1, last=msg(ME, 5, outgoing=True))], history={1: [msg(ME, 5, outgoing=True)]})
        self.assertIsNone(a.latest_incoming(s, 0))

    def test_display_name_is_first_and_last_never_username_or_phone(self):
        s = FakeSession([], users={FRIEND: {"first_name": " Bob ", "last_name": "Example", "usernames": {"active_usernames": ["bob_x"]},
                                            "phone_number": "15550100001", "type": {"@type": "userTypeRegular"}}})
        self.assertEqual(a.display_name(s, FRIEND, 0), "Bob Example")
        self.assertEqual(a.display_name(s, OTHER, 0), "Unknown contact")


class ReadOnlyBoundary(unittest.TestCase):
    def td(self, login=False):
        td = a.Td.__new__(a.Td)
        td.login, td.n, td.client, td.sent = login, 0, 1, []
        td.td = type("Lib", (), {"td_send": lambda self_, c, raw: td.sent.append(raw)})()
        return td

    def test_no_send_read_marking_open_or_download_request_can_leave_the_adapter(self):
        td = self.td()
        for kind in ("sendMessage", "viewMessages", "openChat", "readAllChatMentions", "downloadFile", "forwardMessages", "deleteMessages", "joinChat",
                     "setTdlibParameters_x", "logOut", "terminateSession"):
            with self.assertRaises(a.Refused):
                td.send({"@type": kind})
        self.assertEqual(td.sent, [])

    def test_sign_in_requests_are_refused_outside_the_login_command(self):
        with self.assertRaises(a.Refused):
            self.td().send({"@type": "checkAuthenticationCode", "code": "1"})
        self.assertEqual(self.td(login=True).send({"@type": "checkAuthenticationCode", "code": "1"}), 1)

    def test_storage_is_off_except_the_encrypted_session(self):
        p = a.params(1, "0" * 32, "k")
        self.assertEqual((p["use_message_database"], p["use_file_database"], p["use_chat_info_database"], p["use_secret_chats"]), (False, False, False, False))
        self.assertEqual(p["database_encryption_key"], "k")


class Observe(unittest.TestCase):
    def test_not_signed_in_or_missing_credentials_is_unavailable_with_null_counts(self):
        orig = a.db_key
        try:
            a.db_key = lambda create=False: (_ for _ in ()).throw(a.Refused("NOT_SIGNED_IN"))
            out = a.observe(creds=lambda: (1, "0" * 32))
            a.db_key = lambda create=False: "k"
            out2 = a.observe(creds=lambda: (_ for _ in ()).throw(a.Refused("CREDENTIALS_UNAVAILABLE")))
        finally:
            a.db_key = orig
        for o, code in ((out, "NOT_SIGNED_IN"), (out2, "CREDENTIALS_UNAVAILABLE")):
            self.assertEqual((o["health"], o["error"], o["latest_state"], o["latest_name"]), ("UNAVAILABLE", code, "UNAVAILABLE", None))
            self.assertTrue(all(o[k] is None for k in ("unread_messages", "unread_messages_unmuted", "unread_chats", "unread_chats_unmuted")))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/python3 -I
"""COMM-1.2 (LIFE-29, Jim's approval Oct 15 2026, Jira 10203): Jim's personal Telegram attention, read on Oracle through TDLib's JSON interface.

Installed on Oracle as ~/.local/lib/lifeos-telegram/attention.py (stdlib + the locally built libtdjson). Two forms:
    attention    one bounded observation, printed as ONE JSON line for the private comms endpoint: unread messages and unread chats in the Main
                 chat list (Archive excluded), each total and excluding muted chats, the newest genuinely incoming sender's display name, health
    login        Jim's one-time interactive sign-in (phone, code, 2FA password typed by Jim; never printed, logged or stored by this file)

The session is a full-access Telegram login, so the read-only boundary is enforced HERE: every TDLib request type must be in ALLOWED, which has no
send, edit, delete, forward, view/read-marking, chat-open, join or download request. TDLib's message, file and chat-info databases are off (no message or
media archive); its session database is encrypted with a key that never leaves Oracle. The API id/hash are resolved per run from Bitwarden through the
existing OpenClaw resolver and never stored. Anything not proven (not signed in, not connected, list not fully loaded, timeout) is a fixed health with
null counts: never a zero or a guess. Message text is never read into the output; history is fetched only to find the newest incoming sender and date."""
import base64
import ctypes
import getpass
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
LIB = os.path.join(HOME, ".local/lib/lifeos-telegram/td/lib/libtdjson.so")
STATE = os.path.join(HOME, ".local/state/lifeos-telegram")               # 0700: TDLib's encrypted session database only
DB_KEY = os.path.join(HOME, ".config/lifeos-telegram/db-key")            # 0600, generated on Oracle at login, never copied anywhere
RESOLVER = os.path.join(HOME, ".local/bin/openclaw-bws-resolver.mjs")
BWS_ENV = os.path.join(HOME, ".config/bws/openclaw.env")
NODE = os.path.join(HOME, ".openclaw/tools/node/bin/node")
SECRET_IDS = ("openclaw/telegram/apiId", "openclaw/telegram/apiHash")
ALLOWED = {"setTdlibParameters", "setAuthenticationPhoneNumber", "checkAuthenticationCode", "checkAuthenticationPassword", "close",
           "loadChats", "getChatHistory", "getUser", "getScopeNotificationSettings", "getMe"}
LOGIN_ONLY = {"setAuthenticationPhoneNumber", "checkAuthenticationCode", "checkAuthenticationPassword"}
TELEGRAM_SERVICE = 777000
MAX_HISTORY = 12                                                          # chats whose newest message is outgoing that may be scanned for an incoming one
MAX_NAME = 80
SCOPES = {"chatTypePrivate": "notificationSettingsScopePrivateChats", "chatTypeBasicGroup": "notificationSettingsScopeGroupChats",
          "chatTypeSupergroup": None}                                    # supergroup scope depends on is_channel, resolved below


class Refused(Exception):
    """A fixed state code; never source text."""


def secrets(run=subprocess.run):
    """The API id and hash from Bitwarden through the existing resolver; nothing is written or printed."""
    env = dict(os.environ)
    with open(BWS_ENV) as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.rstrip("\n").split("=", 1)
                env[k.strip()] = v.strip().strip('"')
    out = run([NODE, RESOLVER], input=json.dumps({"protocolVersion": 1, "provider": "bws", "ids": list(SECRET_IDS)}),
              capture_output=True, text=True, timeout=30, env=env)
    values = (json.loads(out.stdout or "{}").get("values") or {}) if out.returncode == 0 else {}
    api_id, api_hash = values.get(SECRET_IDS[0], ""), values.get(SECRET_IDS[1], "")
    if not str(api_id).strip().isdigit() or len(str(api_hash).strip()) != 32:
        raise Refused("CREDENTIALS_UNAVAILABLE")
    return int(api_id), api_hash.strip()


def db_key(create=False):
    if not os.path.exists(DB_KEY):
        if not create:
            raise Refused("NOT_SIGNED_IN")
        os.makedirs(os.path.dirname(DB_KEY), mode=0o700, exist_ok=True)
        fd = os.open(DB_KEY, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(base64.b64encode(os.urandom(32)).decode())
    with open(DB_KEY) as f:
        return f.read().strip()


class Td:
    """The JSON client with a request allowlist. TDLib's own log stream is switched off before the client exists."""

    def __init__(self, login=False, lib=LIB):
        self.td = ctypes.CDLL(lib)
        self.td.td_create_client_id.restype = ctypes.c_int
        self.td.td_send.argtypes = [ctypes.c_int, ctypes.c_char_p]
        self.td.td_receive.argtypes = [ctypes.c_double]
        self.td.td_receive.restype = ctypes.c_char_p
        self.td.td_execute.argtypes = [ctypes.c_char_p]
        self.td.td_execute.restype = ctypes.c_char_p
        self.td.td_execute(json.dumps({"@type": "setLogStream", "log_stream": {"@type": "logStreamEmpty"}}).encode())
        self.td.td_execute(json.dumps({"@type": "setLogVerbosityLevel", "new_verbosity_level": 0}).encode())
        self.login, self.n, self.client = login, 0, self.td.td_create_client_id()

    def send(self, request):
        kind = request["@type"]
        if kind not in ALLOWED or (kind in LOGIN_ONLY and not self.login):
            raise Refused("REQUEST_NOT_ALLOWED")
        self.n += 1
        request = {**request, "@extra": self.n}
        self.td.td_send(self.client, json.dumps(request).encode())
        return self.n

    def receive(self, timeout):
        raw = self.td.td_receive(timeout)
        return json.loads(raw) if raw else None


def params(api_id, api_hash, key):
    os.makedirs(STATE, mode=0o700, exist_ok=True)
    return {"@type": "setTdlibParameters", "use_test_dc": False, "database_directory": STATE, "files_directory": os.path.join(STATE, "files"),
            "database_encryption_key": key, "use_file_database": False, "use_chat_info_database": False, "use_message_database": False,
            "use_secret_chats": False, "api_id": api_id, "api_hash": api_hash, "system_language_code": "en", "device_model": "LIFE OS attention (Oracle)",
            "system_version": "", "application_version": "1.0"}


class Session:
    """Chat state folded from TDLib updates (no persistence)."""

    def __init__(self, td):
        self.td, self.chats, self.users, self.scopes, self.replies = td, {}, {}, {}, {}
        self.auth, self.connection, self.me = None, None, None

    def pump(self, until, deadline):
        while time.monotonic() < deadline:
            event = self.td.receive(min(1.0, max(0.0, deadline - time.monotonic())))
            if event is not None:
                self.fold(event)
            if until():
                return True
        return False

    def call(self, request, deadline):
        n = self.td.send(request)
        if not self.pump(lambda: n in self.replies, deadline):
            raise Refused("TIMEOUT")
        return self.replies.pop(n)

    def fold(self, e):
        kind = e.get("@type")
        if "@extra" in e:
            self.replies[e["@extra"]] = e
        elif kind == "updateAuthorizationState":
            self.auth = e["authorization_state"]["@type"]
        elif kind == "updateConnectionState":
            self.connection = e["state"]["@type"]
        elif kind == "updateNewChat":
            c = e["chat"]
            self.chats[c["id"]] = {k: c.get(k) for k in ("id", "type", "positions", "unread_count", "is_marked_as_unread", "notification_settings", "last_message")}
        elif kind == "updateUser":
            u = e["user"]
            self.users[u["id"]] = u
        elif kind in ("updateChatLastMessage", "updateChatPosition", "updateChatReadInbox", "updateChatIsMarkedAsUnread", "updateChatNotificationSettings"):
            c = self.chats.setdefault(e["chat_id"], {"id": e["chat_id"], "positions": []})
            if kind == "updateChatLastMessage":
                c["last_message"], c["positions"] = e.get("last_message"), e.get("positions") or c.get("positions") or []
            elif kind == "updateChatPosition":
                pos = e["position"]
                c["positions"] = [p for p in c.get("positions") or [] if p["list"]["@type"] != pos["list"]["@type"]] + ([pos] if pos.get("order") not in (0, "0") else [])
            elif kind == "updateChatReadInbox":
                c["unread_count"] = e["unread_count"]
            elif kind == "updateChatIsMarkedAsUnread":
                c["is_marked_as_unread"] = e["is_marked_as_unread"]
            else:
                c["notification_settings"] = e["notification_settings"]

    def start(self, api_id, api_hash, key, deadline):
        if not self.pump(lambda: self.auth == "authorizationStateWaitTdlibParameters", deadline):
            raise Refused("TDLIB_NOT_STARTED")
        reply = self.call(params(api_id, api_hash, key), deadline)
        if reply.get("@type") == "error":
            raise Refused("DATABASE_UNAVAILABLE")                         # wrong key, or locked by a running login
        self.pump(lambda: self.auth not in (None, "authorizationStateWaitTdlibParameters"), deadline)

    def close(self, deadline):
        try:
            self.td.send({"@type": "close"})
            self.pump(lambda: self.auth == "authorizationStateClosed", deadline)
        except Refused:
            pass


def in_main(chat):
    return any(p["list"]["@type"] == "chatListMain" and str(p.get("order")) != "0" for p in chat.get("positions") or [])


def muted(chat, scopes):
    ns = chat.get("notification_settings") or {}
    if not ns.get("use_default_mute_for", True):
        return ns.get("mute_for", 0) > 0
    return scopes.get(scope_of(chat), 0) > 0


def scope_of(chat):
    t = (chat.get("type") or {}).get("@type")
    if t == "chatTypeSupergroup":
        return "notificationSettingsScopeChannelChats" if chat["type"].get("is_channel") else "notificationSettingsScopeGroupChats"
    return SCOPES.get(t)


def counts(chats, scopes):
    main = [c for c in chats.values() if in_main(c)]
    unread = lambda c: c.get("unread_count") or 0
    loud = [c for c in main if not muted(c, scopes)]
    return {"chats_main": len(main), "unread_messages": sum(unread(c) for c in main), "unread_messages_unmuted": sum(unread(c) for c in loud),
            "unread_chats": sum(1 for c in main if unread(c) or c.get("is_marked_as_unread")),
            "unread_chats_unmuted": sum(1 for c in loud if unread(c) or c.get("is_marked_as_unread"))}


def person(message, me, users=None):
    """A message from another person (not me, a bot, a channel post or Telegram's service account): its user id, else None."""
    sender = message.get("sender_id") or {}
    if message.get("is_outgoing") or sender.get("@type") != "messageSenderUser" or sender.get("user_id") in (me, TELEGRAM_SERVICE):
        return None
    if (((users or {}).get(sender["user_id"]) or {}).get("type") or {}).get("@type") == "userTypeBot":
        return None
    return sender["user_id"]


def latest_incoming(s, deadline):
    """(user id, unix date) of the newest message from another person across Main-list chats that are not channels. A chat whose newest message is
    outgoing (or a channel post) may still hold a newer incoming message than the other chats, so its recent history is checked, newest first."""
    candidates = sorted((c for c in s.chats.values() if in_main(c) and c.get("last_message") and not (c.get("type") or {}).get("is_channel")),
                        key=lambda c: c["last_message"]["date"], reverse=True)
    best, scanned = None, 0
    for chat in candidates:
        if best and chat["last_message"]["date"] <= best[1]:
            break
        found = None
        if person(chat["last_message"], s.me, s.users):
            found = (person(chat["last_message"], s.me, s.users), chat["last_message"]["date"])
        elif scanned < MAX_HISTORY:
            scanned += 1
            page = s.call({"@type": "getChatHistory", "chat_id": chat["id"], "from_message_id": 0, "offset": 0, "limit": 20, "only_local": False}, deadline)
            for m in page.get("messages") or []:
                if person(m, s.me, s.users):
                    found = (person(m, s.me, s.users), m["date"])
                    break
        if found and (best is None or found[1] > best[1]):
            best = found
    return best


def display_name(s, user_id, deadline):
    user = s.users.get(user_id) or s.call({"@type": "getUser", "user_id": user_id}, deadline)
    name = " ".join(" ".join(filter(None, (user.get("first_name"), user.get("last_name")))).split())
    return name[:MAX_NAME] or "Unknown contact"


def observe(budget=40.0, lib=LIB, creds=secrets):
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    out = {"channel": "telegram", "scope": "personal account, Main chat list (Archive excluded)", "observed_at": now, "health": "UNAVAILABLE",
           "auth": None, "error": None, "chats_main": None, "unread_messages": None, "unread_messages_unmuted": None, "unread_chats": None,
           "unread_chats_unmuted": None, "latest_state": "UNAVAILABLE", "latest_name": None, "latest_at": None}
    deadline = time.monotonic() + budget
    try:
        key = db_key()
        api_id, api_hash = creds()
        s = Session(Td(lib=lib))
    except Refused as e:
        return {**out, "error": str(e)}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {**out, "error": "ADAPTER_UNAVAILABLE"}
    try:
        s.start(api_id, api_hash, key, deadline)
        if s.auth != "authorizationStateReady":
            return {**out, "auth": "NOT_SIGNED_IN" if s.auth and "Wait" in s.auth else "CLOSED"}
        out["auth"] = "READY"
        if not s.pump(lambda: s.connection == "connectionStateReady", deadline):          # connected AND caught up on missed updates
            raise Refused("NOT_CONNECTED")
        s.me = s.call({"@type": "getMe"}, deadline).get("id")
        for scope in ("notificationSettingsScopePrivateChats", "notificationSettingsScopeGroupChats", "notificationSettingsScopeChannelChats"):
            s.scopes[scope] = s.call({"@type": "getScopeNotificationSettings", "scope": {"@type": scope}}, deadline).get("mute_for", 0)
        while True:                                                                         # TDLib answers 404 once the whole Main list is known
            reply = s.call({"@type": "loadChats", "chat_list": {"@type": "chatListMain"}, "limit": 100}, deadline)
            if reply.get("@type") == "error":
                if reply.get("code") != 404:
                    raise Refused("CHAT_LIST_INCOMPLETE")
                break
        found = counts(s.chats, s.scopes)
        best = latest_incoming(s, deadline)
        name = display_name(s, best[0], deadline) if best else None
        if s.connection != "connectionStateReady":
            raise Refused("NOT_CONNECTED")
        return {**out, **found, "health": "CURRENT", "latest_state": "PRESENT" if best else "NONE",
                "latest_name": (name or "Unknown contact") if best else None,
                "latest_at": datetime.fromtimestamp(best[1], timezone.utc).isoformat().replace("+00:00", "Z") if best else None}
    except Refused as e:
        return {**out, "health": "DEGRADED" if out["auth"] == "READY" else "UNAVAILABLE", "error": str(e)}
    finally:
        s.close(time.monotonic() + 5)


def login():
    """Jim's one-time sign-in. Prompts go to the terminal; nothing typed is printed or written by this file."""
    api_id, api_hash = secrets()
    s = Session(Td(login=True))
    deadline = time.monotonic() + 600
    s.start(api_id, api_hash, db_key(create=True), deadline)
    while s.auth != "authorizationStateReady":
        if s.auth == "authorizationStateWaitPhoneNumber":
            reply = s.call({"@type": "setAuthenticationPhoneNumber", "phone_number": input("Telegram phone number (international format): ").strip()}, deadline)
        elif s.auth == "authorizationStateWaitCode":
            reply = s.call({"@type": "checkAuthenticationCode", "code": input("Login code Telegram just sent you: ").strip()}, deadline)
        elif s.auth == "authorizationStateWaitPassword":
            reply = s.call({"@type": "checkAuthenticationPassword", "password": getpass.getpass("Two-step verification password: ")}, deadline)
        else:
            print("Stopped: Telegram asked for a step this tool does not perform (%s)." % (s.auth or "unknown"))
            s.close(time.monotonic() + 5)
            return 1
        if reply.get("@type") == "error":
            print("Telegram refused that entry; try again.")
        s.pump(lambda: s.auth not in (None,), time.monotonic() + 5)
    print("Signed in. This Oracle session is now listed in Telegram > Settings > Devices as 'LIFE OS attention (Oracle)'.")
    s.close(time.monotonic() + 10)
    return 0


def main(argv):
    if argv == ["attention"]:
        print(json.dumps(observe(), sort_keys=True))
        return 0
    if argv == ["login"] and sys.stdin.isatty():
        return login()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

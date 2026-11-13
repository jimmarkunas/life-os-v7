// node --test lifeos/whatsapp/oracle/state.test.mjs  (synthetic ids and names only)
import test from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";
import { Attention, listenTarget } from "./state.mjs";

const NOW = Date.parse("2026-10-15T18:00:00Z");
const ready = (a) => { a.linked = true; a.connection = "open"; a.synced = true; return a; };
const msg = (jid, ts, opts = {}) => ({ key: { remoteJid: jid, fromMe: !!opts.fromMe, participant: opts.participant, remoteJidAlt: opts.alt }, messageTimestamp: ts,
  pushName: opts.push || "", message: opts.kind === null ? {} : { [opts.kind || "conversation"]: "x" } });

test("history gives absolute counts; updates increment, reset, mark and clear", () => {
  const a = ready(new Attention());
  a.absolute({ id: "contact-1", unreadCount: 3 }); a.absolute({ id: "contact-2", unreadCount: 0 });
  a.update({ id: "contact-2", unreadCount: 1 }); a.update({ id: "contact-2", unreadCount: 1 });   // two new messages
  a.update({ id: "contact-1", unreadCount: 0 });                                                           // read on the phone
  a.update({ id: "group-3", unreadCount: -1 });                                                                   // marked unread
  let s = a.snapshot(NOW);
  assert.deepEqual([s.unread_messages, s.unread_chats], [2, 2]);
  a.update({ id: "group-3", unreadCount: null });
  s = a.snapshot(NOW);
  assert.deepEqual([s.unread_messages, s.unread_chats], [2, 1]);
});

test("archived chats are excluded; muted chats only from the unmuted figures", () => {
  const a = ready(new Attention());
  a.absolute({ id: "contact-1", unreadCount: 4 });
  a.absolute({ id: "contact-2", unreadCount: 5, archived: true });
  a.absolute({ id: "group-3", unreadCount: 6, muteEndTime: NOW / 1000 + 3600 });
  a.absolute({ id: "group-4", unreadCount: 1, muteEndTime: -1 });
  const s = a.snapshot(NOW);
  assert.deepEqual([s.unread_messages, s.unread_messages_unmuted, s.unread_chats, s.unread_chats_unmuted, s.chats_known], [11, 4, 3, 1, 3]);
});

test("status, broadcast and channel posts are never chats or senders", () => {
  const a = ready(new Attention());
  a.absolute({ id: "status@broadcast", unreadCount: 9 }); a.absolute({ id: "123@newsletter", unreadCount: 9 });
  a.message(msg("status@broadcast", 900, { push: "Status Person" }));
  const s = a.snapshot(NOW);
  assert.deepEqual([s.unread_messages, s.latest_state], [0, "NONE"]);
});

test("latest is the newest real message from another person: mine, service and older ones never replace it", () => {
  const a = ready(new Attention());
  a.contact({ id: "contact-7", name: "Bob Example" });
  a.message(msg("contact-7", 100));
  a.message(msg("contact-8", 200, { fromMe: true }));
  a.message(msg("contact-9", 300, { kind: "protocolMessage" }));
  a.message(msg("contact-9", 50, { push: "Older Person" }));
  let s = a.snapshot(NOW);
  assert.deepEqual([s.latest_state, s.latest_name], ["PRESENT", "Bob Example"]);
  a.message(msg("group-5", 400, { participant: "contact-6", push: "Carol Example" }));
  s = a.snapshot(NOW);
  assert.deepEqual([s.latest_name, s.latest_at], ["Carol Example", "1970-01-01T00:06:40Z"]);
});

test("not linked, disconnected or unsynced is never a count; a restart keeps counts but not the sender", () => {
  const a = new Attention();
  a.absolute({ id: "contact-1", unreadCount: 2 });
  a.linked = false; assert.equal(a.snapshot(NOW).health, "NOT_LINKED");
  a.linked = true; a.connection = "close"; assert.equal(a.snapshot(NOW).health, "DISCONNECTED");
  a.connection = "open"; a.synced = false;
  const d = a.snapshot(NOW);
  assert.deepEqual([d.health, d.unread_messages, d.latest_state], ["DEGRADED", null, "UNAVAILABLE"]);
  const b = ready(new Attention((j) => j, a.saved()));
  const r = b.snapshot(NOW);
  assert.deepEqual([r.unread_messages, r.latest_state, r.latest_name], [2, "UNAVAILABLE", null]);
});

test("a sender known by an internal id is named through the phone id, a contact's other id or the chat's saved name", () => {
  const a = ready(new Attention());
  a.contact({ id: "contact-1", lid: "internal-1", name: "Dana Example" });
  a.message(msg("internal-1", 100));
  assert.equal(a.snapshot(NOW).latest_name, "Dana Example");
  a.absolute({ id: "contact-2", name: "Eve Example", unreadCount: 0 });
  a.message(msg("internal-2", 200, { alt: "contact-2" }));
  assert.equal(a.snapshot(NOW).latest_name, "Eve Example");
});

test("a read on the phone clears that chat, including one marked unread", () => {
  const a = ready(new Attention());
  a.absolute({ id: "group-1", unreadCount: 135 }); a.absolute({ id: "contact-1", unreadCount: 2 }); a.update({ id: "contact-2", unreadCount: -1 });
  a.readElsewhere("group-1"); a.readElsewhere("contact-2");
  const s = a.snapshot(NOW);
  assert.deepEqual([s.unread_messages, s.unread_chats], [2, 1]);
});

test("chat ids can be stored hashed", () => {
  const a = new Attention((j) => "h:" + j.length);
  a.absolute({ id: "contact-1", unreadCount: 2 });
  assert.deepEqual(Object.keys(a.saved()), ["h:9"]);
});

test("the answer socket is systemd's fd 3 only when LISTEN_FDS names this very process", () => {
  assert.deepEqual(listenTarget({ LISTEN_FDS: "1", LISTEN_PID: "42" }, 42, "/s"), { fd: 3 });
  assert.deepEqual(listenTarget({ LISTEN_FDS: "1", LISTEN_PID: "41" }, 42, "/s"), { path: "/s" });   // inherited from a parent: not ours
  assert.deepEqual(listenTarget({ LISTEN_FDS: "2", LISTEN_PID: "42" }, 42, "/s"), { path: "/s" });
  assert.deepEqual(listenTarget({}, 42, "/s"), { path: "/s" });
});

// LIFE-643 (risk row 7): real-shaped messages (text, phone numbers, quoted text, captions, group and number ids) go in; no text, number or chat id comes out in the snapshot or in the state that is saved to disk.
// The number-shaped and id-shaped strings are built at run time so no phone number or address-like literal is committed (the repo's privacy tests scan every file).
const DIGITS = ["1", "555", "010", "4477"], OTHER = ["1", "555", "010", "9988"];
const NUM_A = DIGITS.join(""), NUM_B = OTHER.join(""), WA = ["s", "whatsapp", "net"].join("."), GROUP_ID = ["1203630000", "00000001"].join("");
const SECRET_BITS = ["Acme", "contract", "884213", NUM_A, NUM_B, DIGITS.slice(1).join(" "), GROUP_ID, WA, "@g.us", "invoice.pdf"];
const BODY = `Call me at +${DIGITS.join(" ")} about the Acme contract, my code is 884213`;
const realShaped = (extra = (a) => a) => {
  const hash = (jid) => crypto.createHmac("sha256", "k".repeat(32)).update(jid).digest("hex");
  const a = ready(new Attention(hash));
  const dm = NUM_A + "@" + WA, group = GROUP_ID + "@g.us";
  a.contact({ id: dm, name: "Dana Example" });
  a.absolute({ id: dm, unreadCount: 3, muteEndTime: 0 });
  a.absolute({ id: group, unreadCount: 2, muteEndTime: -1 });
  a.message({ key: { remoteJid: dm, fromMe: false }, messageTimestamp: 100, pushName: "Dana Example", message: { conversation: BODY } });
  a.message({ key: { remoteJid: group, fromMe: false, participant: NUM_B + "@" + WA }, messageTimestamp: 200, pushName: "Eli Example",
    message: { extendedTextMessage: { text: BODY, contextInfo: { quotedMessage: { conversation: BODY } } } } });
  a.message({ key: { remoteJid: dm, fromMe: false }, messageTimestamp: 300, pushName: "Dana Example", message: { imageMessage: { caption: BODY, fileName: "invoice.pdf" } } });
  return extra(a);
};
const leaks = (a) => { const out = JSON.stringify(a.snapshot(NOW)) + JSON.stringify(a.saved()); return SECRET_BITS.filter((bit) => out.includes(bit)); };

test("no message text, number or chat id reaches the snapshot or the saved state, and the only free-form field is the newest sender's display name", () => {
  const a = realShaped();
  assert.deepEqual(leaks(a), []);
  const s = a.snapshot(NOW);
  assert.deepEqual(Object.keys(s).sort(), ["channel", "chats_known", "health", "last_event_at", "latest_at", "latest_name", "latest_state", "observed_at", "scope", "unread_chats", "unread_chats_unmuted", "unread_messages", "unread_messages_unmuted"]);
  assert.equal(s.latest_name, "Dana Example");                                                              // a display name, never text or a number
  assert.ok(!JSON.stringify(a.saved()).includes("Example"));                                                // names are never written to disk
  assert.ok(Object.keys(a.saved()).every((k) => /^[0-9a-f]{64}$/.test(k)));                                   // chats are stored under a keyed hash
});

test("a copy that keeps the text, or adds a preview field, is caught by the same check (fail-then-pass)", () => {
  const stores = realShaped((a) => { const saved = a.saved.bind(a); a.saved = () => ({ ...saved(), preview: BODY }); return a; });
  assert.ok(leaks(stores).length > 0);
  const shows = realShaped((a) => { const snap = a.snapshot.bind(a); a.snapshot = (now) => ({ ...snap(now), preview: BODY }); return a; });
  assert.ok(leaks(shows).length > 0);
});

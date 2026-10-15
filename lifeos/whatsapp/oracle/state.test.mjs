// node --test lifeos/whatsapp/oracle/state.test.mjs  (synthetic ids and names only)
import test from "node:test";
import assert from "node:assert/strict";
import { Attention } from "./state.mjs";

const NOW = Date.parse("2026-10-15T18:00:00Z");
const ready = (a) => { a.linked = true; a.connection = "open"; a.synced = true; return a; };
const msg = (jid, ts, opts = {}) => ({ key: { remoteJid: jid, fromMe: !!opts.fromMe, participant: opts.participant }, messageTimestamp: ts,
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

test("chat ids can be stored hashed", () => {
  const a = new Attention((j) => "h:" + j.length);
  a.absolute({ id: "contact-1", unreadCount: 2 });
  assert.deepEqual(Object.keys(a.saved()), ["h:9"]);
});

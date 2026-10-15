// The pure attention state for attention.mjs (no network, no disk): Baileys' unread semantics folded per chat, and the snapshot it answers.
const MAX_NAME = 80;
const PERSON_KINDS = new Set(["conversation", "extendedTextMessage", "imageMessage", "videoMessage", "audioMessage", "documentMessage",
  "documentWithCaptionMessage", "stickerMessage", "contactMessage", "contactsArrayMessage", "locationMessage", "liveLocationMessage",
  "pollCreationMessage", "pollCreationMessageV3", "ptvMessage", "viewOnceMessage", "viewOnceMessageV2", "eventMessage"]);

export const isOwnOrService = (jid) => !jid || jid === "status@broadcast" || jid.endsWith("@broadcast") || jid.endsWith("@newsletter");

export class Attention {
  constructor(hash = (jid) => jid, saved = null) {
    this.hash = hash;
    this.chats = new Map(Object.entries(saved || {}));   // hash(jid) -> {unread, marked, archived, muteEnd}
    this.restored = saved !== null;  // counts came from disk: current once connected; the latest sender is unknown until a new message
    this.dirty = false;
    this.names = new Map();          // jid -> saved contact name or push name (display only)
    this.latest = null;              // {sender, ts, name}
    this.synced = false;
    this.connection = "starting";
    this.linked = null;
    this.lastEvent = null;
  }

  chat(jid) {
    const k = this.hash(jid);
    if (!this.chats.has(k)) this.chats.set(k, { unread: 0, marked: false, archived: false, muteEnd: 0 });
    this.dirty = true;
    return this.chats.get(k);
  }

  saved() {
    return Object.fromEntries(this.chats);
  }

  absolute(c) {                                   // history sync / upsert: absolute values
    if (isOwnOrService(c.id)) return;
    if (c.name && !c.id.endsWith("@g.us")) this.contact({ id: c.id, name: c.name });   // a one-to-one chat carries the saved contact name
    const s = this.chat(c.id);
    if (typeof c.unreadCount === "number") { s.unread = Math.max(c.unreadCount, 0); s.marked = c.unreadCount < 0; }
    this.flags(s, c);
  }

  update(u) {                                     // chats.update: Baileys' increment/reset semantics
    if (isOwnOrService(u.id)) return;
    const s = this.chat(u.id);
    if (u.unreadCount === null) s.marked = false;
    else if (typeof u.unreadCount === "number") {
      if (u.unreadCount > 0) s.unread += u.unreadCount;
      else if (u.unreadCount === 0) { s.unread = 0; s.marked = false; }
      else s.marked = true;
    }
    this.flags(s, u);
  }

  readElsewhere(jid) {                            // a "read by you on another device" receipt: opening a chat reads all of it
    if (isOwnOrService(jid)) return;
    const s = this.chat(jid);
    s.unread = 0; s.marked = false;
  }

  flags(s, c) {
    if (typeof c.archived === "boolean") s.archived = c.archived;
    if (c.muteEndTime !== undefined) s.muteEnd = Number(c.muteEndTime || 0);
  }

  contact(c) {                                    // a contact may be known by its phone id, its LID or both: file the name under each
    const name = (c.name || c.verifiedName || c.notify || "").replace(/\s+/g, " ").trim();
    if (!name) return;
    for (const id of [c.id, c.lid, c.phoneNumber]) if (id) this.names.set(id, name.slice(0, MAX_NAME));
  }

  message(m) {                                    // newest message from another person, by the sender's own timestamp
    const key = m.key || {};
    if (key.fromMe || isOwnOrService(key.remoteJid)) return;
    const kinds = Object.keys(m.message || {});
    if (!kinds.some((k) => PERSON_KINDS.has(k))) return;
    const ts = Number(m.messageTimestamp || 0);
    if (!ts || (this.latest && ts <= this.latest.ts)) return;
    const ids = [key.participantAlt, key.participant, key.remoteJidAlt, key.remoteJid].filter(Boolean);   // newer WhatsApp: LID and phone id
    const push = (m.pushName || "").replace(/\s+/g, " ").trim().slice(0, MAX_NAME);
    this.latest = { ids: key.participant ? ids.filter((i) => !i.endsWith("@g.us")) : ids, ts, push };
  }

  snapshot(now = Date.now()) {
    const muted = (s) => s.muteEnd === -1 || s.muteEnd * 1000 > now;
    const open = [...this.chats.values()].filter((s) => !s.archived);
    const unreadChat = (s) => s.unread > 0 || s.marked;
    const ready = this.linked === true && this.connection === "open" && this.synced;
    const health = this.linked === false ? "NOT_LINKED" : this.connection !== "open" ? "DISCONNECTED" : this.synced ? "CURRENT" : "DEGRADED";
    const out = { channel: "whatsapp", scope: "personal account, chats not archived", health, observed_at: new Date(now).toISOString().replace(/\.\d+Z$/, "Z"),
      last_event_at: this.lastEvent, chats_known: null, unread_messages: null, unread_messages_unmuted: null, unread_chats: null, unread_chats_unmuted: null,
      latest_state: "UNAVAILABLE", latest_name: null, latest_at: null };
    if (!ready) return out;
    const loud = open.filter((s) => !muted(s));
    Object.assign(out, { chats_known: open.length, unread_messages: open.reduce((n, s) => n + s.unread, 0), unread_messages_unmuted: loud.reduce((n, s) => n + s.unread, 0),
      unread_chats: open.filter(unreadChat).length, unread_chats_unmuted: loud.filter(unreadChat).length });
    if (!this.latest) out.latest_state = this.restored ? "UNAVAILABLE" : "NONE";
    else Object.assign(out, { latest_state: "PRESENT", latest_name: this.latest.ids.map((i) => this.names.get(i)).find(Boolean) || this.latest.push || "Unknown contact",
      latest_at: new Date(this.latest.ts * 1000).toISOString().replace(/\.\d+Z$/, "Z") });
    return out;
  }
}


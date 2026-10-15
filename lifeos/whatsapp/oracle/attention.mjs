// COMM-1.3 (LIFE-30, Jim's approval Oct 15 2026, Jira 10215): Jim's personal WhatsApp attention, as ONE dedicated linked device on Oracle.
//
// Installed as ~/.local/lib/lifeos-whatsapp/attention.mjs next to its pinned Baileys (package-lock.json). Two forms:
//   link    Jim's one-time pairing: prints an 8-character code that Jim types on his phone (Linked devices > Link with phone number instead).
//   serve   the always-on user service: keeps, IN MEMORY ONLY, each chat's unread count, archive/mute state and the newest message from another
//           person, from WhatsApp's own linked-device sync; answers one JSON line on a 0600 Unix socket for the private comms endpoint.
//
// Attention only. It never sends, reacts, marks read or sets presence (markOnlineOnConnect is OFF, or Jim's phone would stop notifying), never
// downloads media and never stores or prints message text, numbers or chat names: the only identity it can answer is the latest sender's
// display name, which the endpoint scrambles before it leaves Oracle. Unread rules are Baileys' own: on a chat update a positive unreadCount is
// an increment (a new message), 0 is "read on another device", -1 is "marked unread", null clears that mark; the history sync after linking
// gives absolute counts. Not linked, logged out, disconnected or not yet synced is reported as such, never as zero.
//
// WhatsApp sends a linked device the per-chat counts ONCE, at linking; afterwards only changes. So the per-chat counts are saved (0600 file, keyed
// by an HMAC of the chat id under an Oracle-only key: no number, chat id, name or text on disk) and reloaded on restart; offline changes then arrive
// as ordinary sync. The latest sender's name is never written to disk: after a restart it is UNAVAILABLE until the next real incoming message.
import makeWASocket, { useMultiFileAuthState, DisconnectReason, Browsers } from "baileys";
import crypto from "node:crypto";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";
import pino from "pino";

const STATE = path.join(os.homedir(), ".local/state/lifeos-whatsapp");
const AUTH = path.join(STATE, "auth");
const SOCKET = path.join(STATE, "attention.sock");
const SAVED = path.join(STATE, "chats.json");
const HASH_KEY = path.join(os.homedir(), ".config/lifeos-whatsapp/hash-key");
import { Attention } from "./state.mjs";

const quiet = pino({ level: "silent" });

function socketFor(auth) {
  return makeWASocket({ auth, logger: quiet, browser: Browsers.ubuntu("LIFE OS attention"), markOnlineOnConnect: false, syncFullHistory: false,
    generateHighQualityLinkPreview: false, emitOwnEvents: false, getMessage: async () => undefined });
}

function serveSnapshots(att) {
  fs.mkdirSync(STATE, { recursive: true, mode: 0o700 });
  try { fs.unlinkSync(SOCKET); } catch { /* none yet */ }
  const server = net.createServer((conn) => { conn.end(JSON.stringify(att.snapshot()) + "\n"); });
  server.listen(SOCKET, () => fs.chmodSync(SOCKET, 0o600));
}

function hasher() {
  fs.mkdirSync(path.dirname(HASH_KEY), { recursive: true, mode: 0o700 });
  if (!fs.existsSync(HASH_KEY)) fs.writeFileSync(HASH_KEY, crypto.randomBytes(32).toString("hex"), { mode: 0o600, flag: "wx" });
  const key = Buffer.from(fs.readFileSync(HASH_KEY, "utf8").trim(), "hex");
  return (jid) => crypto.createHmac("sha256", key).update(jid).digest("hex").slice(0, 32);
}

function load() {
  try { return JSON.parse(fs.readFileSync(SAVED, "utf8")).chats || null; } catch { return null; }
}

function persist(att) {
  if (!att.dirty) return;
  const tmp = SAVED + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify({ version: 1, chats: att.saved() }), { mode: 0o600 });
  fs.renameSync(tmp, SAVED);
  att.dirty = false;
}

function wire(sock, att, saveCreds) {
  sock.ev.on("creds.update", saveCreds);
  const touch = () => { att.lastEvent = new Date().toISOString().replace(/\.\d+Z$/, "Z"); };
  sock.ev.on("messaging-history.set", ({ chats, contacts, messages, isLatest }) => {
    (contacts || []).forEach((c) => att.contact(c)); (chats || []).forEach((c) => att.absolute(c)); (messages || []).forEach((m) => att.message(m));
    if (isLatest) att.synced = true; touch();
  });
  sock.ev.on("chats.upsert", (cs) => { cs.forEach((c) => att.absolute(c)); touch(); });
  sock.ev.on("chats.update", (us) => { us.forEach((u) => att.update(u)); touch(); });
  sock.ev.on("contacts.upsert", (cs) => cs.forEach((c) => att.contact(c)));
  sock.ev.on("contacts.update", (cs) => cs.forEach((c) => att.contact(c)));
  sock.ev.on("messages.upsert", ({ messages, type }) => { if (type === "notify") messages.forEach((m) => att.message(m)); touch(); });
}

async function serve() {
  const att = new Attention(hasher(), load());
  serveSnapshots(att);
  setInterval(() => persist(att), 5000);
  let delay = 2000;
  const start = async () => {
    const { state, saveCreds } = await useMultiFileAuthState(AUTH);
    att.linked = Boolean(state.creds?.registered || state.creds?.me);
    if (!att.linked) return;                      // not linked: answer NOT_LINKED, never try to pair from the service
    const sock = socketFor(state);
    wire(sock, att, saveCreds);
    sock.ev.on("connection.update", ({ connection, lastDisconnect }) => {
      if (connection === "open") { att.connection = "open"; delay = 2000; if (att.restored) setTimeout(() => { att.synced = true; }, 30000); }   // offline catch-up first
      if (connection === "close") {
        att.connection = "close"; att.synced = false;
        const code = lastDisconnect?.error?.output?.statusCode;
        if (code === DisconnectReason.loggedOut) { att.linked = false; return; }      // unlinked from the phone: stay NOT_LINKED, no reconnect
        setTimeout(start, delay); delay = Math.min(delay * 2, 300000);
      }
    });
  };
  await start();
}

async function link() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH);
  if (state.creds?.registered) { console.log("Already linked."); return 0; }
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  const phone = (await new Promise((r) => rl.question("WhatsApp phone number with country code, digits only: ", r))).replace(/\D/g, "");
  rl.close();
  const att = new Attention(hasher(), null);
  return new Promise((resolve) => {
    let asked = false;
    const finish = (sock, code) => { persist(att); sock.end(undefined); resolve(code); };
    const open = () => {
      const sock = socketFor(state);
      wire(sock, att, saveCreds);
      sock.ev.on("connection.update", async ({ connection, lastDisconnect, qr }) => {
        if (qr && !asked && !state.creds.registered) {
          asked = true;
          const code = await sock.requestPairingCode(phone);
          console.log(`\nOn your phone: WhatsApp > Settings > Linked devices > Link a device > "Link with phone number instead", then enter:\n\n    ${code}\n`);
        }
        if (connection === "open") {
          console.log("Linked. Receiving WhatsApp's first sync (up to 3 minutes; keep this window open)...");
          const started = Date.now();
          const wait = setInterval(() => {
            if (att.synced || Date.now() - started > 180000) {
              clearInterval(wait);
              console.log(att.synced ? "First sync received. Done." : "First sync not confirmed; counts will show as unavailable until it is.");
              finish(sock, att.synced ? 0 : 3);
            }
          }, 2000);
        }
        if (connection === "close") {
          const code = lastDisconnect?.error?.output?.statusCode;
          if (code === DisconnectReason.restartRequired) open();          // WhatsApp restarts the connection once right after pairing
          else if (!state.creds.registered) { console.log("Pairing did not complete; run this again."); resolve(1); }
        }
      });
    };
    open();
  });
}

const mode = process.argv[2];
if (mode === "serve") await serve();
else if (mode === "link" && process.stdin.isTTY) process.exit(await link());
else if (mode !== undefined) process.exitCode = 2;

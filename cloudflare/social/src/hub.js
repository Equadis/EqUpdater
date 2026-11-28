// One SQLite-backed Durable Object holds everything: accounts, friendships, and every live WebSocket.
//
// Why one object: presence fan-out ("tell this player's friends") is then an in-process loop over sockets, with
// no extra requests; the test environment is a few hundred players at most, far below one object's capacity
// (~1,000 requests per second). Why SQLite storage: accounts and friendships are rows, reads and writes are
// metered per row, and it is available on the Workers Free plan.
//
// Presence is NOT stored. It lives in each socket's serialized attachment, so it survives hibernation for free,
// costs no storage writes, and a socket that is gone is by definition offline. While every socket is idle the
// object hibernates: heartbeats ("ping") are answered by the runtime without waking it (setWebSocketAutoResponse).
// An alarm every ALARM_S seconds (only while sockets exist) drops sockets that have been silent past the TTL.
import { DurableObject } from "cloudflare:workers";
import {
  Limiter, MAX_BODY, MAX_FRIENDS, NAME_RE, bucket, cleanState, estimate, json, randomToken, sha256hex, shown,
  timingSafeEqual,
} from "./logic.js";

const DAY = 86400000;
const KEEP_DAYS = 35;

export class SocialHub extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.limiter = new Limiter();
    this.buckets = new Map();           // account id -> message token bucket (memory only)
    this.sessions = new Map();          // session hash -> {id, name} (memory only, short-lived)
    this.dirty = 0;
    this.c = this.fresh();
    this.dayHttp = 0;                   // today's request total, kept apart from the counters because those are flushed and reset
    this.cfg = {
      ttl: Number(env.PRESENCE_TTL_S || 100) * 1000,
      alarm: Number(env.ALARM_S || 45) * 1000,
      cap: Number(env.DAILY_REQUEST_CAP || 40000),
      accounts: Number(env.MAX_ACCOUNTS || 500),
      conns: Number(env.MAX_CONNECTIONS || 400),
      regPerIp: Number(env.REGISTER_PER_IP_HOUR || 20),
      recPerName: Number(env.RECOVER_PER_NAME_HOUR || 8),
      wsPerAccount: Number(env.WS_PER_ACCOUNT_10MIN || 40),
      friendAdds: Number(env.FRIEND_ADDS_HOUR || 120),
    };
    ctx.blockConcurrencyWhile(async () => {
      this.migrate();
      this.dayHttp = (this.sql.exec("SELECT http FROM daily WHERE day = ?", this.today()).toArray()[0] || {}).http || 0;
    });
    ctx.setWebSocketAutoResponse(new WebSocketRequestResponsePair("ping", "pong"));
  }

  fresh() { return { day: this.today(), http: 0, errors: 0, ws_connects: 0, ws_in: 0, ws_out: 0, alarms: 0, rows_read: 0, rows_written: 0, peak_conn: 0, peak_game: 0 }; }
  today() { return Math.floor(Date.now() / DAY); }

  migrate() {
    for (const s of [
      `CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, name TEXT NOT NULL, key TEXT NOT NULL UNIQUE,
         session_hash TEXT NOT NULL, recovery_hash TEXT NOT NULL, created INTEGER NOT NULL, last_day INTEGER NOT NULL)`,
      `CREATE INDEX IF NOT EXISTS accounts_session ON accounts(session_hash)`,
      `CREATE TABLE IF NOT EXISTS friends (owner TEXT NOT NULL, friend TEXT NOT NULL, favourite INTEGER NOT NULL DEFAULT 0,
         PRIMARY KEY (owner, friend))`,
      `CREATE INDEX IF NOT EXISTS friends_friend ON friends(friend)`,
      `CREATE TABLE IF NOT EXISTS daily (day INTEGER PRIMARY KEY, http INTEGER, errors INTEGER, ws_connects INTEGER,
         ws_in INTEGER, ws_out INTEGER, alarms INTEGER, rows_read INTEGER, rows_written INTEGER, peak_conn INTEGER, peak_game INTEGER)`,
    ]) this.sql.exec(s);
  }

  /** Run SQL, count the rows it touched (the unit of storage billing), return the rows. */
  q(query, ...b) {
    const cur = this.sql.exec(query, ...b);
    const rows = cur.toArray();
    this.c.rows_read += cur.rowsRead || 0;
    this.c.rows_written += cur.rowsWritten || 0;
    return rows;
  }

  // ── metrics (private, aggregate, no per-person history) ─────────────────────────────────────
  roll() {
    if (this.c.day !== this.today()) { this.flush(); this.c = this.fresh(); this.dayHttp = 0; }
  }

  flush() {
    const c = this.c;
    if (!this.dirty) return;
    this.sql.exec(
      `INSERT INTO daily VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET http=http+excluded.http,
         errors=errors+excluded.errors, ws_connects=ws_connects+excluded.ws_connects, ws_in=ws_in+excluded.ws_in,
         ws_out=ws_out+excluded.ws_out, alarms=alarms+excluded.alarms, rows_read=rows_read+excluded.rows_read,
         rows_written=rows_written+excluded.rows_written, peak_conn=MAX(peak_conn, excluded.peak_conn),
         peak_game=MAX(peak_game, excluded.peak_game)`,
      c.day, c.http, c.errors, c.ws_connects, c.ws_in, c.ws_out, c.alarms, c.rows_read, c.rows_written, c.peak_conn, c.peak_game);
    this.sql.exec("DELETE FROM daily WHERE day < ?", c.day - KEEP_DAYS);
    for (const k of ["http", "errors", "ws_connects", "ws_in", "ws_out", "alarms", "rows_read", "rows_written"]) c[k] = 0;
    this.dirty = 0;
  }

  count(k, n = 1) {
    this.roll();
    this.c[k] += n;
    this.dirty += n;
    if (k === "http") this.dayHttp += n;
    if (k === "http" && this.dirty >= 200) this.flush();
  }

  // ── HTTP ─────────────────────────────────────────────────────────────────────────────────────────
  async fetch(request) {
    this.roll();
    const url = new URL(request.url);
    const path = url.pathname, method = request.method;
    this.count("http");
    const res = await this.route(request, path, method).catch((e) => {
      this.count("errors");
      return json(500, { error: "server error" });
    });
    if (res.status >= 400) this.count("errors");
    return res;
  }

  async body(request) {
    const len = Number(request.headers.get("content-length") || 0);
    if (len > MAX_BODY) throw new Error("too large");
    const text = await request.text();
    if (text.length > MAX_BODY) throw new Error("too large");
    const obj = text ? JSON.parse(text) : {};
    if (obj === null || typeof obj !== "object" || Array.isArray(obj)) throw new Error("not an object");
    return obj;
  }

  paused() { return this.dayHttp >= this.cfg.cap; }

  async account(request) {
    const h = request.headers.get("authorization") || "";
    const token = h.startsWith("Bearer ") ? h.slice(7) : "";
    if (!token) return null;
    const hash = await sha256hex(token);
    let a = this.sessions.get(hash);
    if (!a) {
      const r = this.q("SELECT id, name, last_day FROM accounts WHERE session_hash = ?", hash)[0];
      if (!r) return null;
      a = { id: r.id, name: r.name };
      this.sessions.set(hash, a);
      if (this.sessions.size > 2000) this.sessions.clear();
      if (r.last_day !== this.today()) this.q("UPDATE accounts SET last_day = ? WHERE id = ?", this.today(), r.id);   // one write per account per day
    }
    return a;
  }

  async route(request, path, method) {
    const ip = request.headers.get("cf-connecting-ip") || "unknown";
    if (path === "/v1/admin/metrics" && method === "GET") return this.metrics(request);
    if (this.paused()) return json(503, { error: "The test service is paused for today." });
    if (method === "GET" && path === "/v1/info") return json(200, { protocol: 2, requires_key: !!this.env.TESTER_KEYS });
    let body = {};
    if (method === "POST" || method === "PUT") {
      try { body = await this.body(request); } catch { return json(400, { error: "bad request" }); }
    }
    if (method === "POST" && path === "/v1/register") return this.register(body, ip);
    if (method === "POST" && path === "/v1/recover") return this.recover(body);
    const me = await this.account(request);
    if (!me) return json(401, { error: "sign in again" });
    if (method === "GET" && path === "/v1/ws" && request.headers.get("upgrade") === "websocket") return this.websocket(me);
    if (method === "GET" && path === "/v1/me") return json(200, { username: me.name });
    if (method === "POST" && path === "/v1/logout") return this.logout(me, request);
    if (method === "POST" && path === "/v1/friends") return this.addFriend(me, body.username);
    if (path.startsWith("/v1/friends/")) {
      const target = decodeURIComponent(path.slice("/v1/friends/".length));
      if (method === "DELETE") return this.removeFriend(me, target);
      if (method === "PUT") return this.favourite(me, target, !!body.favourite);
    }
    return json(404, { error: "not found" });
  }

  async register(body, ip) {
    const name = body.username;
    if (typeof name !== "string" || !NAME_RE.test(name)) return json(400, { error: "A username is 3 to 20 letters, numbers, dashes or underscores." });
    if (!this.limiter.hit("reg:" + ip, this.cfg.regPerIp, 3600000)) return json(429, { error: "Too many sign-ups from here. Try again later." });
    if (this.env.TESTER_KEYS) {
      const ok = String(this.env.TESTER_KEYS).split(",").map((s) => s.trim()).filter(Boolean);
      const given = await sha256hex(String(body.key || ""));
      if (!ok.some((k) => timingSafeEqual(k, given))) return json(403, { error: "A tester key is needed to create an account." });
    }
    if (this.q("SELECT COUNT(*) AS n FROM accounts")[0].n >= this.cfg.accounts) return json(503, { error: "The test environment is full." });
    const key = name.toLowerCase();
    if (this.q("SELECT 1 FROM accounts WHERE key = ?", key).length) return json(409, { error: "That username is taken." });
    const token = randomToken(32), recovery = randomToken(24);
    this.q("INSERT INTO accounts VALUES (?,?,?,?,?,?,?)", crypto.randomUUID(), name, key, await sha256hex(token),
      await sha256hex(recovery), Date.now(), this.today());
    return json(200, { username: name, token, recovery });
  }

  async recover(body) {
    const name = body.username, recovery = body.recovery;
    if (typeof name !== "string" || !NAME_RE.test(name) || typeof recovery !== "string") return json(400, { error: "bad request" });
    if (!this.limiter.hit("rec:" + name.toLowerCase(), this.cfg.recPerName, 3600000)) return json(429, { error: "Too many attempts. Try again later." });
    const row = this.q("SELECT id, name, recovery_hash FROM accounts WHERE key = ?", name.toLowerCase())[0];
    if (!row || !timingSafeEqual(await sha256hex(recovery), row.recovery_hash)) return json(401, { error: "That recovery code does not match." });
    const token = randomToken(32);
    this.q("UPDATE accounts SET session_hash = ? WHERE id = ?", await sha256hex(token), row.id);
    this.sessions.clear();
    this.kick(row.id, "recovered elsewhere", 4001);                       // the old computer is signed out
    return json(200, { username: row.name, token });
  }

  async logout(me, request) {
    this.q("UPDATE accounts SET session_hash = ? WHERE id = ?", await sha256hex(randomToken(16)), me.id);
    this.sessions.clear();
    this.kick(me.id, "signed out", 1000);
    return json(204);
  }

  kick(accountId, reason, code) {
    for (const ws of this.ctx.getWebSockets(accountId)) {
      const att = ws.deserializeAttachment() || {};
      ws.serializeAttachment({ ...att, gone: true });
      this.send(ws, { t: "bye", reason });
      try { ws.close(code, reason); } catch { /* already closing */ }
    }
    this.broadcast(accountId);
  }

  // ── friends ─────────────────────────────────────────────────────────────────────────────────────
  addFriend(me, name) {
    if (typeof name !== "string" || !NAME_RE.test(name)) return json(400, { error: "That is not a valid username." });
    if (!this.limiter.hit("add:" + me.id, this.cfg.friendAdds, 3600000)) return json(429, { error: "Too many friend requests. Try again later." });
    const row = this.q("SELECT id, name FROM accounts WHERE key = ?", name.toLowerCase())[0];
    if (!row) return json(404, { error: "No user has that name." });
    if (row.id === me.id) return json(400, { error: "You cannot add yourself." });
    if (this.q("SELECT 1 FROM friends WHERE owner = ? AND friend = ?", me.id, row.id).length) return json(409, { error: "Already in your friends list." });
    if (this.q("SELECT COUNT(*) AS n FROM friends WHERE owner = ?", me.id)[0].n >= MAX_FRIENDS) return json(400, { error: "The friends list is full." });
    this.q("INSERT INTO friends(owner, friend) VALUES (?,?)", me.id, row.id);
    this.pushFriends(me.id);
    return json(200, { username: row.name });
  }

  favourite(me, name, value) {
    const row = this.q("SELECT id FROM accounts WHERE key = ?", String(name).toLowerCase())[0];
    const n = row ? this.q("UPDATE friends SET favourite = ? WHERE owner = ? AND friend = ? RETURNING 1", value ? 1 : 0, me.id, row.id).length : 0;
    if (n) this.pushFriends(me.id);
    return json(n ? 204 : 404);
  }

  removeFriend(me, name) {
    const row = this.q("SELECT id FROM accounts WHERE key = ?", String(name).toLowerCase())[0];
    const n = row ? this.q("DELETE FROM friends WHERE owner = ? AND friend = ? RETURNING 1", me.id, row.id).length : 0;
    if (n) this.pushFriends(me.id);
    return json(n ? 204 : 404);
  }

  // ── presence ─────────────────────────────────────────────────────────────────────────────────────
  live(accountId) {
    return this.ctx.getWebSockets(accountId).filter((w) => !(w.deserializeAttachment() || {}).gone);
  }

  presenceOf(accountId) {
    const w = this.live(accountId)[0];
    return shown(w ? w.deserializeAttachment() : null);
  }

  send(ws, obj) {
    try { ws.send(typeof obj === "string" ? obj : JSON.stringify(obj)); this.c.ws_out += 1; this.dirty += 1; return true; } catch { return false; }
  }

  friendsView(owner) {
    return this.q(`SELECT a.id, a.name, f.favourite FROM friends f JOIN accounts a ON a.id = f.friend WHERE f.owner = ? ORDER BY a.name`, owner)
      .map((r) => ({ username: r.name, favourite: !!r.favourite, ...this.presenceOf(r.id) }));
  }

  pushFriends(owner) {
    const socks = this.live(owner);
    if (!socks.length) return;
    const msg = JSON.stringify({ t: "friends", friends: this.friendsView(owner) });
    for (const w of socks) this.send(w, msg);
  }

  /** Tell exactly the accounts that have this one as a friend, and only if they are connected. */
  broadcast(accountId) {
    const name = (this.q("SELECT name FROM accounts WHERE id = ?", accountId)[0] || {}).name;
    if (!name) return;
    const msg = JSON.stringify({ t: "presence", u: name, ...this.presenceOf(accountId) });
    for (const r of this.q("SELECT owner FROM friends WHERE friend = ?", accountId)) {
      for (const w of this.live(r.owner)) this.send(w, msg);
    }
  }

  async websocket(me) {
    if (this.ctx.getWebSockets().length >= this.cfg.conns) return json(503, { error: "The test service is full right now." });
    if (!this.limiter.hit("ws:" + me.id, this.cfg.wsPerAccount, 600000)) return json(429, { error: "Too many reconnections. Slow down." }, { "retry-after": "30" });
    this.count("ws_connects");
    for (const old of this.live(me.id)) {                                  // one connection per account: the newest wins
      const att = old.deserializeAttachment() || {};
      old.serializeAttachment({ ...att, gone: true });
      this.send(old, { t: "bye", reason: "replaced" });
      try { old.close(4000, "replaced"); } catch { /* closing */ }
    }
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    this.ctx.acceptWebSocket(server, [me.id]);
    server.serializeAttachment({ a: me.id, n: me.name, st: "online", ac: "launcher", rm: "", t: Date.now() });
    this.c.peak_conn = Math.max(this.c.peak_conn, this.ctx.getWebSockets().length);
    this.dirty += 1;
    this.send(server, { t: "friends", friends: this.friendsView(me.id) });    // restored on every (re)connect
    this.broadcast(me.id);
    await this.ensureAlarm();
    return new Response(null, { status: 101, webSocket: client });
  }

  async webSocketMessage(ws, message) {
    const att = ws.deserializeAttachment() || {};
    if (att.gone) return;
    if (typeof message !== "string" || message.length > 512) { ws.close(1009, "message too large"); return; }
    this.roll();
    this.c.ws_in += 1;
    this.dirty += 1;
    let b = this.buckets.get(att.a);
    if (!b) { b = bucket(); this.buckets.set(att.a, b); }
    if (!b()) { this.send(ws, { t: "bye", reason: "too many messages" }); ws.close(1008, "too many messages"); return; }
    let msg;
    try { msg = JSON.parse(message); } catch { return; }
    if (!msg || typeof msg !== "object") return;
    if (msg.t === "state") {
      const s = cleanState(msg);
      if (s.st === att.st && s.ac === att.ac && s.rm === att.rm) return;
      ws.serializeAttachment({ ...att, ...s, t: Date.now() });
      const socks = this.ctx.getWebSockets();
      this.c.peak_game = Math.max(this.c.peak_game, socks.filter((w) => { const x = w.deserializeAttachment() || {}; return x.ac === "in_game" && x.st !== "invisible" && !x.gone; }).length);
      this.broadcast(att.a);
    } else if (msg.t === "sync") {
      this.pushFriends(att.a);
    }
  }

  async webSocketClose(ws) { this.dropped(ws); }
  async webSocketError(ws) { this.dropped(ws); }

  dropped(ws) {
    const att = ws.deserializeAttachment() || {};
    try { ws.close(1000, "bye"); } catch { /* already closed */ }
    if (att.gone || !att.a) return;                                         // replaced or kicked: the account is handled
    ws.serializeAttachment({ ...att, gone: true });
    if (!this.live(att.a).length) this.broadcast(att.a);                    // really offline now
  }

  // ── expiry ───────────────────────────────────────────────────────────────────────────────────────
  async ensureAlarm() {
    if ((await this.ctx.storage.getAlarm()) === null) await this.ctx.storage.setAlarm(Date.now() + this.cfg.alarm);
  }

  async alarm() {
    this.roll();
    this.count("alarms");
    const now = Date.now();
    let remaining = 0;
    for (const ws of this.ctx.getWebSockets()) {
      const att = ws.deserializeAttachment() || {};
      if (att.gone) continue;
      const auto = this.ctx.getWebSocketAutoResponseTimestamp(ws);
      const last = Math.max(att.t || 0, auto ? auto.getTime() : 0);
      if (now - last > this.cfg.ttl) {
        this.send(ws, { t: "bye", reason: "no heartbeat" });
        this.dropped(ws);
      } else remaining += 1;
    }
    this.flush();
    if (remaining > 0) await this.ctx.storage.setAlarm(now + this.cfg.alarm);   // no sockets: no alarm, the object can sleep
  }

  // ── private metrics ──────────────────────────────────────────────────────────────────────────────
  async metrics(request) {
    const h = request.headers.get("authorization") || "";
    const token = h.startsWith("Bearer ") ? h.slice(7) : "";
    const want = this.env.ADMIN_KEY_HASH;
    if (!want || !token || !timingSafeEqual(await sha256hex(token), want)) return json(401, { error: "no" });
    this.flush();
    const socks = this.ctx.getWebSockets().map((w) => w.deserializeAttachment() || {}).filter((a) => !a.gone);
    const day = this.today();
    const today = this.q("SELECT * FROM daily WHERE day = ?", day)[0] || {};
    const pending = this.c;
    const merged = { ...today };
    for (const k of ["http", "errors", "ws_connects", "ws_in", "ws_out", "alarms", "rows_read", "rows_written"]) merged[k] = (today[k] || 0) + (pending[k] || 0);
    merged.peak_conn = Math.max(today.peak_conn || 0, pending.peak_conn);
    return json(200, {
      accounts: this.q("SELECT COUNT(*) AS n FROM accounts")[0].n,
      daily_active: this.q("SELECT COUNT(*) AS n FROM accounts WHERE last_day = ?", day)[0].n,
      connected: socks.length,
      in_game: socks.filter((a) => a.ac === "in_game" && a.st !== "invisible").length,
      today: merged,
      estimated_usage_today: estimate(merged, merged.peak_conn),
      cap: { daily_requests: this.cfg.cap, used: this.dayHttp, paused: this.paused() },
      days: this.q("SELECT day, http, ws_connects, ws_in, peak_conn FROM daily ORDER BY day DESC LIMIT 14"),
    });
  }
}

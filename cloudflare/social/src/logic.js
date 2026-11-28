// Pure helpers for the social test backend (no Cloudflare APIs): testable with `node --test`.

export const NAME_RE = /^[A-Za-z0-9_-]{3,20}$/;
export const STATUSES = ["online", "away", "dnd", "invisible"];
export const ACTIVITIES = ["launcher", "in_game"];
export const REALMS = ["nzoth", "yshaarj", "cthun"];
export const MAX_BODY = 4096;
export const MAX_FRIENDS = 200;

export async function sha256hex(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function randomToken(bytes) {
  const raw = crypto.getRandomValues(new Uint8Array(bytes));
  let s = "";
  for (const b of raw) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

/** A player's validated state. Invisible keeps and sends nothing else. */
export function cleanState(msg) {
  const status = STATUSES.includes(msg.status) ? msg.status : "online";
  let activity = ACTIVITIES.includes(msg.activity) ? msg.activity : "launcher";
  let realm = REALMS.includes(msg.realm) && activity === "in_game" ? msg.realm : "";
  if (status === "invisible") { activity = "launcher"; realm = ""; }
  return { st: status, ac: activity, rm: realm };
}

/** How others see a connection's attachment. Invisible and absent both read as offline, with nothing else. */
export function shown(att) {
  if (!att || att.gone || att.st === "invisible") return { state: "offline", activity: "", realm: "" };
  return { state: att.st, activity: att.ac, realm: att.ac === "in_game" ? att.rm : "" };
}

/** Sliding-window counters kept in memory: hit(key, limit, windowMs) is true while under the limit. */
export class Limiter {
  constructor() { this.hits = new Map(); }
  hit(key, limit, windowMs, now = Date.now()) {
    const q = (this.hits.get(key) || []).filter((t) => now - t < windowMs);
    if (q.length >= limit) { this.hits.set(key, q); return false; }
    q.push(now);
    this.hits.set(key, q);
    if (this.hits.size > 5000) this.sweep(now, windowMs);
    return true;
  }
  sweep(now, windowMs) {
    for (const [k, q] of this.hits) if (!q.length || now - q[q.length - 1] > windowMs) this.hits.delete(k);
  }
}

/** A token bucket for one connection's messages. */
export function bucket(capacity = 20, perSecond = 2, start = Date.now()) {
  let tokens = capacity, last = start;
  return (now = Date.now()) => {
    tokens = Math.min(capacity, tokens + ((now - last) / 1000) * perSecond);
    last = now;
    if (tokens < 1) return false;
    tokens -= 1;
    return true;
  };
}

export function json(status, body, headers = {}) {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store", ...headers },
  });
}

/** Estimated billable usage from one day's counters (see docs/v3/SOCIAL_COSTS.md for the model and prices). */
export function estimate(c, peakConnections) {
  const wsInBilled = Math.ceil((c.ws_in || 0) / 20);                  // incoming WebSocket messages bill 20:1
  const pingsBilled = Math.ceil(((peakConnections || 0) * 1920) / 20); // heartbeats answered without waking the object, worst case
  return {
    worker_requests: (c.http || 0) + (c.ws_connects || 0),
    do_requests: (c.http || 0) + (c.ws_connects || 0) + (c.alarms || 0) + wsInBilled + pingsBilled,
    do_requests_free_per_day: 100000,
    rows_read: c.rows_read || 0,
    rows_written: c.rows_written || 0,
    duration_gb_s_upper_bound: 0.125 * 86400,                           // 128 MB awake all day: the most one object can use
    duration_gb_s_free_per_day: 13000,
  };
}

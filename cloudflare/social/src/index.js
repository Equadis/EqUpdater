// The edge Worker: cheap checks first, then everything goes to the one SocialHub object.
// Nothing here holds state or secrets. Requests that cannot possibly succeed are turned away here, before a
// Durable Object is woken (each wake is billed): wrong path or method, oversized bodies, missing credentials,
// and per-IP floods (the Workers Rate Limiting API).
import { SocialHub } from "./hub.js";
import { MAX_BODY, json } from "./logic.js";

export { SocialHub };

const PUBLIC = new Set(["/v1/info", "/v1/register", "/v1/recover", "/v1/admin/metrics"]);
const METHODS = new Set(["GET", "POST", "PUT", "DELETE"]);

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/v1/")) return json(404, { error: "not found" });
    if (!METHODS.has(request.method)) return json(405, { error: "method not allowed" });
    if (Number(request.headers.get("content-length") || 0) > MAX_BODY) return json(413, { error: "too large" });
    const isWs = (request.headers.get("upgrade") || "").toLowerCase() === "websocket";
    if (url.pathname === "/v1/ws" && !isWs) return json(426, { error: "a WebSocket connection is required" });
    if (!PUBLIC.has(url.pathname) && !(request.headers.get("authorization") || "").startsWith("Bearer ")) {
      return json(401, { error: "sign in again" });
    }
    const limiter = isWs ? env.WS_LIMIT : env.HTTP_LIMIT;
    if (limiter && env.EDGE_LIMITS_OFF !== "1") {            // (the test suite switches only this off, on a local copy)
      const { success } = await limiter.limit({ key: request.headers.get("cf-connecting-ip") || "unknown" });
      if (!success) return json(429, { error: "Too many requests. Slow down." }, { "retry-after": "30" });
    }
    const stub = env.HUB.get(env.HUB.idFromName("hub"));
    return stub.fetch(request);
  },
};

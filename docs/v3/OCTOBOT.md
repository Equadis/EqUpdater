# OctoBot integration assessment (Phase 6 input)

Source: https://github.com/Equadis/OctoBot (public), inspected 2026-11-24. Endpoints verified live with `curl`.

## Actual data sources

| Data | Endpoint | Producer / cadence | Headers | Notes |
|---|---|---|---|---|
| Population per realm + All | `https://octobot-population.eqvoice.workers.dev/api/latest` | Cloudflare Worker cron, **every 1 min**, stored in D1 | `Cache-Control: no-store`, CORS `*` | `{all,nzoth,yshaarj,cthun}` each `{timestamp, online, source}`. Realm counts from in-game `.server info`; `all` from official census. Failed fetch records nothing (never 0). |
| Status (online/offline/auth/tcp/latency) | `…/api/status/latest` | same Worker, 1 min | `no-store` | `{schema:1, source, sample:{timestamp, website, realms{…:"online"…}, tcp, auth, latency_ms, source_error}}`. Failed/blocked probe → **unknown**, and offline needs consecutive explicit offline readings. |
| Status health/history | `…/api/status/health`, `…/api/status/history?since=` | same | | `health` gives last-reading freshness; history paginated (`has_more`, `next_since`). Not needed for the launcher. |
| Static fallback | `https://equadis.github.io/OctoBot/live.json`, `status/latest.json` | GitHub Actions, **5 min best-effort** (`population.yml`, `status.yml`) | `max-age=600`, `ETag`, CORS `*` | Pages/CDN-served: effectively free, but up to ~15 min stale. |
| Realm list | `…/realms.json` | static | `max-age=600` | ids + display names (“N’Zoth (Normal)”, “Y’Shaarj (PvP)”, “C’Thun (Hardcore)”). Use for realm names; do not hardcode. |

The website itself polls `/api/latest` every 60 s and merges with the GitHub fallback — the launcher should mirror that semantics.

## Semantics to adopt (authoritative = OctoBot)
States: `online`, `offline`, `degraded` (website/auth/tcp disagree with realm label — derive exactly as `docs/status-dashboard.js` does; **to be copied from source in Phase 6, not guessed**), `unknown` (probe failed / `source_error`), plus launcher-side overlays **stale** (last sample older than 3× cadence) and **unavailable** (our request failed — shows last good value, never “offline”). Uptime/incident data: derive from `status/history.json`/`bot-history.json` only if cheap; otherwise omit v1.

## Refresh strategy
- Active window: 60 s; unfocused 5 min; minimised: paused; on activate: refresh if last > 30 s.
- Conditional GET on static files (`If-None-Match`); Worker endpoints are `no-store` so use `If-Modified-Since` only if added upstream.
- Single in-flight request per endpoint (TaskManager dedupe), jitter ±10 s, exponential backoff 60→120→240→… cap 15 min, reset on success. Persist last good payload to `cache/server.json`.
- Fetch population + status in **one tick** (2 requests/min/user worst case).
- Not recommended: WebSockets/SSE (none exist upstream; data only changes once a minute).

## Infrastructure cost (Worker + D1, current `no-store`)
Requests/day = users × active-minutes/day × 2 endpoints. Assume 2 h active/day (120 min).

| Concurrent users (assumed 4× daily users) | Worker requests/day | vs Workers free tier (100k/day) |
|---|---|---|
| 100 | 100×120×2 = 24,000 | 24% |
| 1,000 | 240,000 | **exceeds free tier → paid plan (~$5/mo incl. 10 M requests)** |
| 10,000 | 2.4 M | within paid plan quota; D1 reads depend on whether handlers query D1 per request |

Peak rate at 10,000 concurrent ≈ 333 req/s — fine for Workers, but **each uncached call may hit D1**. **Recommended upstream change (needs your approval, not applied):** add `Cache-Control: public, max-age=30, stale-while-revalidate=60` to `/api/latest` and `/api/status/latest`. Then Cloudflare edge answers repeats, origin/D1 load collapses to ~2–4 req/min regardless of user count and cost is flat. Without it, launcher polling is ≈ proportional to users. Alternative requiring no upstream change: poll only the GitHub Pages JSON (5 min cadence, CDN, free) — acceptable but ≥ 5–15 min stale.

## UI placement (to evaluate on all five skins in Phase 6)
Recommended: compact **footer status chip** (dot + total online + tooltip/popover listing realms, last update, stale marker) — it fits every skin and the existing footer layout; News page gets no extra panel.

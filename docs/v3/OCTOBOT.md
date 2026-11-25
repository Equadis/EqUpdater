# OctoBot integration: request/billing architecture and recommendation

Inspected 2026-11-24 from https://github.com/Equadis/OctoBot (public) and the live endpoints. **Nothing in OctoBot or Cloudflare was changed.** Prices below are from Cloudflare's public pricing as returned by search on 2026-11-24 and must be re-checked on the pricing page before any decision.

## 1. What exists

| Data | Where | Produced by | Response headers |
|---|---|---|---|
| Population: `{all, nzoth, yshaarj, cthun}`, each `{timestamp, online, source}` | `https://octobot-population.eqvoice.workers.dev/api/latest` | Worker **cron `* * * * *`** inserts into D1 `readings` | `cache-control: no-store` (all Worker responses) |
| Status: `{schema, sample:{timestamp, website, realms{}, tcp, auth, latency_ms, source_error}}` | `…/api/status/latest` | same cron | `no-store` |
| Same data, static | `https://equadis.github.io/OctoBot/live.json`, `status/latest.json`, `realms.json` | GitHub Actions every 5 min (best effort) | `max-age=600`, ETag, CORS `*` |

`wrangler.toml`: `workers_dev = true` (served from `*.workers.dev`), one D1 database, one cron. `/api/latest` runs a `SELECT` on D1 per request (`index.js` ~L145); other endpoints write `viewer_presence` rows — **the launcher must never call those**.

## 2. The five layers (what is and is not billed)

| Layer | Effect of `Cache-Control: max-age=30` on a Worker response |
|---|---|
| Client cache | The launcher can honour it (it would skip requests inside 30 s) — but players' clients differ, so this alone does not reduce load much |
| Cloudflare edge cache | **Not applied.** A Worker runs *before* the cache; the Cache API does nothing on `*.workers.dev` (Cloudflare docs: "any *.workers.dev deployments will have no impact"). Edge caching needs a custom domain (or Cloudflare's newer Workers Caching, whose availability/pricing I could not verify) |
| Worker invocations | Billed per request, **cached or not**, while the Worker is in the path |
| D1 reads | Only if the handler queries; today `/api/latest` does. 25 B rows read/month are included on paid, 5 M/day on free: reads are not the bottleneck |
| Upstream collection | Fixed: 1 cron/minute regardless of how many launchers exist. Unaffected by clients |
| GitHub Pages | Static CDN (Fastly), no Worker, no per-request cost; but updated only every 5–10 min and best-effort; soft limits ~100 GB/month bandwidth |

So the earlier suggestion ("add `max-age=30` to the Worker") **would not make cost flat** on workers.dev. Correction accepted.

## 3. Options

| | A. Poll today's Worker | B. Poll GitHub Pages JSON | C. New combined snapshot as a *static* object (proposal) |
|---|---|---|---|
| Freshness | ≤ 1 min | 5–15 min | ≤ 1 min |
| Infrastructure change | none | none | cron additionally writes one `snapshot.json` to a public static origin (R2 public bucket on a custom domain with cache rules, or Workers Static Assets/KV behind a cached route). **Needs your approval; not done** |
| Cost scales with launchers? | **Yes**, linearly (Worker request each) | Effectively no | No (served from CDN edge; origin touched ~once per cache TTL per region) |
| Requests per launcher per min | 2 (population + status) | 2 (or 1 with ETag 304) | **1** |

Snapshot shape (≈300–500 B): `{schema:1, generated_ms, population:{all,nzoth,yshaarj,cthun:{online,ts}}, status:{website,realms{},tcp,auth,latency_ms,ts}, incidents:[…]}` — one object with its own freshness timestamps, so the launcher decides *stale* locally and never infers *offline* from a failed request.

## 4. Projected cost (request charges only)

Assumptions: every active launcher polls once per minute with ±15 s jitter; "evening" = 6 active hours/day × 30 days; "sustained" = all day. Workers Paid ($5/mo) includes 10 M requests, overage $0.30/M *(unverified this session)*. Free tier 100 k Worker requests/day.

| Concurrent launchers | A: Worker, **2 req/min** (evening) | A, sustained | A with one combined Worker request (1/min), evening | C: static snapshot |
|---|---|---|---|---|
| 100 | 2.2 M/mo (72 k/day: inside the free tier) | 8.6 M/mo (288 k/day: over the free tier ⇒ $5 plan) | 1.1 M ⇒ $5 | ≈ $0 |
| 1,000 | 21.6 M/mo ⇒ **≈ $8.5** | 86 M ⇒ **≈ $28** | 10.8 M ⇒ ≈ $5.2 | ≈ $0 |
| 5,000 | 108 M ⇒ **≈ $34** | 432 M ⇒ **≈ $132** | 54 M ⇒ ≈ $18 | ≈ $0 |
| 10,000 | 216 M ⇒ **≈ $67** | 864 M ⇒ **≈ $261** | 108 M ⇒ ≈ $35 | ≈ $0 (bandwidth: 10 k × 1440 × 30 × 400 B ≈ 170 GB/mo, mostly 304s/edge) |

D1 reads: ≤ 3 rows per request ⇒ even 864 M requests ≈ 2.6 B rows, ~10 % of the paid inclusion: negligible. Worker CPU time: tiny per request; not a factor.

These are small absolute numbers; the point is the **shape**: A grows with users, C does not. At 1,000 concurrent the free tier is already gone under A.

## 5. Recommendation
1. **Phase 6 default: option B+ now, C when approved.** Ship the client against a *snapshot interface* (one URL, one schema, ETag-conditional, freshness-stamped). Initially feed it by combining `live.json` + `status/latest.json` (GitHub Pages, no infra change, free, 5–15 min stale and labelled as such). Switch the URL to a static snapshot when you approve option C — no client change.
2. Never poll `/api/latest` directly from every launcher.
3. Client behaviour (implemented in `core/server.py` in Phase 6): 60 s active (±15 s jitter), 5 min unfocused, paused minimised, one refresh on re-activation if older than 30 s; conditional GET; exponential backoff 60 s → 15 min on failure, reset on success; last good snapshot persisted to `cache/server.json`; states `online/offline/degraded/unknown` from OctoBot's own semantics, plus launcher-side `stale` (older than 3× cadence) and `unavailable` (our fetch failed — shows last good value, **never offline**).
4. Placement: a compact footer chip (dot + total online; popover with per-realm numbers and "updated Xm ago"), identical across skins.

## 6. Open items needing you
- Approve (or reject) producing a static snapshot from the existing cron (R2/Static Assets). I will draft the exact patch as a PR against OctoBot for review; nothing deployed.
- Confirm current Cloudflare prices before relying on the table.
- Population `nzoth/yshaarj/cthun` come from in-game `.server info`, `all` from the website census (`realms.json` notes some realms as "waiting for verified feed"): the UI will show per-realm figures only when present and label the source.

## 7. What was built (Phase 6, 2026-11-24)
**Client:** `core/server.py` (`Snapshot`, `ServerFeed`, `http_fetch`), `ServerBridge` (QTimer-driven, window-state aware), `ServerChip.qml` (footer dot + count, popover with per-realm rows and ages), Settings switch *Show server status in the footer* (off = no requests at all). **Nothing in OctoBot or Cloudflare was changed.** Only `equadis.github.io/OctoBot/live.json` and `status/latest.json` are requested (allow-list; the Worker's `/api/*` are never called; a test asserts it). When a combined static snapshot exists, set `server.SNAPSHOT_URL` (one request instead of two, `schema:1` shape in `parse_combined`); nothing else changes.
**Behaviour (as designed in section 5):** 60 s +-15 s active, 5 min unfocused, none minimised, refresh on return only if older than 30 s; conditional GET (a repeat poll is two 304s, measured against the live files); failures back off 60 s -> 15 min and reset on success; the last good snapshot is kept in `cache/server.json`; responses over 64 KB, non-JSON, wrong types, negative or absurd counts and unknown words are rejected or dropped. The dot is coloured only by OctoBot's own *recent* probe; old data is dim, a failed fetch shows the last values with "Could not refresh just now", and nothing the client does can turn the dot red.
**Finding that changes the plan:** measured against the live files today, the population was **1 h 53 min** old and the status sample **9 h 52 min** old, not the 5-15 min the workflow promises (GitHub Actions schedules are best effort). With the 3x-cadence rule the chip therefore shows the count with "Updated 2 h ago / this is out of date" and a *dim* dot. This is correct and honest, but it means option B alone makes the chip rarely green. Option C (a static snapshot written by the Worker's existing every-minute cron) fixes it and is the only part that needs your approval.
**Tests:** `tests/test_v3_server.py` (23), `TestServerChip` (8). **Suite: 1,168, all pass.**


## 8. Snapshot patch (beta prep, 2026-11-25)
Approved and written, **not deployed**. OctoBot draft PR 77 (branch `feat/launcher-snapshot`) adds an additive `GET /api/snapshot` to the existing population Worker: one small JSON document (population plus status), with ETag/304 and an in-memory memo, reading the D1 rows the Worker already keeps. No new bucket, cron, binding or paid product; existing routes and consumers are unchanged (tests in the PR). The owner must review, merge and deploy it.
**Client:** snapshot first; on a 404 or any failure it falls back to the two static files and retries the snapshot after 24 h, so the beta works before and after the deploy. Checked over the real network today: the endpoint is not deployed, the client fell back to the static feed, and showed that data (about 2 h old) as stale.

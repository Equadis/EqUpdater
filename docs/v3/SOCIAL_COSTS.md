# What the social test backend costs, and what stops it costing more

**Bottom line:** at 10 to 2,000 daily active testers the Worker fits inside the **Workers Free plan** on every metered resource by this model (the tightest is object duration at about 83 % of the free daily allowance at 500 DAU and above, and that figure is capped by physics: one object cannot be awake for more than 24 hours a day). On the Workers Paid plan the same load costs **the $5 monthly minimum** (about $5.05 at 2,000 DAU). Nothing here needs a paid plan, and none was provisioned.

These are **estimates from stated assumptions**, not measurements. The deployed Worker's private metrics endpoint shows what really happens; the first live figures are at the end.

## Prices and allowances used (read from Cloudflare's documentation on 2026-11-27; check them before relying on a figure)
| | Workers Free (per UTC day) | Workers Paid (per month) |
|---|---|---|
| Worker requests | 100,000 | $5 minimum covers 10 million, then $0.30 per million |
| Durable Object requests | 100,000 (a WebSocket message received counts 1 for every 20) | 1 million, then $0.15 per million |
| Durable Object duration | 13,000 GB-s | 400,000 GB-s, then $12.50 per million GB-s |
| SQLite rows read / written | 5,000,000 / 100,000 | 25 billion / 50 million, then $0.001 / $1.00 per million |
| Stored data | 5 GB | 5 GB-months, then $0.20 per GB-month |
| What happens at the limit | requests fail until the next UTC day: **no charge** | overage is billed (a Durable Objects spending limit can be set in the dashboard) |

Sources: [Durable Objects pricing](https://developers.cloudflare.com/durable-objects/platform/pricing/), [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/), [Durable Objects spending limits](https://developers.cloudflare.com/durable-objects/platform/spend-management/), [SQLite storage API](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/).

## The model (`python tools/socialserver/cost_model.py`)
Each daily active user has the launcher open **3 hours a day**, **8 friends**, **6 presence changes an hour**; heartbeat every 45 s; 3 connections a day (a start plus the odd reconnect); about 5 ordinary requests a day. Deliberately pessimistic choices: heartbeats are counted as billable incoming messages (the runtime answers them without waking the object, and they may not be billed at all); every event is assumed to keep the object in memory for 10 s before it can hibernate; the expiry alarm fires every 45 s whenever anyone is connected.

| DAU | DO requests / day | Worker requests / day | Duration GB-s / day | Rows read / day | Rows written / day | Tightest free limit | Paid plan $ / month |
|---:|---:|---:|---:|---:|---:|---|---:|
| 10 | 2,129 | 80 | 2,725 | 2,550 | 1,936 | duration 21 % | 5.00 |
| 50 | 2,965 | 400 | 4,025 | 12,750 | 2,000 | duration 31 % | 5.00 |
| 100 | 4,010 | 800 | 5,650 | 25,500 | 2,080 | duration 43 % | 5.00 |
| 500 | 12,370 | 4,000 | 10,800 | 127,500 | 2,720 | duration 83 % | 5.00 |
| 1,000 | 22,820 | 8,000 | 10,800 | 255,000 | 3,520 | duration 83 % | 5.00 |
| 2,000 | 43,720 | 16,000 | 10,800 | 510,000 | 5,120 | duration 83 % | 5.05 |

Why it stays small: presence is **pushed over WebSockets, not polled** (a polled design at one request per five seconds would be 17,280 requests per connected player per day), heartbeats do not wake the object, presence is never written to storage (it lives in the sockets), the only recurring write is a batched counter flush while anyone is connected, and a push goes only to the changer's connected friends. Duration is bounded: one 128 MB object awake all day is 10,800 GB-s, under the free 13,000.

## Safeguards against unexpected usage
* **Edge flood protection** (before a Durable Object is woken): Workers Rate Limiting per address, 120 requests a minute and 20 connection attempts a minute; wrong paths, methods, oversized bodies and unauthenticated calls are refused at the edge.
* **In the object**: sign-ups 20 an hour per address, recovery guesses 8 an hour per name, connection attempts 40 per 10 minutes per account, friend requests 120 an hour, a message bucket per connection (20, refilled at 2 a second; flooding closes the connection), 512-byte messages, 4 KB request bodies.
* **Hard ceilings**: 500 accounts, 400 simultaneous connections, and a **daily request cap of 40,000** after which the service answers "paused for today" until the next UTC day (`DAILY_REQUEST_CAP`, `MAX_ACCOUNTS`, `MAX_CONNECTIONS` in `wrangler.toml`). These bound the bill whatever a client does.
* **Reconnection storms**: clients back off exponentially with jitter and honour `Retry-After`; the server limits attempts per account.
* **Quiet when idle**: no sockets means no alarm and no duration; the object sleeps.
* **No spending limit was set in the Cloudflare dashboard**, and the account's plan could not be read (the stored login lacks the billing scope, and the owner's plan is theirs to know). If the account is on a paid plan, set a Durable Objects spending limit there (link above); on the Free plan the daily limits already stop usage without charges.

## Measured so far (deployed instance, 2026-11-27)
One computer, two to four test accounts, about seven minutes including a four-minute idle period: 36 HTTP requests, 7 connections, 8 billable WebSocket messages, 6 alarms, 90 rows read and 39 rows written; the object's own estimate of its Durable Object requests for the day was 242. After four idle minutes (heartbeats only) both connections were still up, a friend's state was intact, and the next change reached its friend in 21 ms; a connection that stopped pinging was dropped after 105 s (limit 100 s plus the sweep). This is a handful of users on one computer: it checks the mechanism and the order of magnitude, not the table above at scale.

## Watching it
`GET /v1/admin/metrics` with the admin key (`cloudflare/social/README.md`) returns accounts, daily-active accounts, current connections, current in-game players, today's HTTP requests, errors, WebSocket connects and messages, alarms, rows read and written, peaks, the cap's state, an estimated billable-usage block, and the last 14 days of aggregate counters. It holds no names, no tokens and no per-person history.

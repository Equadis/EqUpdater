"""Expected Cloudflare usage and monthly cost of the social test backend, from stated assumptions.

    python tools/socialserver/cost_model.py [--sessions-hours 3] [--friends 8] [--changes-per-hour 6]

The prices and allowances are Cloudflare's published ones, read on 2026-11-27 (links in docs/v3/SOCIAL_COSTS.md);
check them before relying on a figure. Every number below is an ESTIMATE from the assumptions printed with it; the
deployed Worker's private metrics endpoint (``GET /v1/admin/metrics``) shows what really happens.
"""

import argparse
import math

# Workers Free plan, per UTC day
FREE = {"worker_requests": 100_000, "do_requests": 100_000, "gb_s": 13_000, "rows_read": 5_000_000, "rows_written": 100_000}
# Workers Paid plan, per month: $5 minimum, then these allowances and overage prices
PAID_BASE = 5.00
PAID = {"worker_requests": (10_000_000, 0.30 / 1e6), "do_requests": (1_000_000, 0.15 / 1e6), "gb_s": (400_000, 12.50 / 1e6),
        "rows_read": (25_000_000_000, 0.001 / 1e6), "rows_written": (50_000_000, 1.00 / 1e6)}
PING_S = 45.0           # the client's heartbeat interval
ALARM_S = 45.0          # the object's expiry sweep while connections exist
AWAKE_PER_EVENT_S = 10.0   # conservative: an event keeps the object in memory for 10 s before it can hibernate
MEMORY_GB = 0.125       # 128 MB


def day(dau, hours=3.0, friends=8, changes_per_hour=6.0, pings_billed=True):
    """One day's usage for ``dau`` daily active users, each with the launcher open ``hours`` a day."""
    connected_s = dau * hours * 3600
    pings = connected_s / PING_S
    state_msgs = dau * hours * changes_per_hour
    ws_connects = dau * 3                                   # a session start plus the odd reconnect
    http = dau * 5                                          # resume check per session, friend edits, a few registrations
    alarms = min(86400 / ALARM_S, connected_s / ALARM_S)    # while anyone is connected, one sweep per interval
    do_req = http + ws_connects + alarms + math.ceil((state_msgs + (pings if pings_billed else 0)) / 20)
    events = http + ws_connects + alarms + state_msgs
    awake = min(86400.0, events * AWAKE_PER_EVENT_S)
    rows_read = ws_connects * (2 * friends + 4) + state_msgs * (friends + 2) + http * 3
    rows_written = dau * 1.6 + 86400 / ALARM_S * min(1.0, connected_s / 86400)    # a last-seen write per account, metrics flushes
    return {"worker_requests": http + ws_connects, "do_requests": do_req, "gb_s": awake * MEMORY_GB, "rows_read": rows_read,
            "rows_written": rows_written, "ws_messages_in": state_msgs + pings, "ws_connections_peak": dau * hours / 24 * 2}


def monthly(d, days=30):
    m = {k: v * days for k, v in d.items()}
    cost = PAID_BASE + sum(max(0.0, m[k] - inc) * price for k, (inc, price) in PAID.items())
    return m, cost


def fits_free(d):
    return {k: d[k] / FREE[k] for k in FREE}


def table(**kw):
    rows = []
    for dau in (10, 50, 100, 500, 1000, 2000):
        d = day(dau, **kw)
        _m, cost = monthly(d)
        worst = max(fits_free(d).items(), key=lambda kv: kv[1])
        rows.append((dau, d, worst, cost))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions-hours", type=float, default=3.0)
    ap.add_argument("--friends", type=int, default=8)
    ap.add_argument("--changes-per-hour", type=float, default=6.0)
    a = ap.parse_args()
    kw = dict(hours=a.sessions_hours, friends=a.friends, changes_per_hour=a.changes_per_hour)
    print(f"assumptions: launcher open {a.sessions_hours} h/day, {a.friends} friends, {a.changes_per_hour} presence changes/hour,"
          f" heartbeat every {PING_S:.0f} s (counted as billable, worst case)\n")
    print(f"{'DAU':>5} {'DO req/day':>11} {'Worker req/day':>15} {'GB-s/day':>9} {'rows read/day':>14} {'rows written/day':>17} {'tightest free limit':>26} {'paid $/month':>13}")
    for dau, d, (name, frac), cost in table(**kw):
        print(f"{dau:>5} {d['do_requests']:>11,.0f} {d['worker_requests']:>15,.0f} {d['gb_s']:>9,.0f} {d['rows_read']:>14,.0f} "
              f"{d['rows_written']:>17,.0f} {name + ' ' + format(frac, '.0%'):>26} {cost:>13.2f}")


if __name__ == "__main__":
    main()

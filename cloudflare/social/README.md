# equpdater-social

EqUpdater's social **test** backend as a Cloudflare Worker: one Worker, one SQLite-backed Durable Object with
WebSocket Hibernation. Accounts, friendships and live presence for testing; not production authentication. Protocol,
architecture and limits: `docs/v3/SOCIAL_TEST_BACKEND.md`. Costs and safeguards: `docs/v3/SOCIAL_COSTS.md`.

It is separate from OctoBot: its own Worker (`equpdater-social`), its own Durable Object, no D1, no R2, no shared
bindings. It uses nothing that needs a paid plan.

## Run and test locally
```
npm install                      # once: wrangler (Cloudflare's CLI) and workerd
npx wrangler dev                 # http://127.0.0.1:8787
node --test tests/               # pure-logic tests
# from the repository root, the scenario suite against the Worker under workerd:
set EQU3_TEST_WORKER=1 && python -m unittest tests.test_v3_social_worker
```

## Deploy
```
npx wrangler deploy              # first time: add --var MAX_ACCOUNTS:0 so nobody can register before the secrets exist
echo <sha256 of a tester key> | npx wrangler secret put TESTER_KEYS        # comma-separated hashes: several keys, or rotation
echo <sha256 of the admin key> | npx wrangler secret put ADMIN_KEY_HASH
npx wrangler deploy              # again, without the override
```
Secrets are **hashes**: a key is hashed (`python -c "import hashlib;print(hashlib.sha256(b'KEY').hexdigest())"`) and only the hash is stored in Cloudflare. The keys themselves are in `social-test.secrets.txt` here (git-ignored, created by the deployment); hand the tester key to authorised testers, who type it once in the sign-in box. **Rotate** a tester key by putting a new hash (keep the old one in the list until testers have registered, then drop it); accounts already registered do not need the key again. Nothing secret is in `wrangler.toml` or the source (a test checks).

## Metrics (private)
```
curl -H "Authorization: Bearer <admin key>" https://<worker>.workers.dev/v1/admin/metrics
```
Aggregate counters only: no names, tokens or per-person history.

## Limits you can change (`wrangler.toml` `[vars]`)
`PRESENCE_TTL_S`, `ALARM_S`, `DAILY_REQUEST_CAP`, `MAX_ACCOUNTS`, `MAX_CONNECTIONS`, `REGISTER_PER_IP_HOUR`,
`RECOVER_PER_NAME_HOUR`, `WS_PER_ACCOUNT_10MIN`, `FRIEND_ADDS_HOUR`; the per-address edge limits are the `[[ratelimits]]`
blocks. `EDGE_LIMITS_OFF=1` exists only so the test suite can run many sign-ups from one address: never set it on a deployment.

## Files
`src/index.js` the edge (cheap rejections, per-address limits, then the object) · `src/hub.js` the object (accounts,
friends, WebSockets, expiry, metrics) · `src/logic.js` pure helpers · `tests/` node tests of the helpers.

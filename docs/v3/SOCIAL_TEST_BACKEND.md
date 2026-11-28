# The social test backend (protocol v2) and the temporary username sign-in

**A development and testing system, not production authentication.** It lets friends, presence and the sidebar be tried between several launchers and machines before OctoWoW offers real sign-in (`SOCIAL.md`, `OCTOWOW_INTEGRATION_PROPOSAL.md`). Costs and limits: `SOCIAL_COSTS.md`.

## What a player sees
Sign In asks for a **username**, nothing else: no password, no email, no OctoWoW account. The first time on a hosted test environment it also asks for a **tester key** once (given to authorised testers). A new account then shows its **recovery code** once; *Switch account* and *Recovery code* are in the account menu. Click your status dot for Online, Away, Do Not Disturb, Invisible.

## What is behind it
* **Real accounts.** Registering a name creates an account with a random id, a random 256-bit **session token** and a random **recovery code**. Only SHA-256 hashes are stored server-side. A name belongs to its account for good: **nobody else can take it, online or not** (case-insensitive). Signing out ends the session, not the ownership.
* **Moving to another computer**: sign in with the name and the recovery code. The old computer is signed out at once ("recovered elsewhere"). Wrong codes are rate-limited per name (8 an hour) and nothing reveals which part was wrong.
* **Secrets on the client** live only in the operating system's credential manager (`core/credstore`: Windows Credential Manager, Linux keyring/`secret-tool`): the session token and recovery code per username, and the tester key. Not in settings, not in the source, not in the package (a test scans for it, and an early build's plaintext `dev_tokens` setting is deleted on start-up).
* **Restricted to testers**: the hosted instance requires a tester key to *register* (secret `TESTER_KEYS`, stored as SHA-256 hashes), caps accounts (500) and connections (400), and rate-limits sign-ups per address (20 an hour), connection attempts per account (40 per 10 minutes) and friend requests (120 an hour); per-address floods are turned away at the edge before anything is billed (Workers Rate Limiting). A daily request cap pauses the service at 40,000 requests a day.
* **Isolation**: `core/devsocial.py` is the only module that knows about all this and `qtui/main.py` the only file that imports it; it plugs into `account.AuthProvider` / `presence.PresenceProvider`, the interfaces official OctoWoW authentication will use, and is chosen only when a backend address is configured and `account.PROVIDER_CONFIG` is not (a test asserts both).

## Presence is event-driven (WebSocket)
One WebSocket per signed-in launcher. **No request is made every five seconds**: a test counts HTTP requests while presence changes and finds none.
* A status change is sent when it happens; the server tells **only the changer's friends** that are connected. Measured: 21-42 ms from change to friend's screen against the deployed Worker from this computer (see `SOCIAL_COSTS.md`, QA report).
* **Heartbeat**: the client sends the text `ping` after 45 s of quiet; the Worker answers `pong` through `setWebSocketAutoResponse`, which does **not wake** the Durable Object. A connection silent for 100 s (120 s locally) is dropped and the player reads as offline; a clean sign-out or a closed socket is offline at once.
* **Reconnection**: exponential back-off 1, 2, 4 ... 60 s with ±25 % jitter, honouring `Retry-After`; it resets after a connection that lasted 10 s. On every (re)connect the server pushes the friends list with everyone's presence, and the client re-sends its own state, so nothing is lost. A revoked session stops the link and signs out; a second launcher on the same account replaces the first, which gives way and does not fight back.
* **States**: Online, Away, Do Not Disturb, Invisible, Offline; automatic Away after 15 minutes without input is unchanged and never overrides Do Not Disturb or Invisible.
* **Invisible never leaks**: the client sends no activity or realm while invisible, the server drops them if sent, and others are told "offline" with empty activity and realm (tested on the wire).

## Architecture (hosted)
`cloudflare/social`: Worker `equpdater-social` (own Worker, own Durable Object, nothing shared with OctoBot) + **one SQLite-backed Durable Object**. It holds accounts and friendships in SQLite (rows are metered; available on the Workers Free plan) and every live WebSocket (Hibernation API). **Presence is not stored**: it is in each socket's attachment, so it survives hibernation, costs no storage writes, and a missing socket is offline. An alarm every 45 s, only while sockets exist, expires silent ones; with no sockets nothing is scheduled and the object sleeps. Metrics are aggregate daily counters (flushed in batches), plus the last-seen *day* per account for daily-active counts: no per-person history. One object is deliberate: fan-out is an in-process loop with no extra requests, and a test environment is far below one object's capacity (about 1,000 requests a second).

## Protocol v2
JSON; `Authorization: Bearer <session token>` except `info`, `register`, `recover`.

| Call | Result |
|---|---|
| `GET /v1/info` | `{protocol:2, requires_key}` |
| `POST /v1/register {username, key?}` | 200 `{username, token, recovery}` · 400 · 403 key · 409 taken · 429 · 503 full |
| `POST /v1/recover {username, recovery}` | 200 `{username, token}` (old session revoked) · 401 · 429 |
| `POST /v1/logout` | 204 |
| `GET /v1/me` | `{username}` |
| `POST /v1/friends {username}` · `PUT /v1/friends/<name> {favourite}` · `DELETE /v1/friends/<name>` | 200/204 · 400 · 404 no such user · 409 already |
| `GET /v1/ws` (Upgrade) | the presence stream |
| `GET /v1/admin/metrics` (admin key) | private counters |

WebSocket, text JSON. Server to client: `{"t":"friends","friends":[{username,favourite,state,activity,realm}]}` on connect and after edits; `{"t":"presence","u","state","activity","realm"}` to the changer's friends; `{"t":"bye","reason"}` (`replaced`, `signed out`, `recovered elsewhere`, `no heartbeat`). Client to server: `{"t":"state","status":"online|away|dnd|invisible","activity":"launcher|in_game","realm":""}`, `{"t":"sync"}`, and the text `ping`. Limits: 512-byte messages, a 20-message bucket refilled at 2 a second (flooding closes the connection).

## Two implementations, one test suite
* `tools/socialserver/server.py`: the standard-library Python twin (sqlite3 + http.server + a hand-written RFC 6455 server). It is for automated tests and offline work.
* `cloudflare/social`: the Worker. The **same scenarios** (`tests/social_scenarios.py`: accounts, impersonation, recovery, friends, every presence state, Invisible, automatic Away, launcher-open versus in-game, realm switching, game and launcher closing, silent expiry, abrupt disconnection and reconnection, floods, metrics) run against the Python twin (`tests/test_v3_social_backend.py`), the Worker under workerd (`tests/test_v3_social_worker.py`, `EQU3_TEST_WORKER=1`) and, as a smoke test, the deployed Worker (`tests/test_v3_social_live.py`, `EQU3_TEST_LIVE_URL`/`_KEY`). Unit tests cover the link's back-off and the WebSocket client.

## Running it
```
python tools/socialserver/server.py --port 8787 --tester-key KEY      # local twin
set EQU3_SOCIAL_URL=http://127.0.0.1:8787                              # https anywhere; http only to this computer or a private network
python -m equpdater.qtui
```
`python tools/qa/social_instances.py --names Alice Bobby` starts a local server and isolated launchers (own data folder and credentials via `EQU3_PROFILE`), signed in automatically (`EQU3_DEV_LOGIN`); `--set Alice=in_game:nzoth` simulates a game (`EQU3_SIM_ACTIVITY`). These three variables are ignored by a packaged build.

The hosted instance: see `cloudflare/social/README.md` (deploy, secrets, rotation, metrics). **Development builds use it by default** through `devsocial.DEFAULT_URL`; a packaged build contains the address but no key (a tester types it once).

## Realm detection: what was investigated and found
Investigated read-only against a real, running OctoWoW client on the developer's machine (no memory access, no injection; the game was not started or changed):
* **`WTF/Config.wtf` is not a reliable "current realm".** In the observed session the client started at 12:45, the world-server connection began at 20:13, and `Config.wtf` was last written at **20:58, 45 minutes after the connection began and unrelated to it**, holding the last realm chosen (`realmName`). Its value and its file time therefore say nothing dependable about the realm connected *now*, and it would also still name the last played realm after the player left. **It is not used.**
* **Game open versus connected to a realm can be told, read-only**, from the operating system's TCP table: at the login screen only the connection to the login server (`realmlist` host, port 3724) exists; connected to a realm there is also an established connection to a *different* host (the world server; here a non-login address on port 8092). `core/gamewatch.py` reports "In-game" only for that, and "Launcher open" for a game that is open but not connected.
* **Which realm** is decided only by a *known world-server address*: published by OctoWoW/OctoBot (`realms.json` may carry `"world": "host:port"`; the client already reads it) or recorded on the tester's computer with `tools/qa/realm_probe.py --learn <realm>` while connected to that realm. With an unknown address, or two realms that would both fit, the realm is **left empty and never guessed**: the player shows as "In-game". On the observed machine the detector returned *in game, realm unknown*, which is the honest answer until an address is known.
* **Not verified**: the world-server address of each realm (only one connection was observed, and which realm it was has not been confirmed by the player), behaviour at character selection (a world connection exists there too, so it counts as connected), under Wine/Linux (the Linux code reads `/proc` and has never run), and when a realm uses several world-server addresses or changes them.

## Limits
* A tester key and a recovery code are bearer secrets: whoever holds one has the access. There is no email, so a lost recovery code with no signed-in computer means the name is lost (an admin cannot restore it).
* Friendship is one-way and unconfirmed: adding a name shows you their presence (a consent step would be needed for production). Presence of players you did not add is never sent to you.
* One Durable Object is the whole backend: a restart of the object loses nothing stored, but at hundreds of simultaneous players it would need sharding (not built).
* The deployed instance is on `workers.dev`, so there is no WAF/zone-level protection; the protections are the ones listed above.
* Cross-machine behaviour has **not** been verified: the deployed Worker has only been exercised from one computer (see the QA report).

## Replacing it with official authentication
Fill `account.PROVIDER_CONFIG`; implement a `PresenceProvider` for OctoWoW's service (`OCTOWOW_INTEGRATION_PROPOSAL.md` section 5: the same messages are proposed, including `invisible`, and WebSocket/push instead of polling); then delete `core/devsocial.py`, `core/wsclient.py` if unused, `tools/socialserver/`, `cloudflare/social/` and the branch in `qtui/main.py`. Nothing else imports them.

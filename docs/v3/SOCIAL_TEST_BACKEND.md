# The social test backend and temporary login

**A development and testing system, not production authentication.** It exists so friends, presence and the sidebar can be tried between several launchers before OctoWoW offers real sign-in (`SOCIAL.md`, `OCTOWOW_INTEGRATION_PROPOSAL.md`).

## What it is
* **Login by username only**: 3-20 letters, digits, `-` or `_`. No password, no email, no OctoWoW account, no external provider. Names are unique ignoring case. A name in use by a live session is refused; a name is free again after Sign Out or 30 seconds without a heartbeat. The client remembers the token for each name it has used, so switching back to an account works.
* **A shared server** (`tools/socialserver/server.py`, standard library only: `http.server` + `sqlite3`). It keeps usernames, each player's friends and favourites, and a heartbeat in memory. It returns nothing else: a friend is `{username, favourite, state, activity, realm}`.
* **One request about every five seconds** (`POST /v1/sync`) is both the player's heartbeat and the read of their friends with presence. The answer carries an `ETag`; nothing changed means `304` and no body. Status changes are sent at once. A token may sync about once a second at most (`429` + `Retry-After`).
* **Presence expires**: a player is offline 30 seconds after the last heartbeat, so a closed launcher or a crash never stays "online". Signing out is offline at once. **Invisible** is reported to others as offline, with no activity and no realm; the invisible player's own requests work as normal.
* **Where it plugs in**: `core/devsocial.py` implements the same two interfaces production will use (`account.AuthProvider`, `presence.PresenceProvider`). `qtui/main.py` is the only other file that mentions it, and chooses it only when a backend address is configured **and** `account.PROVIDER_CONFIG` (OctoWoW's real sign-in) is not. A test asserts this.

## Protocol v1 (JSON, `Authorization: Bearer <token>` except login)
| Call | Result |
|---|---|
| `POST /v1/login {"username","token"?}` | 200 `{username, token}` · 400 bad name · 409 in use |
| `POST /v1/logout` | 204 |
| `GET /v1/me` | 200 `{username}` · 401 |
| `POST /v1/sync {"status","activity","realm"}` + `If-None-Match` | 200 `{friends:[...]}` + `ETag` · 304 · 429 |
| `POST /v1/friends {"username"}` | 200 · 400 (yourself, bad name) · 404 no such user · 409 already |
| `PUT /v1/friends/<name> {"favourite"}` | 204 · 404 |
| `DELETE /v1/friends/<name>` | 204 · 404 |

`status`: `online | away | dnd | invisible`. `activity`: `launcher | in_game`. `realm`: `nzoth | yshaarj | cthun`, only while `in_game`. The same protocol would fit a Cloudflare Worker with D1; that port is **not written**.

## Running it
```
python tools/socialserver/server.py --port 8787 [--host 0.0.0.0] [--db social-test.sqlite]
set EQU3_SOCIAL_URL=http://127.0.0.1:8787            (https anywhere; http only to this computer or a private network)
python -m equpdater.qtui
```
Out of the box there is **no hosted backend** (`devsocial.DEFAULT_URL` is empty), so the launcher shows "Sign-in is not available yet" until an address is configured. Hosting it (a small VM, a container, or a Worker port) is a decision about cost and ownership that has not been made.

## Testing without several computers (development only)
`python tools/qa/social_instances.py --names Alice Bobby` starts a local server and one isolated launcher per name (own data folder, own session, signed in automatically). `--set Alice=in_game:nzoth` (or `launcher`) writes the file that stands in for a running game; the launcher picks it up within about five seconds. These variables exist only in a development run and a **packaged build ignores them**: `EQU3_SIM_ACTIVITY` (simulated game), `EQU3_DEV_LOGIN` (automatic sign-in), `EQU3_PROFILE` (separate credential entries). The simulation is a different class from the real game detection and is never created unless asked for.

`tests/test_v3_social_backend.py` runs a real server on localhost with several independent launcher instances (core level) and covers: sign-in and name rules, restart persistence, account switching, friends and favourites, all presence states, Invisible, automatic Away, launcher-open versus in-game, realm switching, game and launcher closing, heartbeat expiry, reconnection, backend outage and back-off, `304`/`429`. `tests/test_v3_social_qt.py` drives the interface against the same server.

## Real activity detection (and what is not verified)
* **Game running**: whether `WoW.exe` in the game folder can be written (Windows locks a running image; Linux asks the process list), read every presence round.
* **Realm**: only if the game itself wrote `SET realmName "..."` to `WTF/Config.wtf` **after** the launcher saw it start, and the name matches a known realm. Nothing is inferred from anything the launcher shows. **This has not been verified with a real client logging into each realm.** A stronger signal needs OctoWoW to say where each realm's world server is.

## Limits and what is needed before this is replaced
* No passwords: anyone reaching the server can claim a free name, watch anyone's presence by adding them (no consent step), and a restart of the server forgets presence (users and friends are kept in sqlite).
* Not exercised across machines or over https; no hosted instance; no abuse protection beyond the per-token rate limit.
* To replace it with official authentication: fill `account.PROVIDER_CONFIG` (OctoWoW's OAuth endpoints), implement a `PresenceProvider` for OctoWoW's presence service (`OCTOWOW_INTEGRATION_PROPOSAL.md` section 5, including `invisible`), add a consent step for friend requests if OctoWoW wants one, and delete `core/devsocial.py`, `tools/socialserver/` and the `main.py` branch. Nothing else imports them.

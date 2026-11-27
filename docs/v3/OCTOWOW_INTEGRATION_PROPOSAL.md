# Proposal: sign-in and friend presence for OctoWoW launchers

**To:** the OctoWoW development team  **From:** the EqUpdater project  **Status:** proposal, nothing deployed  **Date:** 2026-11-25 (revised 2026-11-27 for the simplified social model)
**Scope:** an optional OctoWoW account in EqUpdater 3 (a community launcher and updater) and an optional friends list, by forum user name, that shows whether friends have the launcher open or are playing, and on which realm.

This document stands on its own. It says what we would like to build, what we need from OctoWoW to build it, exactly what the requests would look like, how players' privacy is protected, and what we will and will not do. Nothing here changes how players log in to the game, and nothing here is required to play.

## 1. Summary

EqUpdater 3 already has the screens: an account area in the top-right corner (with a presence dot: Online, Away, Do Not Disturb, Invisible) and an always-visible friends list, as in Steam or Battle.net. They are switched **off in effect** because OctoWoW has no published way to (a) let a launcher sign a player in with their existing forum account, or (b) say whether a player is online or playing. We would rather wait for an official answer than guess, scrape, or ask players for their passwords.

We are asking for two small, independent things:

| | What | Why | Effort on your side (our estimate) |
|---|---|---|---|
| **A** | An OAuth 2.0 sign-in for a public desktop client | Show the player's forum name and avatar; let them see their own status | A standard OAuth server in front of the existing accounts (for example Laravel Passport or an OIDC bridge) |
| **B** | A batched presence lookup and status publish, by forum user name | Show which friends have the launcher open or are playing, and on which realm; let the player choose Online, Away, Do Not Disturb or **Invisible** | One read endpoint, one write endpoint, and a rule: an invisible player is returned as offline |

A is useful on its own. B is optional and can come later or never; the friends list works without it (statuses show as "unavailable").

## 2. What we found (read-only, 25 Nov 2026)

* `octowow.st` is a Laravel/Livewire site; its `/login` page is a password form with two-factor support. The forum is phpBB.
* There is no `/.well-known/openid-configuration` or `oauth-authorization-server` document, no `/oauth/authorize`, no `/api`, and no `api.`, `auth.` or `id.` host.
* The only live number published is the website-wide "players online". There is no per-character or per-account data.
* OctoBot's `realms.json` lists the realms; EqUpdater already uses it instead of hard-coding them.

## 3. Principles (what EqUpdater commits to)

1. **Authentication happens on octowow.st, in the player's own browser.** EqUpdater never sees, asks for or stores a forum or game password. It receives a one-time code, exchanges it for tokens, and keeps the tokens in the operating system's credential manager (never in a file).
2. **Optional everywhere.** Updating, configuring and launching the game never wait for, or depend on, sign-in or presence. If your service is down, slow or removed, the launcher behaves exactly as it does today.
3. **Forum identity is separate from game accounts.** Nothing here touches a game login or game password.
4. **Minimal data.** What is exchanged about a player is: forum user name, avatar, a presence status, and **the realm they are playing on, only while in game**. No character name, class, level, guild or zone is requested, sent or displayed. Players do not configure this: they choose a status (Online, Away, Do Not Disturb, Invisible) and that is all.
5. **Polite traffic.** One batched request about every five seconds (fixed, not a setting), only while the player is signed in, has friends and the window is not minimised; honouring `Retry-After`, `ETag`/`304` and with exponential back-off. If you offer push (SSE or WebSocket), EqUpdater will use it instead and stop polling (section 5.5).
6. **No new cost to you beyond the endpoints themselves,** and no collector accounts, bots or scraping on our side.

## 4. A. Sign-in

### 4.1 Flow (RFC 6749 authorization code with PKCE RFC 7636; loopback redirect RFC 8252)

1. EqUpdater starts a listener on `http://127.0.0.1:<random port>/callback` and opens the player's browser at your `authorize_url`.
2. The player signs in on octowow.st as they normally would (including two-factor) and approves "EqUpdater".
3. Your server redirects to the loopback address with `code` and `state`.
4. EqUpdater posts the code and the PKCE verifier to `token_url` and receives tokens; then calls `userinfo_url` once to learn the forum name.

### 4.2 What we need

| Item | Value |
|---|---|
| Client type | **public** (no client secret); `client_id` = `equpdater3` (or your choice) |
| Redirect URI | `http://127.0.0.1:{any port}/callback` allowed for this client (RFC 8252 section 7.3) |
| PKCE | required, method `S256` |
| Scopes | `openid profile` for sign-in; later `presence` / `friends` if B exists |
| `authorize_url`, `token_url`, `userinfo_url` | HTTPS, any paths you like |
| `revoke_url` | optional (RFC 7009); used on Sign Out |
| Access token | short (about 1 hour) |
| Refresh token | rotating or long-lived, revocable from the account page, returned with `offline_access` or implicitly |
| Discovery | `/.well-known/oauth-authorization-server` (RFC 8414) would let us configure ourselves from one address |

### 4.3 Requests and responses

```
POST {token_url}                       Content-Type: application/x-www-form-urlencoded
grant_type=authorization_code&code=...&redirect_uri=http://127.0.0.1:53711/callback
&client_id=equpdater3&code_verifier=...

200 {"access_token":"...","token_type":"Bearer","expires_in":3600,"refresh_token":"...","scope":"openid profile"}
400 {"error":"invalid_grant"}          (refresh rejected: EqUpdater signs the player out locally)
```

```
GET {userinfo_url}                     Authorization: Bearer <access token>
200 {"sub":"12345","preferred_username":"Thrallfan","picture":"https://cdn.octowow.st/avatars/12345.png",
     "profile":"https://octowow.st/forum/memberlist.php?mode=viewprofile&u=12345"}
401 / 403                             (EqUpdater refreshes once, then signs out locally)
```

`preferred_username` must be the **forum user name** shown in the launcher. `picture` is optional; EqUpdater loads it only if it is `https` on a host listed in the client's configuration.

### 4.4 Security notes
* Tokens are stored per RFC 8252 guidance in the OS credential store; a failure to store them only means the player signs in again next time.
* The loopback listener accepts one request, checks `state`, and closes.
* EqUpdater validates every field it reads (lengths, types) and ignores unknown ones.

## 5. B. Presence (optional)

Friends are **OctoWoW forum user names**. Nothing about characters is involved.

### 5.1 Reading friends' status (batched)

```
POST {presence_url}/lookup             Authorization: Bearer <access token>   Content-Type: application/json
If-None-Match: "<etag of the last answer>"
{"friends":["Thrall","Jaina","Arthas"]}                                              (1 to 50 forum user names)

200 {"as_of":"2026-11-27T10:00:00Z",
     "results":[{"user":"Thrall","launcher":"online","game":"in_game","realm":"nzoth","as_of":"2026-11-27T09:59:58Z"},
                {"user":"Jaina","launcher":"away","game":"offline"},
                {"user":"Arthas","launcher":"offline","game":"offline"}]}
304                                                                                     (nothing changed)
429 Retry-After: 120
```

Field meanings (EqUpdater treats anything else as `unknown`):

| Field | Values | Meaning |
|---|---|---|
| `launcher` | `online`, `away`, `dnd`, `offline`, `unknown` | the status the player chose with the launcher open. **A player who chose Invisible must be returned as `offline`** |
| `game` | `in_game`, `offline`, `unknown` | whether the player has a character in the world. Detected on your side (game server or addon), never typed in by the player |
| `realm` | a realm id from `realms.json` | **only when `game` is `in_game`** (and the player is not invisible). Omit it otherwise |
| `as_of` | ISO 8601 | when this was true |

What EqUpdater shows for a friend: the forum user name, a dot (green Online, yellow Away, red Do Not Disturb, grey Offline) and one line: `In-game · N'Zoth` when `game` is `in_game` (just `In-game` if no realm is given), `Launcher open` for any other online player, `Offline`, or `Unavailable` when you have said nothing. Nothing is shown that is not in this table.

Rules we ask you to apply:
* **Invisible means offline to everyone else**, in both `launcher` and `game`, and no `realm`. The player stays signed in and everything works for them locally; only other people's view changes. This is the one behaviour EqUpdater cannot provide by itself.
* **Only users who have accepted the presence feature** (signing in to EqUpdater and not being invisible counts as acceptance for the launcher channel) are returned with data; others are `unknown`, never `offline`.
* Never return `offline` for someone who simply is not using a launcher and whose game state you cannot know; return `unknown`. EqUpdater says "Unavailable" for `unknown`, and does not claim they are offline.
* `Cache-Control` and `ETag` are welcome; EqUpdater sends `If-None-Match`.

### 5.2 Publishing the player's own status

```
PUT {presence_url}/me                  Authorization: Bearer <access token>
{"status":"online"}                    (online | away | dnd | invisible)
204
```

* `away` is also sent automatically after 15 minutes without keyboard or mouse use (EqUpdater's own rule), unless the player chose Do Not Disturb, Away or Invisible, which always win.
* In-game presence needs no flag from the launcher: if the player is playing, you already know it. Please apply `invisible` to it as well.
* Sent only when the status changes. There is no other publish call, and no flags for character, realm or zone: the realm is yours to attach while in game, and the character and zone are never needed.

### 5.3 Alternatives
* **No server presence at all:** EqUpdater keeps working with a local friends list and shows "Unavailable".
* **In-game addon:** an addon could report *the player's own* realm to a file EqUpdater reads locally. That would not tell the launcher about *friends*, so it does not replace 5.1.

### 5.4 Adding friends
EqUpdater adds a friend by forum user name and stores the list on the player's computer only. If you prefer friend relationships to live on your side (so that "add friend" sends a request the other person accepts, as in Steam), that is a larger design and we would adopt it: `GET/POST/DELETE {presence_url}/friends`. Until then nobody is told they were added.

### 5.5 Push instead of polling (preferred if cheap for you)
`GET {presence_url}/stream` as Server-Sent Events, carrying the same `results` objects for the signed-in player's friends when they change. EqUpdater would open it once per run and stop polling. One long-lived connection per launcher is less load than a request every five seconds.

## 6. Performance and cost expectations

* Polling fallback: one request about every five seconds, fixed. One request carries up to 50 names and one token; typical response well under 2 KB, usually `304`.
* With 1,000 launchers that is up to 200 requests per second on a read that can be served from a cache keyed by the set of names and invalidated when a status changes. If that is more than you want to serve, tell us the interval you prefer (it is one constant in EqUpdater) or provide 5.5.
* EqUpdater makes no presence requests when the player is signed out, has no friends, has the window minimised, or your service is down (it backs off 30 s, 60 s, 2 min ... up to 10 min).
* We will not poll anything else of yours: no `/api/*` calls beyond those agreed here.

## 7. Versioning and compatibility
* Every response may carry `"schema": 1`; EqUpdater ignores unknown fields and treats unknown values as `unknown`.
* We will ship the endpoints in a configuration (`account.PROVIDER_CONFIG`), so enabling them is a configuration change in EqUpdater, not a redesign.
* Breaking changes: please version the path (`/v1/...`).

## 8. Acceptance (how we will know it works)
1. A player signs in through the browser and EqUpdater shows their forum name and avatar; the token refreshes after an hour; Sign Out revokes it.
2. A wrong or revoked token signs the player out cleanly with a message, without affecting updating or launching.
3. A batched lookup returns correct states; a player who chose Invisible is returned as `offline` with no `realm`; a realm is returned only while in game.
4. A `429` slows EqUpdater down; an outage leaves the last known data on screen, marked as out of date.
5. Our automated tests (fake server) and a manual run against a staging deployment both pass before anything is enabled for players.

## 9. Proposed rollout
1. You tell us whether you want A, B, both, or neither, and any changes to the shapes above.
2. A staging endpoint and a test account.
3. We switch the configuration on for a beta group and test sign-in, then presence.
4. Enable for everyone only after you are satisfied.

## 10. What we will not do
Collect or store passwords; scrape the website or forum; create collector accounts or bots; read your databases; store tokens in plain text; ask for or display character names, classes, levels, guilds or zones; make sign-in a requirement for any feature.

*Contact: the EqUpdater project (GitHub: Equadis/EqUpdater). Technical detail of the client side: `docs/v3/SOCIAL.md`.*

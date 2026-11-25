# Proposal: sign-in and friend presence for OctoWoW launchers

**To:** the OctoWoW development team  **From:** the EqUpdater project  **Status:** proposal, nothing deployed  **Date:** 2026-11-25
**Scope:** an optional OctoWoW account in EqUpdater 3 (a community launcher and updater) and an optional friends list that can show whether friends are online.

This document stands on its own. It says what we would like to build, what we need from OctoWoW to build it, exactly what the requests would look like, how players' privacy is protected, and what we will and will not do. Nothing here changes how players log in to the game, and nothing here is required to play.

## 1. Summary

EqUpdater 3 already has the screens: an account area in the top-right corner and a collapsible friends sidebar. They are switched **off in effect** because OctoWoW has no published way to (a) let a launcher sign a player in with their existing forum account, or (b) say whether a character is online. We would rather wait for an official answer than guess, scrape, or ask players for their passwords.

We are asking for two small, independent things:

| | What | Why | Effort on your side (our estimate) |
|---|---|---|---|
| **A** | An OAuth 2.0 sign-in for a public desktop client | Show the player's forum name and avatar; let them see their own status | A standard OAuth server in front of the existing accounts (for example Laravel Passport or an OIDC bridge) |
| **B** | A batched, opt-in character presence lookup | Show which friends are in game | One read endpoint that reads what the game server already knows; one write endpoint for the player's own visibility choices |

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
4. **Privacy by default.** Nothing about a player is published unless they turn it on in Settings, item by item.
5. **Polite traffic.** One batched request per refresh (every 30 seconds to 15 minutes, 60 s by default), only while the friends list is open and the window is not minimised, honouring `Retry-After`, with exponential back-off.
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

### 5.1 Reading friends' status (batched)

```
POST {presence_url}/lookup             Authorization: Bearer <access token>   Content-Type: application/json
{"friends":[{"name":"Thrall","realm":"nzoth"},{"name":"Jaina","realm":"yshaarj"}]}      (1 to 50 entries)

200 {"as_of":"2026-11-25T10:00:00Z",
     "results":[{"name":"Thrall","realm":"nzoth","game":"in_game","launcher":"online",
                 "class":"shaman","level":60,"guild":"Horde Elite","zone":"Orgrimmar","as_of":"2026-11-25T09:59:41Z"},
                {"name":"Jaina","realm":"yshaarj","game":"unknown","launcher":"unknown"}]}
429 Retry-After: 120
```

Field meanings (EqUpdater treats anything else as `unknown`):

| Field | Values | Meaning |
|---|---|---|
| `game` | `in_game`, `offline`, `unknown` | whether the **character** is in the world. `offline` only if you can say so |
| `launcher` | `online`, `away`, `dnd`, `offline`, `unknown` | whether the **player** has a launcher open and their chosen status |
| `class`, `level`, `guild`, `zone` | optional | only if the character's owner allowed showing them |
| `as_of` | ISO 8601 | when this was true |

Rules we ask you to apply:
* **Only return what the character's owner opted in to share.** A player who has not opted in is returned as `unknown`, never as `offline` (otherwise "unknown" would leak "hidden").
* Do not return account-to-character links unless the owner allowed them.
* Never invent `offline` for someone who simply is not using a launcher: the launcher distinguishes the two channels and will show "status unavailable" rather than "offline" when `game` is `unknown`.
* `Cache-Control` and `ETag` on the response are welcome; EqUpdater honours them.

### 5.2 Publishing the player's own status (optional)

```
PUT {presence_url}/me                  Authorization: Bearer <access token>
{"status":"online","share":{"launcher":true,"game":true,"character":false,"realm":false,"zone":false}}
204
```

The flags are the player's choices in EqUpdater's Settings; all default to `false`. EqUpdater sends the update only when they change.

### 5.3 Account-to-character association (optional, owner-controlled)

```
GET {presence_url}/characters?user=Thrallfan      Authorization: Bearer ...
200 {"characters":[{"name":"Thrall","realm":"nzoth"}]}      (only characters the owner made public)
```

This enables "add friend by forum user name" and showing a friend's characters under their account.

### 5.4 Alternatives
* **No server presence at all:** EqUpdater keeps working with a local friends list and shows "status unavailable".
* **In-game addon:** an addon could write the *player's own* current character, realm and zone to a file EqUpdater reads locally. That would let the launcher show your own status in game, not your friends'. Needs your approval of such an addon.

## 6. Performance and cost expectations

* One request per refresh, at most every 30 s while the list is open and the launcher is focused; every 5 minutes if unfocused if the player chooses; none when minimised; none when the player has no friends or has turned the sidebar off.
* A request carries up to 50 names and one token. Typical response well under 10 KB.
* With 1,000 launchers open at 60 s that is about 17 requests per second at most, and far fewer in practice (the list must be open).
* We will not poll anything else of yours: no `/api/*` calls beyond those agreed here.

## 7. Versioning and compatibility
* Every response may carry `"schema": 1`; EqUpdater ignores unknown fields and treats unknown values as `unknown`.
* We will ship the endpoints in a configuration (`account.PROVIDER_CONFIG`), so enabling them is a configuration change in EqUpdater, not a redesign.
* Breaking changes: please version the path (`/v1/...`).

## 8. Acceptance (how we will know it works)
1. A player signs in through the browser and EqUpdater shows their forum name and avatar; the token refreshes after an hour; Sign Out revokes it.
2. A wrong or revoked token signs the player out cleanly with a message, without affecting updating or launching.
3. A batched lookup returns correct states for characters that are, and are not, sharing; hidden characters appear as `unknown`.
4. A `429` slows EqUpdater down; an outage leaves the last known data on screen, marked as out of date.
5. Our automated tests (fake server) and a manual run against a staging deployment both pass before anything is enabled for players.

## 9. Proposed rollout
1. You tell us whether you want A, B, both, or neither, and any changes to the shapes above.
2. A staging endpoint and a test account.
3. We switch the configuration on for a beta group and test sign-in, then presence.
4. Enable for everyone only after you are satisfied.

## 10. What we will not do
Collect or store passwords; scrape the website or forum; create collector accounts or bots; read your databases; store tokens in plain text; expose a player's characters without their permission; make sign-in a requirement for any feature.

*Contact: the EqUpdater project (GitHub: Equadis/EqUpdater). Technical detail of the client side: `docs/v3/SOCIAL.md`.*

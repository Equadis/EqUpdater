# Account, friends and presence (EqUpdater 3 beta)

**Status: the interface and the machinery are built and tested against fakes. Nothing is operational against a real OctoWoW service, because OctoWoW offers none.** Sign-in and live presence must not be called working until they have been run against real services. The friends list itself is local and works today.

## 1. What was investigated (2026-11-24, read-only requests, no login attempted)

| Question | Finding |
|---|---|
| What is the website? | `octowow.st` is a **Laravel** application with **Livewire** forms. `/login` is a password form (`username`/`email`, `password`, a two-factor `oneTimeCode`, a recovery code, a password-reset form). |
| What is the forum? | **phpBB** at `/forum/` (it answers with phpBB's own pages and feeds). phpBB has no OAuth *provider* and no public API. |
| OAuth / OpenID Connect? | `/.well-known/openid-configuration` and `/.well-known/oauth-authorization-server` are 404; `/oauth/authorize` is 404. No third-party sign-in buttons on `/login`. |
| Token or REST API? | `/api`, `/api/user` are 404; no `api.`/`auth.`/`id.`/`accounts.` host exists in DNS. |
| Character or presence data? | None published. The only live number is the **aggregate** "players online" (OctoBot's census); OctoBot's `realms.json` states that per-realm figures have "no independently verified feed". The website has a *Player Census* page (HTML, aggregate) and nothing per player. |
| Account-to-character association? | None published. |
| Realm list? | **Yes**: OctoBot's `realms.json` (static, public) lists the realms; EqUpdater reads it (daily, conditional GET) instead of hard-coding them, with a marked, non-authoritative fallback. |

Conclusion: **there is no official authentication endpoint, no presence API and no account-to-character API.** Following the brief, EqUpdater therefore does **not** simulate a sign-in, **does not** collect or store a forum password, **does not** scrape the login form or the forum, and **does not** try to bypass anything.

## 2. What exists in the product

* **Account chip** (upper right, beside Settings, in every skin): *Sign In* and *Create account* (opens `https://octowow.st/login` in the browser, the official site). *Sign In* today explains, in a dialog, that OctoWoW has published no sign-in service and that EqUpdater will not ask for the forum password; it never reports a sign-in. When a provider exists the chip becomes avatar, forum name, status dot and a menu: *View Profile*, *Set Status* (Online / Away / Do Not Disturb), *Sign Out*.
* **Friends sidebar** (every page, every skin, collapsible to a rail; Battle.net opens it by default): search, realm filter, sort by status / name / realm, groups Favourites / Online / Offline, online count, add and remove, favourites, right-click menu, optional login notifications, remembered state. Realms come from OctoBot's list.
* **Presence states**: green *In game*, blue *Online in EqUpdater*, yellow *Away*, red *Do not disturb*, grey *Offline*, and a hollow *?* *Status unavailable*. Launcher presence and character (in-game) presence are separate channels; **not using EqUpdater never makes a character "offline"**: *Offline* is shown only when a service says the *character* is offline, otherwise the friend is *unavailable*, and a group containing such friends is titled "Offline or unavailable". Aggregate census data is never used as an individual's presence (a test checks the presence code does not use it).
* **Settings > Social**: enable the sidebar; show my launcher presence / in-game presence / current character / realm / zone (**all off by default**: nothing is shared unless the player opts in, and today there is nothing to share it with); friend login notifications (off by default); default status; automatic Away after 5-30 minutes of the window being unfocused; refresh interval; Sign Out.

## 3. Architecture (all Qt-free except the bridges)

| Module | Role |
|---|---|
| `core/credstore.py` | secrets in the OS credential manager: Windows Credential Manager (real, `ctypes`, long values split across entries), Linux keyring or `secret-tool` (secret on stdin), else memory only. **Never a file.** |
| `core/account.py` | `AuthProvider` interface; `UnavailableProvider` (the default, `PROVIDER_CONFIG = None`); `OAuthProvider` (authorization code + PKCE S256 with a loopback redirect per RFC 8252, public client with no secret, refresh, revocation; https endpoints only; configured, not coded); `AccountService` (restore at start, early refresh, rejected session dropped, offline keeps the session unverified, sign-out always clears locally). Tokens in the credential store, only the name and avatar address in the settings. |
| `core/friends.py` | `FriendBook`: local list (`social.json`), duplicate prevention on (name, realm, forum), same name on another realm allowed, validation, favourites, search, grouping, sorting, limit 200. |
| `core/presence.py` | the two channels, `resolve()`, `PresenceProvider` interface, `UnavailablePresenceProvider` (the default; no requests), `PresenceService`: batches of up to 50, cache, staleness (3 x interval), rate-limit handling (`Retry-After`, capped at 1 hour, not a failure), exponential backoff 30 s to 10 min, polls **only** while a real provider exists, social is on, the sidebar is open, the window is not minimised, there are friends, and (if required) the player is signed in. |
| `core/realms.py` | the realm list from OctoBot, with a marked fallback. |
| `core/social_service.py` | rows, own status, auto-away, notifications, privacy-first defaults, publish gating. |
| `qtui/social_bridge.py`, `SocialSidebar.qml`, `AccountChip.qml`, `AddFriendOverlay.qml`, `EqUi/SkinActionMenu.qml` | the views. Nothing blocks the GUI thread (sign-in, restore and polls run through the task manager). |

Skins style the sidebar through the `sidebar` component (extends `panel`); layout keys `sidebarWidth` and `sidebarDefault`.

## 4. What OctoWoW would need to provide

Everything below is a *request*, written so a small server-side change can switch the features on without a new EqUpdater design. None of it needs new paid infrastructure on EqUpdater's side.

**A. Sign-in (replaces "Sign In is unavailable")**
* OAuth 2.0 authorization code flow **with PKCE** (RFC 7636) for a **public client** `equpdater3` (no client secret), loopback redirect `http://127.0.0.1:<any port>/callback` (RFC 8252). The user signs in on `octowow.st` as they already do (including two-factor); EqUpdater only receives a one-time code.
* Endpoints, all https: `authorize_url`, `token_url` (code and refresh-token grants), `userinfo_url` (returns `preferred_username`, `sub`, optional `picture`, `profile`), optional `revoke_url` (RFC 7009). A discovery document (`/.well-known/oauth-authorization-server`, RFC 8414) would make this self-describing.
* Short access tokens (an hour), rotating or long-lived refresh tokens, revocable from the account page.
* Scopes: `openid profile` for the name and avatar; `friends` / `presence` only if the services below exist.
* Laravel Passport or Socialite-style OAuth server, or an OIDC bridge in front of the forum, would do; the forum's own user name must be the `preferred_username`.
* To enable: fill in `account.PROVIDER_CONFIG` (authorize/token/userinfo/revoke URLs, `client_id`, scopes, `avatar_hosts`, `profile_url`).

**B. Presence (replaces "status unavailable")**
* `POST /presence/lookup` with up to 50 `{name, realm}` (or account ids) per request, returning per friend `{game: in_game|offline, launcher: online|away|dnd|offline, class, level, guild, zone, as_of}`; **only for players who allowed it**; `429` with `Retry-After`; a `Cache-Control` the client can honour. One batched request per refresh is all EqUpdater sends.
* `PUT /presence/me` to publish the player's own status with exactly the visibility flags they chose in Settings > Social (launcher, in-game, character, realm, zone).
* Character data (class, level, guild, zone) comes from the game server; if OctoWoW would rather not expose it, "in game / offline" alone is useful.

**C. Account-to-character association** (only with the owner's permission, as an opt-in): list a signed-in player's public characters, to support "add friend by forum name" and "show characters under an account".

**D. Alternatives if a server API is not wanted:** an **in-game addon** that reports to the launcher locally (the launcher could then show *the player's own* current character, realm and zone, but not friends'), or a community presence relay. Both would need the OctoWoW team's blessing; neither is built.

## 5. Staged status

| Stage | State |
|---|---|
| 1 Skins (Turtle WoW, Battle.net) | done |
| 2 Account interface and provider abstraction | **done; sign-in is not operational** (no service) |
| 3 Friends sidebar and local friend management | **done and usable today** (local list; statuses unavailable) |
| 4 Official authentication and presence integration | **blocked on OctoWoW** (section 4). The provider code paths are written and tested against fakes; unverified against any real service |
| 5 Account synchronisation, notifications, further social | notifications, status, auto-away and settings are done and local; synchronising a list across devices needs section 4A and a store (not built) |

## 6. Recommended next steps
1. Share section 4 with the OctoWoW developers; sign-in (4A) is the smallest useful step and unlocks the account chip.
2. If they agree to 4B, run the real service through `tools/bench/` style checks (batching, `429`, outage) before enabling `PresenceProvider`.
3. Until then, nothing to do for players: the friends list works locally and says plainly that statuses are unavailable.

For the OctoWoW developers there is a standalone technical proposal: `OCTOWOW_INTEGRATION_PROPOSAL.md`. Sign-in and live presence stay disabled until those services exist.

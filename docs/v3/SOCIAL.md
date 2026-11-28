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
* **Friends sidebar** (every page, every skin, always visible; no collapse, filters or sorting): the player's friends by **OctoWoW forum user name**, with search, Add Friend, Remove Friend, Favourite / Unfavourite, Copy Name (right-click), and the online count. Favourites first, then by presence (in game, online, away, busy), offline and unavailable last. Each row: avatar (the first letter until a service gives one), name, presence dot, and `Status ・ Realm` (the realm only while in game). Never a character, class, level, guild or zone.
* **Presence strings and dots**: a friend is a name, an avatar (the first letter for now), a dot and one line: `In-game · N'Zoth` (the realm only while playing), `Launcher open`, `Offline` or `Unavailable` (no service). The dot is green (online), yellow (away), red (do not disturb) or grey (offline); a tooltip names the status. Having the launcher open and playing are different channels; **not using EqUpdater never makes someone "offline"**.
* **Your own status**: click the dot beside your avatar: Online, Away, Do Not Disturb, Invisible (green, yellow, red, grey). Invisible is published as `invisible` so a service can show you as offline to others; you stay signed in and everything works. A manual status wins over the automatic one; Online becomes Away after 15 minutes without keyboard or mouse use. The choice is remembered.
* **No social settings.** There is no Social section in Settings. Fixed behaviour: sidebar always on; launcher and in-game presence on for a signed-in player, as the chosen status allows; realm shown while in game; character and zone never; Away after 15 minutes; presence arrives by push over one WebSocket while signed in (no polling; the earlier five-second batched refresh was replaced), with automatic reconnection. Settings written by the first beta for these (`social_enabled`, `show_character`, ...) are deleted on start-up and never read.

## 3. Architecture (all Qt-free except the bridges)

| Module | Role |
|---|---|
| `core/credstore.py` | secrets in the OS credential manager: Windows Credential Manager (real, `ctypes`, long values split across entries), Linux keyring or `secret-tool` (secret on stdin), else memory only. **Never a file.** |
| `core/account.py` | `AuthProvider` interface; `UnavailableProvider` (the default, `PROVIDER_CONFIG = None`); `OAuthProvider` (authorization code + PKCE S256 with a loopback redirect per RFC 8252, public client with no secret, refresh, revocation; https endpoints only; configured, not coded); `AccountService` (restore at start, early refresh, rejected session dropped, offline keeps the session unverified, sign-out always clears locally). Tokens in the credential store, only the name and avatar address in the settings. |
| `core/friends.py` | `FriendBook`: local list (`social.json`) of forum user names, duplicate prevention, validation (first-beta character entries are kept and shown by name), favourites, search, grouping, sorting, limit 200. |
| `core/presence.py` | the two channels, `resolve()`, `PresenceProvider` interface, `UnavailablePresenceProvider` (the default; no requests), `PresenceService`: batches of up to 50, cache, staleness (3 x interval), rate-limit handling (`Retry-After`, capped at 1 hour, not a failure), exponential backoff 30 s to 10 min, polls **only** while a real provider exists, social is on, the sidebar is open, the window is not minimised, there are friends, and (if required) the player is signed in. |
| `core/realms.py` | the realm list from OctoBot, with a marked fallback. |
| `core/social_service.py` | rows, own status (Online, Away, Do Not Disturb, Invisible), the 15-minute Away rule, publish gating. No settings, no notifications. |
| `qtui/social_bridge.py`, `SocialSidebar.qml`, `AccountChip.qml`, `AddFriendOverlay.qml`, `EqUi/SkinActionMenu.qml` | the views. Nothing blocks the GUI thread (sign-in, restore and polls run through the task manager). |

Skins style the sidebar through the `sidebar` component (extends `panel`); layout key `sidebarWidth` (the sidebar is always shown; `sidebarDefault` is no longer used).

## 4. What OctoWoW would need to provide

Everything below is a *request*, written so a small server-side change can switch the features on without a new EqUpdater design. None of it needs new paid infrastructure on EqUpdater's side.

**A. Sign-in (replaces "Sign In is unavailable")**
* OAuth 2.0 authorization code flow **with PKCE** (RFC 7636) for a **public client** `equpdater3` (no client secret), loopback redirect `http://127.0.0.1:<any port>/callback` (RFC 8252). The user signs in on `octowow.st` as they already do (including two-factor); EqUpdater only receives a one-time code.
* Endpoints, all https: `authorize_url`, `token_url` (code and refresh-token grants), `userinfo_url` (returns `preferred_username`, `sub`, optional `picture`, `profile`), optional `revoke_url` (RFC 7009). A discovery document (`/.well-known/oauth-authorization-server`, RFC 8414) would make this self-describing.
* Short access tokens (an hour), rotating or long-lived refresh tokens, revocable from the account page.
* Scopes: `openid profile` for the name and avatar; `friends` / `presence` only if the services below exist.
* Laravel Passport or Socialite-style OAuth server, or an OIDC bridge in front of the forum, would do; the forum's own user name must be the `preferred_username`.
* To enable: fill in `account.PROVIDER_CONFIG` (authorize/token/userinfo/revoke URLs, `client_id`, scopes, `avatar_hosts`, `profile_url`).

**B. Presence (replaces "Unavailable")**: see `OCTOWOW_INTEGRATION_PROPOSAL.md` section 5 for the exact requests: a batched lookup by forum user name (`launcher`, `game`, and `realm` only while in game), `PUT /me` with `online | away | dnd | invisible`, and the rule that **an invisible player is returned as offline**. No character, class, level, guild or zone is requested.

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

## 7. The temporary test login and the hosted test backend (2026-11-28)
So that friends and presence can be tried before OctoWoW offers real sign-in, EqUpdater signs in by **username** (no password, no email, no OctoWoW account) against a test backend that keeps real accounts behind the scenes (a session token and a recovery code, names owned for good) and pushes presence over WebSockets. It runs locally (`tools/socialserver`) and as a Cloudflare Worker (`cloudflare/social`, deployed as `equpdater-social`). It is a development and testing system, isolated in `core/devsocial.py`, and it is used only when a backend address is configured and OctoWoW's real sign-in (`account.PROVIDER_CONFIG`) is not. Protocol, architecture, realm-detection findings and limits: `SOCIAL_TEST_BACKEND.md`; costs and safeguards: `SOCIAL_COSTS.md`.

# ADR-0024 — Sign-in: Firebase phone OTP, then our own tokens

- **Status:** Accepted
- **Date:** 2026-10-04 (decided 2–3 October 2026; built in PRs #13–#15 and #55)
- **Decision ID:** D24

## Context
Customers sign in with their phone number. The apps run as a Capacitor Android app and as browser PWAs and console. We need short-lived access tokens, sign-out that takes effect at once, protection against stolen refresh tokens, and no tokens readable by JavaScript in browsers. Firebase sends and checks OTPs well, but its session tokens are not ours to control.

## Decision
- **Phone OTP only.** The app verifies the OTP with Firebase and sends the Firebase ID token to `POST /v1/auth/exchange`. **The first exchange for a number creates the account** (that is signup) with the `customer` role. Locally only (`APP_ENV=dev`), `dev_phone` signs in without OTP.
- **The API verifies Firebase ID tokens itself** with PyJWT, against Google's published keys (fetched over httpx2 and cached), checking audience, issuer and expiry. It does not use `firebase-admin`, which is heavy and synchronous and which we would use only for this.
- **Our tokens:**
  - an access JWT (HS256 with a `kid` for key rotation), valid for 15 minutes;
  - an opaque refresh token, stored only as an HMAC-SHA256 with a server pepper, in a session that lasts at most 30 days.
- **Refresh tokens rotate on every use.** If a used token comes back within 2 minutes while its child is unused (a lost response, two tabs), the child is replaced. Any other reuse means the token was copied: the whole session is revoked (`auth.refresh_reused`).
- **Apps** (Capacitor) send `Authorization: Bearer` and keep the refresh token in secure storage.
- **Browsers** send `X-GB-Client: web` and never see a token. The API sets httpOnly cookies:
  - `__Host-gb_at` (Path=/, 15 minutes);
  - `gb_rt` (Path=/v1/auth).
  Vercel rewrites `/v1/*` to the API, so the cookies are same-origin. **CSRF:** a cookie-authenticated POST, PATCH, PUT or DELETE without `X-GB-Client: web`, or from an Origin outside `WEB_ORIGINS`, gets 403 `auth.csrf_rejected`.
- **Instant sign-out:** logout, logout-all, device sign-out and refresh reuse put the session on a Valkey deny-list for the access token's remaining life. A refresh always checks the session in Postgres.
- **Rate limits:** fixed windows in Valkey. Exchange: 30 per 10 minutes per address and 10 per 10 minutes per phone (the key is a hash, never the number). Refresh: 300 per minute per address. If Valkey is down, requests are allowed (Firebase also limits OTPs).
- **Roles:** a grant is `role` or `role:<scope id>`. `X-Active-Role` narrows a request to one of the caller's roles; endpoints ask `require(permission)`.

## Alternatives considered
- **Firebase sessions end to end:** rejected. We could not revoke at once, add roles to tokens, or keep tokens out of browser JavaScript.
- **Our own OTP over an SMS gateway:** rejected. It costs money and needs DLT registration; Firebase test numbers are free during development.
- **firebase-admin:** rejected (heavy, synchronous, only one function needed).
- **Tokens in localStorage for browsers:** rejected (readable by any injected script).

## Consequences
- (+) Short tokens, instant sign-out, theft detection, no browser-readable tokens, one model for app and web.
- (−) More code to own (covered by `tests/identity/`).
- (−) Real OTPs need Firebase Blaze at the pilot (ADR-0020).
- Handbook 10 and 13 and the M1 spec §3.1 describe this design.

## Related
[ADR-0020](0020-free-tier-hosting-render-vercel-neon-r2.md) · [M1 spec](../modules/M1-identity.md) · Handbook [Chapter 13](../handbook/13-security-and-compliance.md)

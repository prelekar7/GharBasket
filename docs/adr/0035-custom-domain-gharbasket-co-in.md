# ADR-0035 — The custom domain gharbasket.co.in, with DNS on Cloudflare

- **Status:** Accepted (2026-11-11)
- **Date:** 2026-11-11
- **Decision ID:** D35
- **Relates to:** [ADR-0020](0020-free-tier-hosting-render-vercel-neon-r2.md) (hosting; it had no custom domain), [ADR-0024](0024-sign-in-firebase-otp-own-tokens.md) (same-origin cookies through the PWA's `/v1/*` rewrite). Handbook 12 §12.2 "Pointing the domain" holds the records and the settings to change.

## Context
Staging ran on the platforms' own addresses (`gharbasket-api.onrender.com`, and `*.vercel.app` once the frontends are deployed). That has three problems:
- **Links the API builds** (referral links, a society page's join button) defaulted to a placeholder domain, `gharbasket.in`, that we do not own.
- **Every caller names Render.** The Oracle VM scheduler, the mandi collector, the Capacitor build, the PWA rewrite, the Razorpay webhook and the uptime monitor all carry `onrender.com`. Moving the API off Render would mean changing every one of them.
- **Razorpay KYC and the Play Store listing** ask for a website and a contact address on our own domain.

The team bought `gharbasket.co.in` on 2026-11-11 and moved its DNS to Cloudflare, where R2 already lives.

## Decision
- **Three names**, matching the layout the code already assumed for the placeholder:

  | Name | Serves |
  |---|---|
  | `api.gharbasket.co.in` | The API (Render) |
  | `gharbasket.co.in` (`www` redirects to it) | The console: public pages such as `/s/<slug>` and the staff console |
  | `app.gharbasket.co.in` | The customer and Captain PWA |

- **DNS on Cloudflare, every record DNS only** (not proxied). Render and Vercel issue and renew the certificates. The SSL/TLS mode is Full (strict) as a guard.
- **The names point at staging** for now, since it is the only environment. A production environment for the pilot takes them over; staging then moves to `staging.`-prefixed names. That is decided with the paid plans before Phase 6 (handbook 12, Q3).
- **Defaults in the code use the new names**: `PUBLIC_WEB_URL`, `APP_WEB_URL`, the console's `NEXT_PUBLIC_*` fallbacks, the PWA rewrite and the scripts' `STAGING_API_URL` fallback.
- **The platform addresses stay valid** as fallbacks. Nothing depends on them being removed.

## Alternatives considered
- **Keep the platform addresses.** It costs nothing, but it leaves the problems above. Rejected.
- **Proxy the records through Cloudflare** (orange cloud). It would add caching and DDoS protection, but it puts a second TLS hop and a cache in front of Render and Vercel. Nothing we serve needs a CDN yet. Rejected for now; it can be turned on per record later.
- **The PWA on the bare name and the console on `console.`.** Rejected because the public society and referral pages are served by the console, and those are the links people share.

## Consequences
- (+) Moving the API to another host is one CNAME change.
- (+) Shared links carry our own name.
- (−) The domain must be renewed each year. If the domain lapses, the API, the links and sign-in break together. Keep auto-renew on at the registrar.
- (−) Firebase's authorised domains, `WEB_ORIGINS`, Vercel's domain settings and Cloudflare must agree. Handbook 12 §12.2 lists them in one place.

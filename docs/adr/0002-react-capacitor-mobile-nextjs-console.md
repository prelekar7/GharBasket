# ADR-0002 — One TypeScript/React frontend: Vite + Capacitor mobile app, Next.js console

- **Status:** Accepted
- **Date:** 2026-10-02
- **Decision ID:** D2

## Context
The accepted proposal specified Flutter for six or seven separate mobile apps (customer, delivery, kitchen, warehouse, women partner, admin) plus a React/Next.js dashboard. That means two UI languages (Dart and TypeScript), no shared components between mobile and web, and seven app shells for a four-person team. Several users hold more than one role (a Society Captain is also a customer; a kitchen partner also orders vegetables). The team proposed building one web codebase as a PWA and wrapping it with Capacitor, with role-based login.

## Decision
- `apps/mobile`: **Vite + React 19 + TanStack Router** single-page app, installable as a **PWA** and wrapped with **Capacitor 8** for Android.
- **One mobile app for field roles only:** customer, Society Captain, kitchen/salad partner, rider, packer. After phone-OTP login the backend returns `roles[]`; the user picks a role (remembered) and can switch from the profile. Partner roles activate only after KYC; "Become a partner" lives inside the customer app.
- `apps/console`: **Next.js 16** for admin, hub manager, franchise owner and NGO coordinator — web only. It also hosts the public website and SEO society/referral pages.
- Shared `packages/ui`, `packages/api-client` (generated), `packages/domain`.

## Alternatives considered
- **Flutter (original proposal)** — rejected: second language, no UI sharing with the web console, more apps to maintain.
- **Next.js static export for the mobile build** — rejected: static export disables server actions, `proxy.ts`, dynamic routes without pre-generation and default image optimisation; we would fight the framework.
- **Expo / React Native** — rejected: native feel, but its UI cannot be shared with the Next.js console.
- **Admin screens inside the mobile app** — rejected: cramped UI and larger attack surface.
- **Two store apps (customer + partner) from one codebase** — rejected: customers who become partners would need a second install.

## Consequences
- (+) One language and one design system across all screens; generated API client shared everywhere.
- (+) Role switching matches real users; the partner funnel starts inside the customer app.
- (+) Capacitor plugins cover push, camera, QR scanning and background location (which a PWA alone cannot do).
- (−) WebView animation is less smooth than Flutter; requires a performance budget and testing on low-end phones.
- (−) Offline sync must be built with IndexedDB (Dexie) rather than service-worker Background Sync, which is unreliable in Capacitor.
- (−) The department-accepted proposal said Flutter; Prof. Kranti Bade must be informed and the report updated.

## Related
[ADR-0012](0012-android-and-pwa-platforms.md) · [ADR-0014](0014-final-stack-and-service-based-architecture.md) · Handbook [Chapter 09](../handbook/09-frontend-and-design.md)

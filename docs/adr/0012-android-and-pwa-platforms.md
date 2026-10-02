# ADR-0012 — Platforms: Android (Capacitor) + PWA; no iOS build in v1

- **Status:** Accepted
- **Date:** 2026-10-02
- **Decision ID:** D12

## Context
Capacitor can build Android and iOS apps from the same web bundle. An iOS build needs a Mac with Xcode, an Apple Developer account ($99/year) for TestFlight, and extra device testing. Most pilot users in Panvel and Navi Mumbai use Android.

## Decision
- Ship the **Android app** via Capacitor 8 (debug APK per PR; signed AAB to Play Console internal testing or Firebase App Distribution).
- Ship the **installable PWA** from the same build; iPhone users use the PWA.
- No iOS native build in v1.

## Alternatives considered
- **Android + iOS** — rejected for v1: cost and testing effort for a small share of pilot users.

## Consequences
- (+) Lower cost and testing load; one store listing.
- (−) iPhone users miss native-only features (reliable push needs the PWA installed on iOS 16.4+, no background location). Riders and captains must use Android devices.

## Related
[ADR-0002](0002-react-capacitor-mobile-nextjs-console.md) · Handbook [Chapter 09](../handbook/09-frontend-and-design.md)

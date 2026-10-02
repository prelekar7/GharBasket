# ADR-0016 — UI design entirely in Claude Design

- **Status:** Accepted
- **Date:** 2026-10-02
- **Decision ID:** D16

## Context
The team has no dedicated designer. The account provides a Claude **Design** artifact type (live artboards on a canvas) and a **Design System** artifact type. Figma (free education plan) was the alternative, and a hybrid (Claude for screens, Figma for logo/icons) was recommended. The team chose Claude Design for everything.

## Decision
- Create one **"GharBasket Design System"** artifact: tokens (kraft, ink, turmeric, curry-leaf, chilli, neel, slate), typography (Fraunces + Mukta), role accent colours, icon style (Phosphor + custom 1.5 px stroke food icons), core and signature components (Parchi list, Thali week, Gullak meter, Society unlock bar, Know-your-cook card).
- Three **Design canvases** attached to it: Customer, Field roles, Console. Wireframes first, then high-fidelity screens.
- The logo, custom icon set and spot illustrations are also drafted on the canvas, with explicit art direction (hand-drawn ink line + turmeric fill, 1.5 px stroke grid) and iteration so they stay distinctive.
- Roles: Rhythm Thakur and Manish Raje run UX interviews, review through artifact comments, write copy and run usability tests; Yash Salunkhe reviews buildability and ports approved designs into `packages/ui` and Storybook.
- Tokens are copied from the design system into `packages/ui` (`tokens.css`); token changes happen in the design system first.

## Alternatives considered
- **Hybrid (Claude Design + Figma for vector work)** — recommended but not chosen.
- **Figma only** — rejected: slow without a designer.

## Consequences
- (+) Fast iteration; designs are HTML/CSS and map closely to React + Tailwind; shareable with comments.
- (−) Hand-drawn assets may look less unique; requires strong art direction and review against the "not AI-made" rules.
- (−) Artifacts start private; links must be shared with the team and Prof. Kranti Bade.

## Related
[ADR-0017](0017-design-before-code-in-phase-0.md) · Handbook [Chapter 09](../handbook/09-frontend-and-design.md)

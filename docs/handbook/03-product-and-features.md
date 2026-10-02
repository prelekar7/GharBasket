# Chapter 03 — Product and Features

> **In short:**
> - Three applications: the **GharBasket mobile app** (one app for all field roles), the **GharBasket Console** (web, for admin, hub and franchise) and the **public website** (served by the console app).
> - Five field roles share one mobile app: **customer, Society Captain, kitchen partner, rider, packer**. After login the user picks a role; partner roles activate only after KYC approval.
> - The backend is split into **14 modules (M0–M13)**, each owned by one person. The table in §4 marks what is MVP and what is stretch.
> - Seven use-case diagrams below show what each actor can do.

---

## 1. Applications

| App | Technology | Users | Notes |
|---|---|---|---|
| **GharBasket** (mobile) | React 19 + Vite SPA → PWA + Capacitor 8 (Android) | Customer, Captain, Kitchen partner, Rider, Packer | One app; role chooser after login; partner roles gated by KYC; offline-tolerant field flows |
| **GharBasket Console** | Next.js 16 (web) | Admin, Hub manager, Franchise owner, NGO coordinator | Dense tables, maps, route planning, KYC review, analytics; never shipped inside the mobile app |
| **Public website** | Next.js 16 (same app as the console) | Anyone | Landing page, society referral pages (`/s/<society>`), "Know your cook" profiles, privacy notice |

## 2. Roles and login

1. User logs in with a phone OTP.
2. The backend returns the user's list of roles, each with a scope (for example "Captain of Sunrise Towers").
3. One role → straight to that home screen. Several roles → the role chooser ("Continue as: Customer · Captain (Sunrise Towers)"). The last choice is remembered; switching is possible from the profile.
4. A coloured accent bar shows the active role (customer leaf green, captain turmeric, kitchen chilli, rider indigo, packer slate).
5. Partner roles go through: applied → KYC pending → training → verified → active. "Become a partner" is a button inside the customer app.
6. The server checks permissions on every request; switching role only changes the screens.

## 3. Features by role

| Role | Key features (MVP) |
|---|---|
| **Customer** | Onboarding (society, household, diet, fasting/Jain, budget, consent) · Autopilot week (thali calendar) · Weekly box (parchi) with reasons · swap via substitute sheet · skip week · add-ons · tiffin and salad booking · Budget Lock · wallet top-up and statements · order tracking · Kindness donations · society unlock and referrals · text assistant (stretch-tier MVP) · become a partner · data export/delete |
| **Society Captain** | Today's drops · crate handover scan · flat checklist and bag scanning · exceptions (not home, damaged) · donation bags and crate returns · earnings and payout statements |
| **Kitchen partner** | Today's portion counts · menu editor (approved by hub) · prep status (preparing, ready) · training modules · ratings · earnings |
| **Rider** | Route map with stops · crate counts · proof of handover/delivery · pickups on return leg · earnings |
| **Packer** | Packing queue · pick-and-pack with bag QR scanning · shortage reporting |
| **Hub manager / Franchise owner** (console) | Ops board · procurement plan review and POs · goods receiving and QC · packing board · live routes · deliveries · waste and surplus cascade · vendors · franchise P&L · unit-economics calculator |
| **Admin** (console) | Catalogue and prices · plans · societies (map polygon editor) · hubs · partner KYC review · donation drives and NGOs · analytics · AI health · settings |
| **NGO coordinator** (console) | Handoff schedule, items received, photo proof, impact reports |

## 4. Module map

| ID | Module | Owner | MVP | Stretch |
|---|---|---|---|---|
| M0 | Platform (repo, CI, auth middleware, config, outbox, design tokens) | Yash + Prathamesh | Yes | — |
| M1 | Identity & Household (OTP, roles, household, members, addresses, consents) | Yash | Yes | — |
| M2 | Catalog & Pricing (products, kits, recipes, menus, prices, substitution graph, festival calendar) | Yash | Yes | — |
| M3 | Plans & Subscriptions (plans, society delivery days, skip/pause, Budget Lock, weekly cycles) | Yash | Yes | — |
| M4 | Orders & Checkout (cart, order state machine, slots, cut-off lock) | Yash | Yes | — |
| M5 | Billing & Ledger (Razorpay test, wallet, double-entry ledger, Kindness points, refunds) | Yash | Yes | UPI Autopay |
| M6 | Hub Operations (inventory, procurement, vendors, packing, QC, waste) | Prathamesh | Yes | Produce photo grading |
| M7 | Logistics (vehicles, OR-Tools routes, proofs, captain handover, reverse pickups) | Prathamesh | Yes | Live GPS on map |
| M8 | Partner Network (onboarding, KYC, FSSAI, training, kitchen assignment, earnings, payouts) | Prathamesh | Yes | Micro-loans |
| M9 | Community & Kindness (societies, unlock, referrals, drives, donations, NGO handoff) | Prathamesh (backend), Yash (screens) | Basic | Leaderboards |
| M10 | Intelligence (ML service: consumption model, ranker, meal planner, forecast, procurement, routing, assignment, slot pricing, churn) | Prathamesh | v1 | Fridge-photo input |
| M11 | Notifications (push, WhatsApp, templates, preferences) | Yash | Yes | — |
| M12 | Console & Analytics (KPIs, unit economics, impact, AI health) | Prathamesh | Yes | — |
| M13 | Assistant (text chat with tool calls; voice later) | Prathamesh | Text | Voice |

*Figure 3.1 — Module map and dependencies*

```mermaid
flowchart LR
  M0["M0 Platform"]
  subgraph Commerce["Commerce core (Yash)"]
    M1["M1 Identity"]
    M2["M2 Catalog"]
    M3["M3 Subscriptions"]
    M4["M4 Orders"]
    M5["M5 Billing"]
    M11["M11 Notify"]
  end
  subgraph Ops["Operations and intelligence (Prathamesh)"]
    M6["M6 Hub ops"]
    M7["M7 Logistics"]
    M8["M8 Partners"]
    M9["M9 Community"]
    M10["M10 Intelligence"]
    M12["M12 Analytics"]
    M13["M13 Assistant"]
  end
  M0 --- Commerce
  M0 --- Ops
  M10 -->|plans| M3
  M3 -->|approved boxes| M4
  M4 -->|lock, pay| M5
  M4 -->|locked orders| M6
  M10 -->|forecast| M6
  M6 -->|packed| M7
  M10 -->|routes| M7
  M10 -->|assignments| M8
  M7 -->|pickups| M9
  M9 -->|points| M5
  M13 -->|tool calls| M3
  M13 -->|tool calls| M4
  M4 -.->|events| M11
  M4 -.->|events| M12
```

## 5. Feature → module traceability (selected)

| Feature | Modules |
|---|---|
| Autopilot weekly plan | M10 (planner, pantry twin, ranker) → M3 (cycle, box lines) → M11 (notify) |
| Box edit and approval | M3, M2 (substitutes), M10 (feedback signals) |
| Cut-off and payment | M4, M5 |
| Procurement list | M6, M10 (forecast + newsvendor) |
| Packing and shortages | M6, M4, M5 (refunds) |
| Routing and captain handover | M7, M10 (solver), M9 (societies) |
| Tiffin day | M2 (menus), M4, M8, M10 (fair assignment) |
| Kindness donation | M9, M7 (pickup), M5 (points) |
| Society unlock and referrals | M9, M10 (route-cost model), M5 (rewards) |
| Assistant | M13 → M3, M4 APIs |

## 6. Use-case diagrams

*Figure 3.2 — Use cases: Customer*

```mermaid
flowchart LR
  C((Customer))
  subgraph SYS["GharBasket"]
    U1(["Log in with phone OTP"])
    U2(["Set up household and diet"])
    U3(["Give or revoke consent"])
    U4(["View Autopilot week"])
    U5(["Edit or swap box items"])
    U6(["Approve or skip week"])
    U7(["Set Budget Lock"])
    U8(["Book tiffin or salad"])
    U9(["Top up wallet"])
    U10(["Track order"])
    U11(["Rate delivery or tiffin"])
    U12(["Schedule donation pickup"])
    U13(["Refer neighbours"])
    U14(["Chat with assistant"])
    U15(["Apply to become partner"])
    U16(["Export or delete my data"])
  end
  C --- U1 & U2 & U3 & U4 & U5 & U6 & U7 & U8
  C --- U9 & U10 & U11 & U12 & U13 & U14 & U15 & U16
```

*Figure 3.3 — Use cases: Society Captain*

```mermaid
flowchart LR
  SC((Society Captain))
  R((Rider))
  subgraph SYS["GharBasket — Captain mode"]
    U1(["View today's drops"])
    U2(["Receive crates from rider"])
    U3(["Scan bags to flats"])
    U4(["Report exception: not home or damaged"])
    U5(["Collect donation bags"])
    U6(["Return empty crates"])
    U7(["Onboard neighbours"])
    U8(["View earnings and payouts"])
  end
  SC --- U1 & U2 & U3 & U4 & U5 & U6 & U7 & U8
  R --- U2
  R --- U6
```

*Figure 3.4 — Use cases: Kitchen partner*

```mermaid
flowchart LR
  K((Kitchen partner))
  H((Hub manager))
  subgraph SYS["GharBasket — Kitchen mode"]
    U1(["Complete training modules"])
    U2(["Set daily capacity and menu skills"])
    U3(["Propose weekly menu"])
    U4(["Accept today's portion counts"])
    U5(["Update prep status"])
    U6(["Mark ready for pickup"])
    U7(["View ratings"])
    U8(["View earnings"])
  end
  K --- U1 & U2 & U3 & U4 & U5 & U6 & U7 & U8
  H --- U3
```

*Figure 3.5 — Use cases: Rider*

```mermaid
flowchart LR
  RD((Rider))
  subgraph SYS["GharBasket — Rider mode"]
    U1(["View route and stops"])
    U2(["Load crates at hub"])
    U3(["Navigate to society"])
    U4(["Hand over crates to captain"])
    U5(["Capture proof: OTP or photo"])
    U6(["Collect crates and donations"])
    U7(["Pick up tiffins from kitchens"])
    U8(["View earnings"])
  end
  RD --- U1 & U2 & U3 & U4 & U5 & U6 & U7 & U8
```

*Figure 3.6 — Use cases: Packer*

```mermaid
flowchart LR
  P((Packer))
  subgraph SYS["GharBasket — Packer mode"]
    U1(["View packing queue"])
    U2(["Scan bag QR"])
    U3(["Pick and weigh lines"])
    U4(["Report shortage"])
    U5(["Close bag and assign crate"])
  end
  P --- U1 & U2 & U3 & U4 & U5
```

*Figure 3.7 — Use cases: Hub manager and Franchise owner*

```mermaid
flowchart LR
  HM((Hub manager))
  FO((Franchise owner))
  subgraph SYS["GharBasket Console — Hub"]
    U1(["Review procurement plan"])
    U2(["Raise purchase orders"])
    U3(["Receive goods and QC"])
    U4(["Monitor packing board"])
    U5(["Publish and monitor routes"])
    U6(["Handle failed deliveries"])
    U7(["Log waste and surplus cascade"])
    U8(["Approve kitchen menus"])
    U9(["Manage vendors and scores"])
    U10(["View hub P&L"])
    U11(["Use unit-economics calculator"])
  end
  HM --- U1 & U2 & U3 & U4 & U5 & U6 & U7 & U8 & U9
  FO --- U10 & U11
  FO --- U5
```

*Figure 3.8 — Use cases: Admin and NGO coordinator*

```mermaid
flowchart LR
  AD((Admin))
  NG((NGO coordinator))
  subgraph SYS["GharBasket Console — Admin"]
    U1(["Manage catalogue and prices"])
    U2(["Manage plans"])
    U3(["Draw society boundaries"])
    U4(["Manage hubs"])
    U5(["Review partner KYC"])
    U6(["Run donation drives"])
    U7(["View analytics and impact"])
    U8(["Monitor AI health"])
    U9(["Record NGO handoff"])
    U10(["Download impact report"])
  end
  AD --- U1 & U2 & U3 & U4 & U5 & U6 & U7 & U8
  NG --- U9 & U10
```

## Decisions & open questions

- D2 One TypeScript/React codebase; single mobile app for field roles; Next.js console — [ADR-0002](../adr/0002-react-capacitor-mobile-nextjs-console.md).
- D5 Assistant: text MVP, voice stretch — [ADR-0005](../adr/0005-text-assistant-mvp-voice-stretch.md).
- D9 Team ownership — [ADR-0009](../adr/0009-team-ownership-and-tracks.md).
- D12 Android + PWA only — [ADR-0012](../adr/0012-android-and-pwa-platforms.md).
- Open: final plan tiers and which features are paid; leaderboard and micro-loan features stay stretch.

Related: [01 Vision and scope](01-vision-and-scope.md) · [04 System architecture](04-system-architecture.md) · [07 Workflows](07-workflows.md) · [09 Frontend and design](09-frontend-and-design.md)

# Chapter 07 — Workflows

> **In short:**
> - GharBasket runs on a **weekly rhythm per society**: plan at T-3, edit until the **T-1 21:00 cut-off**, buy at 04:00 on T, pack, route, deliver through the Society Captain, and collect crates and donations on the return trip.
> - This chapter describes the **12 core workflows (W1–W12)** step by step, with UML sequence diagrams for the critical interactions and activity diagrams for decision-heavy processes.
> - Heavy AI runs in batch jobs and writes results to `intel.*` tables. Customer-facing calls never wait on a long ML job, and every ML call has a rule-based fallback.
> - Every money-changing step is idempotent and goes through the ledger. Every status change goes through the state machines in Chapter 06.

---

## 7.1 The weekly rhythm

Each society has fixed delivery days (for example, Sunrise Towers: Tuesday, Friday and Sunday mornings). **T** is the delivery day.

| When (IST) | Job / activity | Module | Workflow |
|---|---|---|---|
| T-3 18:00 | `generate_weekly_plans` — meal plan → box draft | intel → subscriptions | W2 |
| T-3 18:30 | Box-ready notification + pre-debit notice | notify, billing | W2, W3 |
| T-3 → T-1 21:00 | Customer views, edits, approves, skips | subscriptions | W2 |
| T-1 19:00 | Reminder for unapproved boxes | notify | W2 |
| T-1 20:55 | `apply_auto_approve` — approve for households that asked (notice ≥ 24 h before) | subscriptions | W2 |
| **T-1 21:00** | **`cutoff_lock`** — lock orders, debit wallet / capture Autopay; unpaid → `payment_failed` with a 2 h grace | orders, billing | W2, W3 |
| T-1 23:00 | `payment_grace_expiry` — still unpaid → cancelled (reason `payment`); the cycle mirrors it | orders → subscriptions | W3 |
| T-1 23:15 | `solve_routes` per hub (M10 solver, or M7's fallback when ML is down) → plan published when its check is clean; vans leave at 06:30 | logistics ← intel | W5 |
| T-1 21:10 | `forecast_uncommitted` → `build_procurement_plan` | intel → ops | W4 |
| T-1 21:30 | `assign_kitchens` for tiffins on T | intel → partners | W6 |
| T 04:00 | Buying at APMC Vashi / FPO, goods receipt, QC | ops | W4 |
| T 05:00 | `build_packing_tasks` → packing | ops | W4 |
| T 08:00–10:00 | Society handover to captain → doorstep delivery | logistics | W5 |
| T 09:35 | `solve_tiffin_routes` per hub (lunch; kitchens first, then societies, in M7 without ML) → plan published when its check is clean | logistics ← partners | W6 |
| T 10:00–12:00 | Return leg: crates + Kindness pickups | logistics, community | W5, W8 |
| T 11:30–13:30 | Tiffin ready, collected by the rider's scan, handed to the captain, delivered | partners, logistics | W6 |
| T 12:30 | `surplus_cascade` | ops | W9 |

Scheduled jobs run without a broker (D21): each one is an internal endpoint, `POST /v1/internal/jobs/<name>`, called by a GitHub Actions schedule. Jobs are idempotent, take a PostgreSQL advisory lock and use the *scheduled* time, so a schedule that starts a few minutes late changes nothing. Events between modules go through the Postgres outbox, which the API relays after each commit.

*Figure 7.1 — Weekly rhythm from T-3 to T*

```mermaid
gantt
    title Weekly rhythm for one society delivery day (T)
    dateFormat YYYY-MM-DD HH:mm
    axisFormat %a %H:%M
    section Plan
    Autopilot generates plans          :milestone, m1, 2026-10-06 18:00, 0m
    Box-ready and pre-debit notices    :milestone, m2, 2026-10-06 18:30, 0m
    Customer edit and approve window   :a1, 2026-10-06 18:30, 2026-10-08 21:00
    section Commit
    Cut-off lock and payment           :milestone, m3, 2026-10-08 21:00, 0m
    Forecast and procurement plan      :a2, 2026-10-08 21:10, 20m
    Kitchen assignment                 :a3, 2026-10-08 21:30, 15m
    section Fulfil on T
    Buying and goods receipt           :a4, 2026-10-09 04:00, 60m
    Packing                            :a5, 2026-10-09 05:00, 150m
    Route solve and dispatch           :a6, 2026-10-09 07:00, 60m
    Captain handover and delivery      :a7, 2026-10-09 08:00, 120m
    Return leg pickups                 :a8, 2026-10-09 10:00, 120m
    Tiffin delivery                    :a9, 2026-10-09 12:30, 60m
    Surplus cascade                    :a10, 2026-10-09 12:30, 60m
```

---

## W1 — Customer onboarding (target under 2 minutes)

1. Open the app (PWA or Android) and choose a language: English, Hindi or Marathi.
2. Enter a phone number → Firebase OTP → the app exchanges the Firebase ID token for GharBasket tokens (`POST /v1/auth/exchange`).
3. Pick the society from a searchable list. If it is missing, drop a map pin, which creates a "society request" for the hub.
4. Enter tower and flat.
5. Household setup: size, diet, cuisine, fasting / Jain toggles, health tags (after an explicit consent screen), weekday cooking time, no-cook days, optional Budget Lock.
6. Preview the **first Autopilot week**, based on the cohort prior (cold start).
7. Choose payment mode: wallet top-up or UPI Autopay mandate.
8. Done. A society-unlock progress bar shows how many neighbours are needed for free delivery.

*Figure 7.2 — Activity: customer onboarding*

```mermaid
flowchart TD
    A([Open app]) --> B[Choose language]
    B --> C[Enter phone number]
    C --> D[Firebase OTP]
    D --> E{OTP valid?}
    E -- No --> C
    E -- Yes --> F[Exchange token for GharBasket session]
    F --> G{Society in list?}
    G -- Yes --> H[Select society, tower, flat]
    G -- No --> I[Drop map pin and create society request]
    I --> H
    H --> J[Household size, diet, cuisine, fasting rules]
    J --> K{Add health tags?}
    K -- Yes --> L[Consent screen for health personalisation]
    L --> M[Weekday cooking time and no-cook days]
    K -- No --> M
    M --> N{Enable Budget Lock?}
    N -- Yes --> O[Set monthly budget]
    N -- No --> P[Preview first Autopilot week]
    O --> P
    P --> Q{Payment mode}
    Q -- Wallet --> R[Top up wallet]
    Q -- Autopay --> S[Create UPI mandate]
    R --> T[Show society unlock progress]
    S --> T
    T --> U([Onboarding complete])
```

*Figure 7.3 — Sequence: login and role switch*

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant APP as Mobile app
    participant FB as Firebase Auth
    participant API as api - identity
    participant DB as PostgreSQL
    U->>APP: Enter phone number
    APP->>FB: Request OTP
    FB-->>U: SMS OTP
    U->>APP: Enter OTP
    APP->>FB: Verify OTP
    FB-->>APP: Firebase ID token
    APP->>API: POST /v1/auth/exchange (ID token)
    API->>FB: Fetch Google public keys (cached)
    API->>API: Verify ID token with PyJWT (signature, audience, issuer, expiry)
    API->>DB: Find or create user (first exchange = signup, customer role), load roles with scopes
    API-->>APP: Access JWT (15 min), refresh token (session up to 30 days), roles
    alt Only customer role
        APP->>APP: Open customer home
    else Several verified roles
        APP-->>U: Continue as Customer or Captain (Sunrise Towers)
        U->>APP: Choose Captain
        APP->>APP: Save active role, load captain route chunk
    end
    APP->>API: GET /v1/captain/today (header X-Active-Role)
    API->>API: Check permission and scope (society)
    API-->>APP: Today's handovers
```

---

## W2 — Weekly Autopilot cycle

1. **T-3 18:00:** job `generate_weekly_plans` (internal job endpoint, scheduled by GitHub Actions) runs for each hub. The ML service loads the household profile, pantry twin estimates, festival calendar, prices and Budget Lock, then runs the CP-SAT meal planner and the basket ranker.
2. Plans are written to `intel.predictions`. The subscriptions module turns each plan into a `weekly_cycle` with `box_lines` and a `reason_code` per line.
3. If the ML service fails or times out, the fallback is **last week's box adjusted by family-size rules** (reason code `FALLBACK_RULES`).
4. **T-3 18:30:** box-ready notification (push + WhatsApp) and, for Autopay users, the pre-debit notice.
5. **T-3 → T-1 21:00:** the customer views the Thali week and the Parchi box. They can swap items (substitute sheet), change quantities, skip the week, add add-ons and book tiffins. Each edit uses optimistic locking (`If-Match: version`) and records feedback signals for the models.
6. **T-1 19:00:** reminder for unapproved boxes.
7. **T-1 21:00:** cut-off locks approved boxes (and auto-approves those with `auto_approve = true` whose notice was sent at least 24 h earlier), then takes payment (W3).

*Figure 7.4 — Sequence: Autopilot weekly plan generation*

```mermaid
sequenceDiagram
    autonumber
    participant SCH as GitHub Actions schedule
    participant W as api - job generate_weekly_plans
    participant ML as ml service
    participant DB as PostgreSQL
    participant SUB as api - subscriptions
    participant N as notify
    SCH->>W: POST /v1/internal/jobs/generate_weekly_plans (hub, T), scheduled T-3 18:00
    W->>ML: POST /plan/week batch (households of hub)
    ML->>DB: Read profiles, pantry estimates, prices, festivals, Budget Lock
    loop Each household (in parallel)
        ML->>ML: Rank candidates (LightGBM)
        ML->>ML: CP-SAT meal plan, 5 s limit
        ML->>ML: Ingredients times pantry quantities to box lines with reasons
    end
    ML->>DB: Write intel.predictions (model name and version)
    ML-->>W: intel.plans_ready
    W->>SUB: Create weekly cycles from plans
    alt Plan missing or ML timeout
        SUB->>SUB: Fallback - last week box adjusted by family-size rules
    end
    SUB->>DB: Insert weekly_cycles, box_lines and outbox event
    DB-->>N: Outbox relay - subscriptions.cycle_generated
    N-->>N: Send box-ready push and WhatsApp, pre-debit notice
```

*Figure 7.5 — Sequence: box edit and approve with optimistic locking*

```mermaid
sequenceDiagram
    autonumber
    actor C as Customer
    participant APP as Mobile app
    participant API as api - subscriptions
    participant ML as ml service
    participant DB as PostgreSQL
    C->>APP: Open Parchi box
    APP->>API: GET /v1/cycles/current
    API-->>APP: Lines, reasons, version 3
    C->>APP: Tap swap on tomato
    APP->>API: GET /v1/products/tomato/substitutes
    API->>ML: Substitutes for household (timeout 800 ms)
    alt ML responds
        ML-->>API: Ranked substitutes with price delta
    else ML timeout
        API->>DB: Read catalog.substitutions (static scores)
    end
    API-->>APP: 3 substitutes
    C->>APP: Choose lauki
    APP->>API: PATCH /v1/cycles/42/lines (If-Match version 3)
    API->>DB: UPDATE where version = 3, set version 4
    alt Version matched
        API-->>APP: 200 OK, version 4
    else Edited elsewhere
        API-->>APP: 412 Precondition Failed
        APP->>API: Reload cycle and show latest box
    end
    C->>APP: Approve this week
    APP->>API: POST /v1/cycles/42/approve (If-Match version 4)
    API->>DB: Status APPROVED, outbox subscriptions.box_approved
    API-->>APP: Approved, ready for cut-off
```

---

## W3 — Payments

1. **Wallet top-up:** the app asks the API to create a Razorpay order (our own thin async Razorpay client on httpx2) → Razorpay Checkout runs inside the WebView → Razorpay sends a webhook → the webhook is stored, then processed idempotently → ledger entry → push notification.
2. **Autopay (from the pilot phase; MS-A uses prepaid wallet top-ups only):** the mandate is created once. Each cycle sends a pre-debit notice at T-3 18:30 and captures at cut-off.
3. **At cut-off:** after 21:00 IST the API itself refuses box edits; the `cutoff_lock` job (internal job endpoint, scheduled by GitHub Actions) then locks the orders. The wallet is debited (`wallet → unearned_revenue`) or Autopay is captured. Failure → `PAYMENT_FAILED` → 2-hour grace notification → cancel.
4. **Refunds:** shortages and undelivered orders are refunded to the wallet instantly, unless that would take the wallet over ₹10,000: then the same refund goes to the original payment source (M5 §3.3). Other refunds to the original source happen on request.
5. **Reconciliation:** an hourly job (internal job endpoint, scheduled by GitHub Actions) compares local payments with the Razorpay API.

*Figure 7.6 — Sequence: cut-off lock and payment*

```mermaid
sequenceDiagram
    autonumber
    participant SCH as GitHub Actions schedule
    participant W as api - job cutoff_lock
    participant DB as PostgreSQL
    participant BIL as api - billing
    participant RZP as Razorpay
    participant N as notify
    SCH->>W: POST /v1/internal/jobs/cutoff_lock (hub, T), scheduled T-1 21:00
    W->>DB: pg_try_advisory_lock(hub, T)
    alt Lock already held
        W-->>SCH: Skip, another run is active
    else Lock acquired
        W->>DB: Auto-approve eligible boxes (notice sent at least 24 h earlier)
        W->>DB: Batch UPDATE approved cycles to orders RETURNING ids
        loop Each order (batches of 200)
            alt Payment mode wallet
                W->>BIL: Debit wallet (idempotency key order id)
                BIL->>DB: Journal entry wallet to unearned_revenue
            else Payment mode Autopay
                W->>BIL: Capture against mandate
                BIL->>RZP: Create recurring payment
                RZP-->>BIL: Accepted, final result by webhook
            end
            alt Payment ok
                W->>DB: Order LOCKED, outbox orders.locked
            else Insufficient balance or capture failed
                W->>DB: Order PAYMENT_FAILED, outbox billing.payment_failed
                DB-->>N: Grace notification, 2 h to top up
            end
        end
        W->>DB: Release advisory lock
    end
```

*Figure 7.7 — Sequence: wallet top-up and idempotent Razorpay webhook*

```mermaid
sequenceDiagram
    autonumber
    actor C as Customer
    participant APP as Mobile app
    participant API as api - billing
    participant RZP as Razorpay
    participant DB as PostgreSQL
    participant W as api - background task
    C->>APP: Top up 1000 rupees
    APP->>API: POST /v1/wallet/topups (Idempotency-Key k1)
    API->>DB: Check balance cap 10000 rupees
    API->>RZP: Create order
    RZP-->>API: order_id
    API->>DB: payments row CREATED, idempotency key k1 stored
    API-->>APP: order_id for checkout
    APP->>RZP: Checkout (UPI or card)
    RZP-->>APP: Success callback
    RZP->>API: Webhook payment.captured (signed)
    API->>API: Verify signature
    API->>DB: INSERT payment_webhooks (event_id unique)
    alt New event
        API-->>RZP: 200 OK
        W->>DB: Process webhook, journal entry razorpay_clearing to wallet
        W->>DB: Payment POSTED, outbox billing.wallet_topped_up
    else Duplicate event_id
        API-->>RZP: 200 OK, ignored
    end
    APP->>API: GET /v1/wallet
    API-->>APP: New balance (animated in app)
```

---

## W4 — Hub morning operations

1. **T-1 21:15:** the procurement plan is ready. For each item: committed quantity + the forecast quantile of uncommitted demand (newsvendor rule). The hub manager reviews and adjusts it in the console, and purchase orders go to vendors (WhatsApp or PDF).
2. **T 04:00:** goods receipt. Weigh, grade (QC), create inventory lots with expiry, record vendor-score inputs.
3. **T 05:00:** packing tasks are generated per order and sorted by route stop. The packer scans the bag QR and picks each line by scan or weight. Shortages are flagged → the customer's substitution preference is applied, or a partial refund is issued.
4. Bags are grouped into crates per society, ready for 07:00 dispatch.

*Figure 7.8 — Sequence: procurement planning (forecast + newsvendor)*

```mermaid
sequenceDiagram
    autonumber
    participant DB as PostgreSQL
    participant W as outbox consumer
    participant ML as ml service
    participant OPS as api - ops
    actor HM as Hub manager
    participant V as Vendor
    DB-->>W: Outbox orders.locked complete for hub and T
    W->>ML: forecast_uncommitted(hub, T)
    ML->>DB: Read history, prices, festivals, weekday features
    ML->>ML: StatsForecast, LightGBM, Chronos-2 for new hubs
    ML->>ML: Reconcile hierarchy and conformal quantiles
    ML->>DB: intel.predictions kind forecast
    ML-->>W: intel.forecast_ready
    W->>OPS: build_procurement_plan(hub, T)
    OPS->>DB: Committed quantity per item from locked orders
    OPS->>OPS: order_qty = committed + quantile tau of uncommitted
    Note over OPS: tau = Cu divided by Cu plus Co, Co lowered by surplus salvage value
    OPS->>DB: procurement_plans and lines, outbox hub_ops.procurement_plan_ready
    HM->>OPS: Review and adjust in console
    OPS->>V: Purchase orders via WhatsApp or PDF
```

*Figure 7.9 — Sequence: packing and shortage substitution*

```mermaid
sequenceDiagram
    autonumber
    actor P as Packer
    participant APP as Mobile app - packer
    participant OPS as api - ops
    participant ORD as api - orders
    participant BIL as api - billing
    participant DB as PostgreSQL
    P->>APP: Open packing queue
    APP->>OPS: GET /v1/packing-tasks?status=ready
    OPS-->>APP: Tasks sorted by route stop
    P->>APP: Scan bag QR
    APP->>OPS: Start task (FOR UPDATE SKIP LOCKED)
    loop Each order line
        P->>APP: Scan or weigh item
        alt Enough stock
            APP->>OPS: Line packed
        else Short
            APP->>OPS: Report shortage quantity
            OPS->>ORD: Apply substitution preference
            alt Substitute available
                ORD->>DB: Line substituted, outbox orders.shortage_substituted
            else No substitute
                ORD->>BIL: Partial refund to wallet
                BIL->>DB: Journal entry unearned_revenue to wallet
            end
        end
    end
    P->>APP: Close bag
    APP->>OPS: Task done
    OPS->>ORD: Mark order PACKED
    ORD->>DB: Outbox orders.packed
```

*Figure 7.10 — Activity: hub morning operations*

```mermaid
flowchart TD
    A([T-1 21:15 procurement plan ready]) --> B[Hub manager reviews quantities]
    B --> C{Adjust any line?}
    C -- Yes --> D[Edit quantities, reason logged]
    C -- No --> E[Send purchase orders to vendors]
    D --> E
    E --> F[T 04:00 buy at APMC Vashi or FPO]
    F --> G[Goods receipt: weigh and grade]
    G --> H{Quality OK?}
    H -- No --> I[Reject lot, update vendor score]
    I --> J[Buy replacement or mark shortage]
    H -- Yes --> K[Create inventory lots with expiry]
    J --> K
    K --> L[T 05:00 generate packing tasks by route stop]
    L --> M[Pack bags, resolve shortages]
    M --> N[Group bags into crates per society]
    N --> O([Ready for 07:00 dispatch])
```

---

## W5 — Routing and delivery

1. **T 07:00:** the route solver (OR-Tools CVRPTW with pickup-and-delivery, OSRM travel times, 30-second limit) publishes routes. If the solver fails, the fallback is a nearest-neighbour route.
2. The rider sees stops, crates per stop and a map.
3. At each society: handover to the **Society Captain**. She scans the crate QR and counts bags; the handover is confirmed by OTP or photo.
4. The captain app lists flats. Each bag is scanned at the door or at lobby pickup → `DELIVERED` → push notification to the customer.
5. Exceptions: if the customer is not home, the captain holds the bag until the evening window. Otherwise it becomes `UNDELIVERED`, followed by redelivery or refund.
6. **Return leg:** crates and Kindness donation pickups are already part of the route as pickup stops.

*Figure 7.11 — Sequence: route solve, captain handover and delivery proof*

```mermaid
sequenceDiagram
    autonumber
    participant SCH as GitHub Actions schedule
    participant ML as ml service - routing
    participant OSRM as OSRM
    participant LOG as api - logistics
    actor R as Rider
    actor CP as Society Captain
    actor C as Customer
    participant DB as PostgreSQL
    SCH->>LOG: solve_routes(hub, T, slot) at 07:00
    LOG->>ML: POST /route/solve (stops, crates, time windows, pickups)
    ML->>OSRM: Travel time and distance matrix
    OSRM-->>ML: Matrix
    ML->>ML: OR-Tools CVRPTW with pickup and delivery, 30 s limit
    alt Solved
        ML-->>LOG: Routes with sequence and ETAs
    else Solver failed
        LOG->>LOG: Fallback nearest-neighbour route
    end
    LOG->>DB: routes, route_stops, outbox logistics.route_published
    R->>LOG: Start route, GPS pings every 15 s
    R->>CP: Arrive at society, hand over crates
    CP->>LOG: Scan crate QR, bag count, OTP or photo
    LOG->>DB: handovers row, orders AT_SOCIETY
    loop Each flat
        CP->>LOG: Scan bag at door or lobby pickup
        LOG->>DB: Delivery proof, orders DELIVERED
        DB-->>C: Push - delivered
    end
    opt Customer not reachable
        CP->>LOG: Hold until evening window
        LOG->>DB: UNDELIVERED if still unreachable
    end
    R->>LOG: Return-leg pickups (crates, donations)
    LOG->>DB: reverse_pickups done, outbox logistics.pickup_completed
```

---

## W6 — Tiffin day

1. Kitchens publish weekly menus; the hub manager approves them.
2. Cut-off at **T-1 21:00**, then `assign_kitchens` at 21:30 runs the fairness-aware CP-SAT assignment. Kitchens accept their counts.
3. At **T 09:35** `solve_tiffin_routes` plans each hub's lunch run (M7 §3.7): kitchens first, then the societies, on two-wheelers filled by portions.
4. Kitchens cook in the morning and mark READY by 11:30.
5. The rider scans each kitchen's `GB1A:` code (`POST /v1/stops/{id}/collect`): its ready tiffins go `packed → dispatched`. At a society with a captain she takes them by count and the handover code and proves each door with the tiffin's short code and the household's code (or a photo); where there is no captain the rider does the doors. A tiffin that does not reach its door is refunded in full at once; one never collected (or on a route nobody started) waits for the hub's triage at 16:00. The captain settles only the tiffins she took, and neither she nor the customer sees the rider's position while he is bound for a kitchen or near one. Delivery between 12:30 and 13:30.
6. A rating prompt goes out at 14:30.
7. Leftover portions are listed in the evening "Bachat Box".

*Figure 7.12 — Sequence: tiffin fair assignment and kitchen flow*

```mermaid
sequenceDiagram
    autonumber
    participant SCH as GitHub Actions schedule
    participant ML as ml service - assignment
    participant PTN as api - partners
    actor K as Kitchen partner
    actor R as Rider
    actor C as Customer
    participant DB as PostgreSQL
    SCH->>PTN: assign_kitchens(hub, T) at T-1 21:30
    PTN->>DB: Locked tiffin orders, kitchen capacity, ratings, 4-week earnings
    PTN->>ML: POST /assign/kitchens
    ML->>ML: CP-SAT - capacity, skills, distance, rating floor
    ML->>ML: Objective - distance + quality penalty + lambda times earnings inequality
    ML-->>PTN: Portions per kitchen
    PTN->>DB: assignments, outbox partners.assignment_created
    PTN-->>K: Push - 12 portions of Diet Thali for tomorrow
    alt Kitchen accepts
        K->>PTN: Accept
    else Kitchen declines
        K->>PTN: Decline
        PTN->>ML: Re-solve remaining portions
    end
    K->>PTN: Preparing, then READY by 11:30 (hygiene checklist)
    participant LOG as api - logistics
    R->>LOG: Collect - scan the kitchen's GB1A code
    LOG->>PTN: pickup(code) - the ready tiffins
    LOG->>DB: packed to dispatched (actor rider), attempts open
    R->>C: Captain by count and code, or the rider at the door, 12:30 to 13:30
    C->>LOG: Short code and delivery code - delivered
    PTN->>DB: Kitchen earning accrues in ledger on orders.delivered
    DB-->>C: Rating prompt at 14:30
```

---

## W7 — Partner onboarding and payouts

1. In the customer app, "Become a partner" → choose a role: kitchen, captain, rider or packer.
2. Upload documents: ID (masked), FSSAI registration for kitchens, PAN where applicable, bank verification by penny drop (simulated in the prototype).
3. The hub manager reviews documents in the console (decision written to the audit log).
4. Complete training modules (video + quiz): hygiene, app usage, role-specific.
5. VERIFIED → the new role appears in the role switcher.
6. Earnings accrue per event in the ledger → weekly payout statement (Razorpay Route / RazorpayX in test mode) → downloadable income certificate.

*Figure 7.13 — Activity: partner onboarding and KYC*

```mermaid
flowchart TD
    A([Become a partner]) --> B{Choose role}
    B --> C[Kitchen partner]
    B --> D[Society Captain]
    B --> E[Rider or packer]
    C --> F[Upload ID masked, bank, PAN, FSSAI registration]
    D --> G[Upload ID masked, bank, society confirmation]
    E --> H[Upload ID masked, bank, driving licence for riders]
    F --> I[Hub manager reviews documents]
    G --> I
    H --> I
    I --> J{Documents approved?}
    J -- Re-upload --> K[Partner fixes documents]
    K --> I
    J -- Rejected --> L([Application closed with reason])
    J -- Approved --> M[Training modules and quiz]
    M --> N{Passed?}
    N -- No --> M
    N -- Yes --> O[Status VERIFIED, role added]
    O --> P[Role switcher shows new role]
    P --> Q[Assignments, earnings in ledger]
    Q --> R([Weekly payout and income statement])
```

---

## W8 — Kindness donation

1. The customer schedules a pickup (items and photos).
2. The pickup is attached as a return-leg stop on the next route serving that society.
3. The hub verifies the items → Kindness points are credited (item rules × drive multiplier).
4. A batch is handed to an NGO with photo proof → the impact counter updates.

*Figure 7.14 — Sequence: Kindness donation to points*

```mermaid
sequenceDiagram
    autonumber
    actor C as Customer
    participant APP as Mobile app
    participant COM as api - community
    participant LOG as api - logistics
    actor R as Rider
    actor HM as Hub manager
    participant BIL as api - billing
    actor NGO as NGO coordinator
    C->>APP: Donate school bag and 3 kg clothes (photos)
    APP->>COM: POST /v1/donations
    COM->>LOG: Request return-leg pickup for society
    LOG-->>COM: Added to route stop on Friday
    R->>LOG: Pickup collected on return leg
    LOG-->>COM: logistics.pickup_completed
    HM->>COM: Verify items at hub
    alt Items usable
        COM->>COM: Points = item rules times drive multiplier
        COM->>BIL: Credit Kindness points (idempotency key donation id)
        BIL->>BIL: Journal entry kindness_expense to kindness customer
        COM-->>C: Push - 300 Kindness points added
    else Items unusable
        COM-->>C: Push - not accepted, with reason
    end
    NGO->>COM: Batch handoff with photo proof
    COM->>COM: Update impact counters
```

---

## W9 — Surplus cascade (daily, 12:30)

1. Unsold or near-expiry lots are listed as "Imperfect Sabzi" add-ons for the next day at a discount.
2. What remains goes to kitchens as ingredient credit (used in curries and dals).
3. What remains goes to an NGO partner.
4. Only unusable items go to compost.

Every step is recorded in `hub_ops.waste_logs.cascade_to`, which feeds the **kg wasted per 100 kg sold** KPI.

*Figure 7.15 — Activity: surplus cascade*

```mermaid
flowchart TD
    A([12:30 surplus_cascade job]) --> B[List unsold and near-expiry lots]
    B --> C{Shelf life left at least 24 h?}
    C -- Yes --> D[List as Imperfect Sabzi add-on at discount]
    D --> E{Sold by next cut-off?}
    E -- Yes --> F([Revenue recovered])
    E -- No --> G
    C -- No --> G{Usable for cooking today?}
    G -- Yes --> H[Send to kitchens as ingredient credit]
    H --> I([Logged cascade_to kitchen])
    G -- No --> J{Fit for donation?}
    J -- Yes --> K[NGO handoff]
    K --> L([Logged cascade_to ngo])
    J -- No --> M[Compost]
    M --> N([Logged cascade_to bin])
```

---

## W10 — Society onboarding and unlock

1. The hub draws the society polygon in the console → status `prospect`.
2. A Society Captain is recruited (through W7).
3. The unlock threshold **N** is computed from the route-cost model: the number of households at which a stop becomes cheaper than the delivery fee.
4. A referral campaign runs through the captain and WhatsApp groups.
5. When N households have joined → status `live`, free-delivery badge, fixed delivery days assigned.
6. Before that, once the hub assigns the society delivery days, households can already get boxes, paying the hub's pre-live delivery fee on each box (`pre_live_box_fee_paise`, default ₹30, set by an admin). The fee is debited with the box at cut-off and stops from the first cut-off after the society goes live (M9 §3.6, M4 §11).

*Figure 7.16 — Activity: society unlock*

```mermaid
flowchart TD
    A([Hub draws society polygon]) --> B[Status prospect]
    B --> C{Captain recruited?}
    C -- No --> D[Recruit through Become a partner and RWA]
    D --> C
    C -- Yes --> E[Compute unlock threshold N from route cost model]
    E --> F[Status unlocking, referral campaign]
    F --> G[Households join and onboard]
    G --> H{Joined households at least N?}
    H -- No --> I[Show progress bar to residents]
    I --> G
    H -- Yes --> J[Status live, free delivery badge]
    J --> K[Assign fixed delivery days and slot]
    K --> L([Society enters weekly rhythm])
```

---

## W11 — ML lifecycle

1. **02:00 daily:** `export_snapshots` reads PostgreSQL with DuckDB and writes Parquet to object storage (S3 API: Cloudflare R2 when deployed, SeaweedFS locally); pandera validates the schema. Snapshots and training run in `backend/training` on GitHub Actions, never inside the ML service or an API request (D21, D25).
2. Point-in-time features are built as of each cut-off (no leakage).
3. **Sunday 03:00:** a challenger model is trained and evaluated on a time-based holdout (last 2–4 weeks).
4. If it beats the current model on the primary metric and passes guardrails, the new version is promoted: its `latest.json` pointer on R2 moves to it (manual `just promote` in v1, D25).
5. The ML service polls `latest.json` every 10 minutes and hot-reloads.
6. A daily monitor compares forecast vs actual (WAPE), plan acceptance and waste %, and alerts on drift.

*Figure 7.17 — Sequence: training, evaluation, champion promotion and hot reload*

```mermaid
sequenceDiagram
    autonumber
    participant SCH as GitHub Actions schedule
    participant MW as backend/training job
    participant DB as PostgreSQL
    participant S3 as R2 data lake (S3 API)
    participant MLF as R2 model registry (latest.json)
    participant ML as ml service
    SCH->>MW: export_snapshots at 02:00
    MW->>DB: DuckDB postgres scan
    MW->>S3: Write Parquet snapshots
    SCH->>MW: retrain_models Sunday 03:00
    MW->>S3: Load snapshots, pandera validation
    MW->>MW: Build point-in-time features
    MW->>MW: Train challenger
    MW->>MW: Evaluate on last 2 to 4 weeks, per data_source
    MW->>MLF: Upload version, metrics, eval report
    MW->>MLF: Load current version's metrics
    alt Challenger better and guardrails pass
        MW->>MLF: Promote (just promote): latest.json points to the new version
    else Not better
        MW->>MLF: Keep the current version
    end
    loop Every 10 minutes
        ML->>MLF: Read latest.json
        alt New version
            ML->>S3: Download artifact
            ML->>ML: Hot reload
        end
    end
```

---

## W12 — Assistant (text MVP) with confirmation

The assistant ("Ghar Saathi" chat) uses Claude in the ML service, which never calls our API: the API sends it the scrubbed conversation and a trimmed week, the model reads that week and names the changes it wants, and the API turns them into **proposed changes** that the user must confirm in the UI (M13 §3). The LLM never calculates prices or quantities.

*Figure 7.18 — Sequence: assistant turn with user confirmation*

```mermaid
sequenceDiagram
    autonumber
    actor C as Customer
    participant APP as Mobile app - chat
    participant API as api - assistant
    participant AS as ml service - assistant
    participant LLM as Claude API
    C->>APP: Kal aloo 2 kilo extra bhejna aur paneer hata do
    APP->>API: POST /v1/assistant/messages (JSON in v0; SSE later)
    API->>API: Reserve the turn under the daily cap, scrub the text, store it, commit
    API->>AS: POST /assistant/turn (scrubbed history, trimmed week with line handles)
    AS->>LLM: System prompt and tools (cached), messages
    LLM-->>AS: Tool call get_my_week
    AS->>LLM: Tool result from the trimmed week
    LLM-->>AS: Tool calls edit_box_line - qty L4 (aloo), remove L7 (paneer)
    AS->>LLM: Tool results - noted, the app shows cards
    LLM-->>AS: Reply summarising the request
    AS-->>API: Reply and intents (no ids, no prices from the model)
    API->>API: Resolve intents - line, catalog product, steps, week - into proposals p7, p8
    API-->>APP: Message and confirm cards with prices from the API
    alt Customer confirms
        C->>APP: Confirm
        APP->>API: POST /v1/assistant/confirm/p7 (Idempotency-Key)
        API->>API: M3 facade at the card's version (412 if the week changed)
        API-->>APP: Box updated, version 5
    else Customer cancels
        C->>APP: Cancel
        APP->>API: POST /v1/assistant/proposals/p7/reject
    end
```

---

## Workflow-to-module map

| Workflow | Modules | Primary owner |
|---|---|---|
| W1 Onboarding | M1, M5, M9 | Prathamesh Relekar (backend), Yash Salunkhe (screens) |
| W2 Weekly Autopilot | M3, M4, M10, M11 | Prathamesh Relekar (backend, models), Yash Salunkhe (screens) |
| W3 Payments | M5 | Prathamesh Relekar (backend), Yash Salunkhe (screens) |
| W4 Hub morning ops | M6, M10 | Prathamesh Relekar |
| W5 Routing and delivery | M7, M10 | Prathamesh Relekar |
| W6 Tiffin day | M8, M7, M10 | Prathamesh Relekar |
| W7 Partner onboarding | M8, M1, M5 | Prathamesh Relekar |
| W8 Kindness | M9, M7, M5 | Prathamesh Relekar (backend), Yash Salunkhe (screens) |
| W9 Surplus cascade | M6 | Prathamesh Relekar |
| W10 Society unlock | M9, M7 | Prathamesh Relekar |
| W11 ML lifecycle | M10 | Prathamesh Relekar |
| W12 Assistant | M13, M3 | Prathamesh Relekar |

End-to-end tests (Playwright) for W1, W2, W3, W5 and W8 are owned by Rhythm Thakur and Manish Raje.

## Decisions & open questions

- **D1** Kitchen Autopilot and Society Captain are the headline flows ([ADR-0001](../adr/0001-headline-usps-autopilot-and-society-captain.md)).
- **D3** batch ML with rule-based fallbacks ([ADR-0003](../adr/0003-python-fastapi-backend-and-ml-service.md)).
- **D4** wallet default, UPI Autopay with 24-hour pre-debit notice ([ADR-0004](../adr/0004-closed-loop-wallet-and-upi-autopay.md)); for MS-A the wallet is topped up through Razorpay Checkout in test mode and Autopay mandates move to the pilot phase.
- **D21** jobs without a broker: Postgres outbox relayed after each commit, scheduled jobs as internal endpoints called by GitHub Actions; Taskiq returns at the pilot ([ADR-0021](../adr/0021-jobs-without-a-broker.md)).
- **D5 / D13** assistant: text MVP, model chosen after the Sprint 8 evaluation ([ADR-0005](../adr/0005-text-assistant-mvp-voice-stretch.md), [ADR-0013](../adr/0013-assistant-model-chosen-by-eval.md)).
- Open: validate the timings (T-1 21:00 cut-off, 04:00 buying, 08:00–10:00 delivery) with hub operators and the UX interviews in Phase P0.
- Open: how long the captain holds undelivered bags (evening window length) — decide in the M7 spec.

Related: [06 — Domain lifecycles](06-domain-lifecycles.md) · [08 — AI and ML](08-ai-and-ml.md) · [10 — API and integration](10-api-and-integration.md) · [14 — Project plan](14-project-plan.md)

# Chapter 05 — Data Architecture

> **In short:**
> - One **PostgreSQL 18** database is the single source of truth, with **one schema per module** (platform, identity, catalog, subscriptions, orders, billing, hub_ops, logistics, partners, community, intel, notifications, analytics, assistant).
> - Extensions: **PostGIS** (geo), **pgvector** (similarity), **pg_trgm + unaccent** (Hinglish fuzzy search), **pgcrypto** (field encryption). JSONB covers flexible attributes.
> - Money is always **integer paise (BIGINT)**; every rupee or Kindness point moves through a **double-entry ledger**.
> - Every household-derived row carries `data_source ∈ {synthetic, pilot, live}`, so models can train on mixed data while we report real-data accuracy separately.
> - A deterministic **household simulator** writes through the module services and keeps ground truth for honest ML evaluation.

---

## 5.1 Why PostgreSQL (summary of the analysis)

| Need | How it is covered |
|---|---|
| Atomic money and order flows (wallet debit + order lock + stock reservation) | ACID transactions in one database |
| Highly connected entities (user → household → society → hub → route) | Relational joins, foreign keys |
| Geo (society polygons, hub service areas, routing) | PostGIS `geography` |
| Flexible attributes (nutrition, recipe steps, KYC metadata) | JSONB, indexed with GIN |
| Hinglish search ("bhindi", "bhendi", "okra") | `pg_trgm` + full-text + synonym table |
| Substitutes, similar recipes | pgvector HNSW on bge-m3 embeddings |
| Franchise isolation | `hub_id` + Row-Level Security |
| Team collaboration | Schema + Alembic migrations act as a contract between four developers |

MongoDB, Firestore, Elasticsearch and Neo4j were evaluated and rejected (see D6, [ADR-0006](../adr/0006-postgresql-centric-data-platform.md)). Supporting stores: **Valkey** (short-lived data only: rate-limit counters, the sign-out deny-list, cache, live rider positions; the free Render Key Value is not durable, so it never holds the only copy of anything), **S3-compatible object storage** (media, data lake, MLflow artifacts; Cloudflare R2 when deployed, SeaweedFS locally; code uses the S3 API only) and **DuckDB + Parquet** (ML training and heavy analytics).

## 5.2 Global data rules

1. Primary keys are `uuidv7()` (built into PostgreSQL 18; time-ordered and index-friendly).
2. `created_at` / `updated_at` are `timestamptz`. Business dates are `date` in Asia/Kolkata.
3. Money is `BIGINT` paise. Quantities are `NUMERIC(10,3)` plus a `unit` enum (`kg`, `g`, `pc`, `bunch`, `ml`, `l`).
4. Every tenant-owned row has `hub_id`; every household-derived row has `data_source`.
5. **One schema per module.** A module writes only to its own schema. Other modules read through its `api.py` facade. Foreign keys are allowed only to stable identifiers (users, households, hubs, products, societies).
6. Soft delete only where the law needs history; personal data is hard-deleted on request (DPDP).
7. Migrations: one Alembic migration per pull request, named `YYYYMMDD_HHMM_<module>_<desc>`. CI enforces a single head and an upgrade/downgrade round trip.
8. Translatable names are JSONB `{en, hi, mr}`.

## 5.3 Schema map

| Schema | Owner module | Owner (backend, ADR-0023) | Purpose |
|---|---|---|---|
| `identity` | M1 | Prathamesh Relekar | Users, roles with scopes, households, members, addresses, consents |
| `catalog` | M2 | Prathamesh Relekar | Products, recipes, substitutions, prices, festival calendar |
| `subscriptions` | M3 | Prathamesh Relekar | Plans, subscriptions, delivery days, Budget Lock, weekly cycles, box lines |
| `orders` | M4 | Prathamesh Relekar | Orders, lines, events, slots |
| `billing` | M5 | Prathamesh Relekar | Ledger, payments, mandates, webhooks |
| `ops` | M6 | Prathamesh Relekar | Hubs, vendors, procurement, inventory lots, packing, waste |
| `logistics` | M7 | Prathamesh Relekar | Vehicles, routes, stops, handovers, deliveries, reverse pickups, GPS pings |
| `partners` | M8 | Prathamesh Relekar | Partners, KYC, capacity, assignments, earnings, trainings |
| `community` | M9 | Prathamesh Relekar | Societies, captains, referrals, donation drives, donations, NGO handoffs |
| `intel` | M10 | Prathamesh Relekar | Feature snapshots, predictions, experiments |
| `notify` | M11 | Prathamesh Relekar | Templates, messages, preferences |
| `platform` | M0 | shared | Outbox, idempotency keys, audit log |

## 5.4 Core cross-schema ERD

*Figure 5.1 — Core cross-schema entity relationships*

```mermaid
erDiagram
    USERS ||--o{ USER_ROLES : has
    USERS ||--o{ HOUSEHOLDS : owns
    HOUSEHOLDS ||--o{ HOUSEHOLD_MEMBERS : includes
    HOUSEHOLDS ||--o{ ADDRESSES : "lives at"
    SOCIETIES ||--o{ ADDRESSES : contains
    HUBS ||--o{ SOCIETIES : serves
    HOUSEHOLDS ||--o{ SUBSCRIPTIONS : holds
    HOUSEHOLDS ||--o{ WEEKLY_CYCLES : plans
    WEEKLY_CYCLES ||--o{ BOX_LINES : contains
    WEEKLY_CYCLES ||--o| ORDERS : "locks into"
    ORDERS ||--o{ ORDER_LINES : contains
    PRODUCTS ||--o{ ORDER_LINES : "sold as"
    PRODUCTS ||--o{ BOX_LINES : "planned as"
    ORDERS ||--o| DELIVERIES : "delivered by"
    ROUTES ||--o{ ROUTE_STOPS : visits
    SOCIETIES ||--o{ ROUTE_STOPS : "is stop"
    PARTNERS ||--o{ ASSIGNMENTS : receives
    USERS ||--o| PARTNERS : becomes
    USERS ||--o{ LEDGER_ACCOUNTS : owns
    JOURNAL_ENTRIES ||--o{ POSTINGS : contains
    LEDGER_ACCOUNTS ||--o{ POSTINGS : receives
```

## 5.5 Schemas in detail

### 5.5.1 identity

*Figure 5.2 — ERD: identity*

```mermaid
erDiagram
    USERS {
        uuid id PK
        text phone_e164 UK
        text name
        text locale
        text status
        timestamptz created_at
    }
    USER_ROLES {
        uuid id PK
        uuid user_id FK
        text role
        text scope_type
        uuid scope_id
        text status
        timestamptz verified_at
    }
    HOUSEHOLDS {
        uuid id PK
        uuid owner_user_id FK
        uuid hub_id FK
        uuid society_id FK
        int size
        text diet
        jsonb cuisine_tags
        int weekday_cook_minutes
        int weekend_cook_minutes
        smallint_array no_cook_days
        bigint budget_paise
        boolean autopilot_enabled
        boolean auto_approve
        text data_source
        int version
    }
    HOUSEHOLD_MEMBERS {
        uuid id PK
        uuid household_id FK
        text age_band
        text sex
        jsonb health_tags
        jsonb fasting_rules
    }
    ADDRESSES {
        uuid id PK
        uuid household_id FK
        uuid society_id FK
        text tower_id
        text flat
        geography location
        text h3_r9
    }
    CONSENTS {
        uuid id PK
        uuid user_id FK
        text purpose
        text version
        timestamptz granted_at
        timestamptz revoked_at
    }
    USERS ||--o{ USER_ROLES : has
    USERS ||--o{ HOUSEHOLDS : owns
    USERS ||--o{ CONSENTS : gives
    HOUSEHOLDS ||--o{ HOUSEHOLD_MEMBERS : includes
    HOUSEHOLDS ||--o{ ADDRESSES : has
```

Roles: `customer`, `captain` (scope: society), `kitchen_partner`, `rider`, `packer`, `hub_manager`, `franchise_owner` (scope: hub), `ngo_coordinator` (scope: NGO), `admin` (global). Role status: `applied → kyc_pending → verified → suspended`. `health_tags` are stored only after consent purpose `health_personalisation` is granted.

### 5.5.2 catalog

*Figure 5.3 — ERD: catalog*

```mermaid
erDiagram
    PRODUCTS {
        uuid id PK
        text kind
        jsonb names
        text unit
        int shelf_life_h
        jsonb nutrition
        jsonb tags
        vector embedding
        boolean active
    }
    RECIPES {
        uuid id PK
        jsonb names
        text cuisine
        int cook_minutes
        jsonb rules
        jsonb steps
        vector embedding
    }
    RECIPE_INGREDIENTS {
        uuid recipe_id FK
        uuid product_id FK
        numeric qty_per_serving
        boolean optional
    }
    SUBSTITUTIONS {
        uuid product_id FK
        uuid substitute_id FK
        numeric score
        text source
    }
    PRICE_LISTS {
        uuid id PK
        uuid hub_id FK
        date valid_from
    }
    PRICES {
        uuid price_list_id FK
        uuid product_id FK
        bigint price_paise
        bigint mrp_paise
    }
    SEARCH_TERMS {
        uuid product_id FK
        text term
        text lang
    }
    FESTIVAL_CALENDAR {
        date day PK
        text region
        text code
        jsonb rules
    }
    RECIPES ||--o{ RECIPE_INGREDIENTS : uses
    PRODUCTS ||--o{ RECIPE_INGREDIENTS : "is used in"
    PRODUCTS ||--o{ SUBSTITUTIONS : "has substitute"
    PRICE_LISTS ||--o{ PRICES : contains
    PRODUCTS ||--o{ PRICES : "priced in"
    PRODUCTS ||--o{ SEARCH_TERMS : "found by"
```

`kind` ∈ {veg, fruit, kit, tiffin, salad, staple}. `rules` on recipes encode Jain, fasting and diabetic suitability. `substitutions.source` ∈ {nutrition, cooccur, embedding, learned}.

### 5.5.3 subscriptions

*Figure 5.4 — ERD: subscriptions*

```mermaid
erDiagram
    PLANS {
        uuid id PK
        text code UK
        text kind
        bigint fee_paise
        jsonb features
    }
    SUBSCRIPTIONS {
        uuid id PK
        uuid household_id FK
        uuid plan_id FK
        text status
        timestamptz started_at
        date paused_until
        text payment_mode
    }
    SOCIETY_DELIVERY_DAYS {
        uuid society_id FK
        int weekday
        uuid slot_id FK
    }
    BUDGET_LOCKS {
        uuid id PK
        uuid household_id FK
        date month
        bigint budget_paise
        bigint spent_paise
    }
    WEEKLY_CYCLES {
        uuid id PK
        uuid household_id FK
        date week_start
        uuid plan_ref
        text status
        timestamptz generated_at
        timestamptz approved_at
        int version
    }
    BOX_LINES {
        uuid cycle_id FK
        uuid product_id FK
        numeric qty
        text unit
        text reason_code
        jsonb reason_text
        boolean locked
    }
    PLANS ||--o{ SUBSCRIPTIONS : "subscribed as"
    SUBSCRIPTIONS ||--o{ WEEKLY_CYCLES : generates
    WEEKLY_CYCLES ||--o{ BOX_LINES : contains
```

`payment_mode` ∈ {wallet, autopay}. `version` is used for optimistic locking when a customer edits a box.

### 5.5.4 orders

*Figure 5.5 — ERD: orders*

```mermaid
erDiagram
    ORDERS {
        uuid id PK
        uuid household_id FK
        uuid hub_id FK
        text kind
        uuid cycle_id FK
        date delivery_date
        uuid slot_id FK
        text status
        bigint subtotal_paise
        bigint fees_paise
        bigint discount_paise
        bigint total_paise
        int version
        text data_source
    }
    ORDER_LINES {
        uuid order_id FK
        uuid product_id FK
        numeric qty
        bigint unit_price_paise
        uuid substituted_from
        numeric shortage_qty
    }
    ORDER_EVENTS {
        uuid id PK
        uuid order_id FK
        text from_status
        text to_status
        text actor
        jsonb payload
        timestamptz at
    }
    SLOTS {
        uuid id PK
        uuid hub_id FK
        text label
        time start_time
        time end_time
        int capacity
    }
    ORDERS ||--o{ ORDER_LINES : contains
    ORDERS ||--o{ ORDER_EVENTS : records
    SLOTS ||--o{ ORDERS : schedules
```

`kind` ∈ {box, tiffin, addon}. Every status change writes an `order_events` row (full audit trail).

### 5.5.5 billing

*Figure 5.6 — ERD: billing*

```mermaid
erDiagram
    LEDGER_ACCOUNTS {
        uuid id PK
        text owner_type
        uuid owner_id
        text currency
        text kind
    }
    JOURNAL_ENTRIES {
        uuid id PK
        text kind
        text ref_type
        uuid ref_id
        text idempotency_key UK
        timestamptz created_at
    }
    POSTINGS {
        uuid entry_id FK
        uuid account_id FK
        bigint amount
    }
    PAYMENTS {
        uuid id PK
        uuid user_id FK
        text provider
        text provider_ref
        bigint amount_paise
        text status
        text purpose
    }
    MANDATES {
        uuid id PK
        uuid user_id FK
        text provider_ref
        bigint max_paise
        text status
    }
    PAYMENT_WEBHOOKS {
        uuid id PK
        text provider
        text event_id UK
        jsonb payload
        timestamptz processed_at
    }
    JOURNAL_ENTRIES ||--o{ POSTINGS : contains
    LEDGER_ACCOUNTS ||--o{ POSTINGS : receives
    PAYMENTS ||--o| JOURNAL_ENTRIES : "posted as"
```

`currency` ∈ {INR, KINDNESS_PTS}. A deferred constraint trigger checks that the postings of every journal entry sum to zero per currency.

### 5.5.6 ops

*Figure 5.7 — ERD: ops*

```mermaid
erDiagram
    HUBS {
        uuid id PK
        text name
        geography location
        geography service_area
        uuid franchise_owner_id FK
    }
    VENDORS {
        uuid id PK
        uuid hub_id FK
        text name
        text kind
    }
    VENDOR_SCORES {
        uuid vendor_id FK
        date week
        numeric freshness
        numeric price
        numeric reliability
        numeric defects
    }
    PROCUREMENT_PLANS {
        uuid id PK
        uuid hub_id FK
        date for_date
        text status
    }
    PROCUREMENT_LINES {
        uuid plan_id FK
        uuid product_id FK
        numeric committed_qty
        numeric forecast_q50
        numeric forecast_qtau
        numeric order_qty
        uuid vendor_id FK
        bigint unit_cost_paise
    }
    INVENTORY_LOTS {
        uuid id PK
        uuid hub_id FK
        uuid product_id FK
        numeric qty
        timestamptz received_at
        timestamptz expires_at
        bigint cost_paise
        text grade
    }
    PACKING_TASKS {
        uuid id PK
        uuid order_id FK
        uuid packer_id FK
        text status
        text bag_qr UK
        timestamptz started_at
        timestamptz done_at
    }
    WASTE_LOGS {
        uuid id PK
        uuid hub_id FK
        uuid product_id FK
        numeric qty
        text reason
        text cascade_to
    }
    HUBS ||--o{ VENDORS : sources
    VENDORS ||--o{ VENDOR_SCORES : "scored weekly"
    HUBS ||--o{ PROCUREMENT_PLANS : plans
    PROCUREMENT_PLANS ||--o{ PROCUREMENT_LINES : contains
    HUBS ||--o{ INVENTORY_LOTS : stocks
    HUBS ||--o{ WASTE_LOGS : records
```

`cascade_to` ∈ {imperfect, kitchen, ngo, bin} — this is how the zero-waste loop is measured.

### 5.5.7 logistics

*Figure 5.8 — ERD: logistics*

```mermaid
erDiagram
    VEHICLES {
        uuid id PK
        uuid hub_id FK
        text kind
        numeric capacity_kg
        int capacity_crates
    }
    ROUTES {
        uuid id PK
        uuid hub_id FK
        date route_date
        uuid slot_id FK
        uuid vehicle_id FK
        uuid rider_id FK
        text status
        jsonb solver_stats
    }
    ROUTE_STOPS {
        uuid route_id FK
        int seq
        uuid society_id FK
        timestamptz eta
        text kind
        jsonb payload
    }
    HANDOVERS {
        uuid id PK
        uuid route_id FK
        uuid captain_id FK
        int bags_count
        boolean otp_ok
        text photo_key
        timestamptz at
    }
    DELIVERIES {
        uuid order_id PK
        text status
        text proof_kind
        text proof_key
        timestamptz at
    }
    REVERSE_PICKUPS {
        uuid id PK
        text kind
        uuid ref_id
        uuid route_id FK
        text status
    }
    GPS_PINGS {
        uuid rider_id FK
        timestamptz at
        geography location
    }
    VEHICLES ||--o{ ROUTES : runs
    ROUTES ||--o{ ROUTE_STOPS : visits
    ROUTES ||--o{ HANDOVERS : "hands over"
    ROUTES ||--o{ REVERSE_PICKUPS : collects
```

`gps_pings` is partitioned by month (`PARTITION BY RANGE (at)`) with a BRIN index on `at`.

### 5.5.8 partners

*Figure 5.9 — ERD: partners*

```mermaid
erDiagram
    PARTNERS {
        uuid id PK
        uuid user_id FK
        text kind
        uuid hub_id FK
        text status
        numeric rating
        text fssai_reg_no
        text bank_ref_masked
    }
    KYC_DOCUMENTS {
        uuid id PK
        uuid partner_id FK
        text doc_type
        text file_key
        text status
        uuid reviewed_by FK
    }
    KITCHEN_CAPACITY {
        uuid partner_id FK
        date day
        text meal
        int max_portions
        jsonb menu_skills
    }
    ASSIGNMENTS {
        uuid id PK
        uuid partner_id FK
        text ref_type
        uuid ref_id
        date day
        int portions
        text status
    }
    EARNINGS {
        uuid id PK
        uuid partner_id FK
        text ref_type
        uuid ref_id
        bigint amount_paise
        date week
    }
    TRAININGS {
        uuid partner_id FK
        text module_code
        timestamptz completed_at
        numeric score
    }
    PARTNERS ||--o{ KYC_DOCUMENTS : submits
    PARTNERS ||--o{ KITCHEN_CAPACITY : declares
    PARTNERS ||--o{ ASSIGNMENTS : receives
    PARTNERS ||--o{ EARNINGS : accrues
    PARTNERS ||--o{ TRAININGS : completes
```

`kind` ∈ {kitchen, captain, rider, packer}. KYC numbers are encrypted at application level and only masked values are displayed. Aadhaar is never stored.

### 5.5.9 community

*Figure 5.10 — ERD: community*

```mermaid
erDiagram
    SOCIETIES {
        uuid id PK
        uuid hub_id FK
        text slug UK
        text name
        text area
        geography boundary
        jsonb towers
        int flats
        int unlock_threshold
        text status
    }
    CAPTAINS {
        uuid society_id FK
        uuid partner_id FK
        date since
    }
    REFERRALS {
        uuid id PK
        uuid referrer_user_id FK
        uuid referee_user_id FK
        text status
        uuid reward_entry_id FK
    }
    DONATION_DRIVES {
        uuid id PK
        text code UK
        date starts_on
        date ends_on
        numeric multiplier
    }
    DONATIONS {
        uuid id PK
        uuid user_id FK
        jsonb items
        text status
        uuid pickup_id FK
        int points_awarded
        uuid ngo_handoff_id FK
    }
    NGO_HANDOFFS {
        uuid id PK
        uuid ngo_id FK
        jsonb items
        text photo_key
        timestamptz at
    }
    SOCIETIES ||--o{ CAPTAINS : "led by"
    DONATION_DRIVES ||--o{ DONATIONS : collects
    NGO_HANDOFFS ||--o{ DONATIONS : distributes
```

Society `status` ∈ {prospect, unlocking, live, paused}.

### 5.5.10 intel

*Figure 5.11 — ERD: intel*

```mermaid
erDiagram
    FEATURE_SNAPSHOTS {
        uuid household_id FK
        timestamptz as_of
        jsonb features
    }
    PREDICTIONS {
        uuid id PK
        text kind
        uuid subject_id
        date for_date
        jsonb payload
        text model_name
        text model_version
        timestamptz created_at
    }
    EXPERIMENTS {
        uuid id PK
        text key
        text variant
        uuid subject_id
        timestamptz assigned_at
    }
    FEATURE_SNAPSHOTS ||--o{ PREDICTIONS : "feeds"
```

`feature_snapshots` are stored "as of" the cut-off time to prevent data leakage. `predictions.kind` ∈ {weekly_plan, pantry, basket_rank, forecast, route, assignment, slot_price, churn}, with `subject_type`, `subject_id`, `for_date` and `source` (ml or fallback). Each row records the exact model name and version. `model_metrics` keeps one row per metric, split and data source for every evaluation run.

### 5.5.11 notify and platform (summary)

- `notifications.templates(event, locale, channel, body)`, `notifications.messages(id, user_id, channel, status, sent_at)`, `notifications.preferences(user_id, channel, quiet_hours)`.
- `platform.outbox(id, event_type, payload, created_at, sent_at)`, `platform.idempotency_keys(key, user_id, request_hash, response, expires_at)`, `platform.audit_log(id, actor, action, target, diff, at)`.

## 5.6 Core domain model (UML class diagram)

*Figure 5.12 — UML class diagram: core domain*

```mermaid
classDiagram
    class Household {
        +UUID id
        +int size
        +Diet diet
        +Paise budget
        +bool autopilotEnabled
        +DataSource dataSource
        +approveWeek(cycleId)
    }
    class HouseholdMember {
        +AgeBand ageBand
        +HealthTags tags
        +FastingRules rules
    }
    class Subscription {
        +Status status
        +PaymentMode mode
        +pause(until)
        +resume()
    }
    class WeeklyCycle {
        +Date weekStart
        +CycleStatus status
        +int version
        +editLine(line, version)
        +approve()
        +skip()
    }
    class BoxLine {
        +Qty qty
        +ReasonCode reason
    }
    class Order {
        +OrderStatus status
        +Paise total
        +lock()
        +markPacked()
        +markDelivered(proof)
    }
    class OrderLine {
        +Qty qty
        +Paise unitPrice
        +Qty shortage
    }
    class Product {
        +Names names
        +Unit unit
        +Nutrition nutrition
    }
    class Recipe {
        +int cookMinutes
        +RecipeRules rules
    }
    class Society {
        +Polygon boundary
        +int unlockThreshold
        +SocietyStatus status
    }
    class Hub {
        +Polygon serviceArea
    }
    class Partner {
        +PartnerKind kind
        +PartnerStatus status
        +decimal rating
    }
    class Route {
        +Date date
        +RouteStatus status
        +publish()
    }
    class RouteStop {
        +int seq
        +StopKind kind
    }
    class LedgerAccount {
        +Currency currency
        +balance()
    }
    class JournalEntry {
        +string idempotencyKey
        +post(postings)
    }
    class Posting {
        +long amount
    }
    class Donation {
        +DonationStatus status
        +int points
        +verify(points)
    }
    Household "1" *-- "1..*" HouseholdMember
    Household "1" --> "0..*" Subscription
    Subscription "1" --> "0..*" WeeklyCycle
    WeeklyCycle "1" *-- "1..*" BoxLine
    WeeklyCycle "1" --> "0..1" Order
    Order "1" *-- "1..*" OrderLine
    OrderLine --> Product
    BoxLine --> Product
    Recipe --> "1..*" Product : ingredients
    Household --> Society
    Society --> Hub
    Hub "1" --> "0..*" Route
    Route "1" *-- "1..*" RouteStop
    RouteStop --> Society
    Partner --> Hub
    JournalEntry "1" *-- "2..*" Posting
    Posting --> LedgerAccount
    Donation --> Household
```

## 5.7 The ledger

### 5.7.1 Account kinds

| Account | Type | Currency | Owner |
|---|---|---|---|
| `wallet:<user>` | liability (we owe the customer) | INR | customer |
| `kindness:<user>` | liability (points) | KINDNESS_PTS | customer |
| `partner_payable:<partner>` | liability | INR | partner |
| `razorpay_clearing`, `payout_clearing` | asset / clearing | INR | platform |
| `unearned_revenue` | liability (boxes paid for, not yet delivered) | INR | platform |
| `sales_revenue` | revenue | INR | platform |
| `promo_expense`, `fulfilment_expense`, `kindness_expense` | expense | INR / PTS | platform |

### 5.7.2 Posting examples (amounts in paise)

| Event | Debit | Credit |
|---|---|---|
| Wallet top-up ₹1,000 | razorpay_clearing 100000 | wallet:U 100000 |
| Top-up bonus ₹100 | promo_expense 10000 | wallet:U 10000 |
| Box locked at cut-off, ₹450 | wallet:U 45000 | unearned_revenue 45000 |
| Box delivered | unearned_revenue 45000 | sales_revenue 45000 |
| Captain commission ₹6 | fulfilment_expense 600 | partner_payable:P 600 |
| Weekly partner payout | partner_payable:P | payout_clearing |
| Shortage refund ₹30 | unearned_revenue 3000 | wallet:U 3000 |
| Kindness credit, 300 points | kindness_expense 300 PTS | kindness:U 300 PTS |
| Redeem 200 points as ₹40 off | kindness:U 200 PTS; promo_expense 4000 | kindness_expense 200 PTS; unearned_revenue 4000 |

### 5.7.3 Invariants (property-tested with Hypothesis)

1. The postings of every journal entry sum to zero **per currency**.
2. A `wallet:` balance can never go below zero; the wallet balance can never exceed the ₹10,000 cap (closed-loop wallet rule, D4).
3. Replaying a request with the same `idempotency_key` never creates a second entry.
4. Balances are derived from postings; no code updates a balance column directly.

*Figure 5.13 — Money flow through the ledger*

```mermaid
flowchart LR
    RZP["Razorpay payment"] --> CLR["razorpay_clearing"]
    CLR -->|top-up| WAL["wallet:customer"]
    PROMO["promo_expense"] -->|bonus| WAL
    WAL -->|cut-off lock| UNE["unearned_revenue"]
    UNE -->|delivered| SALES["sales_revenue"]
    UNE -->|shortage refund| WAL
    FUL["fulfilment_expense"] -->|commission| PAY["partner_payable:partner"]
    PAY -->|weekly payout| POUT["payout_clearing"]
    KEXP["kindness_expense"] -->|donation verified| KIND["kindness:customer"]
    KIND -->|redeem| KEXP
```

## 5.8 Indexing and performance

| Index | Purpose |
|---|---|
| GiST on every `geography` column | "Which hub serves this point?", society containment |
| HNSW on `products.embedding`, `recipes.embedding` | Substitute and similar-recipe search |
| GIN trigram on `catalog.search_terms.term` | Fuzzy multilingual search |
| Partial index `orders(hub_id, delivery_date) WHERE status IN ('APPROVED','AWAITING_APPROVAL')` | Fast cut-off locking |
| BRIN on `logistics.gps_pings.at` | Cheap time-range scans on large append-only data |
| Unique `billing.journal_entries.idempotency_key`, `billing.payment_webhooks.event_id` | Exactly-once money effects |

Cut-off locks about 2,000 orders using batched `UPDATE … RETURNING` inside one job guarded by a PostgreSQL advisory lock. Target: under 60 seconds.

## 5.9 Multi-tenancy and security in data

- Every hub-scoped table has `hub_id`. Row-Level Security policies compare it with `current_setting('app.hub_ids')`, which the API sets with `SET LOCAL` per transaction for hub managers and franchise owners.
- KYC numbers are encrypted with pgcrypto using a key kept in Infisical and passed to the API as a secret; only masked forms are returned.
- No Aadhaar storage; health tags only with consent; `DELETE /me` hard-deletes personal data and keeps anonymised aggregates.
- The lake's nightly snapshots (`snapshots/date=<day>/`, M10 §6) hold no names, contacts, addresses or users; quasi-identifiers are coarsened (no society, only the hub; the budget as a band). Partitions are deleted after 180 days (proposed: a bucket lifecycle rule). An erased household is left out of every later snapshot and feature run; older partitions lose its random id as they age out.

## 5.10 The `data_source` tag

| Value | Origin | Used for |
|---|---|---|
| `synthetic` | Household simulator | Development, demos, model pre-training, ground-truth evaluation |
| `pilot` | Consented pilot households in Panvel | Real-data evaluation reported separately in the paper |
| `live` | Production users after launch | Future |

Training jobs take all sources; every evaluation report shows metrics per source so real-world performance is never hidden by simulated data (D8, [ADR-0008](../adr/0008-simulator-first-data-source-tagging.md)).

## 5.11 Household simulator

Owned by Prathamesh Relekar, reviewed by Rhythm Thakur and Manish Raje. Located in `data/simulator`, deterministic with `--seed`.

It generates:
1. Hubs and societies (real Panvel and Kharghar society names with hand-drawn polygons).
2. Households sampled from cohort distributions (size, diet, region, fasting practice, budget).
3. **Latent per-item consumption rates** — the ground truth the pantry digital twin must recover.
4. Weekly meal choices and accept / edit / skip behaviour.
5. Festivals and price shocks (for example, tomato +80% for three weeks).
6. Churn, partners with kitchen capacities, and donations.

It writes **through the module services**, so ledger and state-machine rules hold, with `data_source='synthetic'`. Ground truth is saved to `data/sim_truth/*.parquet` for evaluation.

**Status (v0, Oct 2026):**
- **Done:** items 2–4, through `just sim`.
  - Households from `data/simulator/cohorts.json` are onboarded through M1 in the seed societies.
  - The truth files hold latent daily-use rates and tastes, weekly CP-SAT plans with accept/edit/skip, and consumption.
- **Next:**
  - item 1 (real society polygons) and items 5–6;
  - writing the weeks through M3/M4 once they exist.
- See M10 §3.2 and `data/simulator/README.md`.

*Figure 5.14 — Simulator data flow*

```mermaid
flowchart LR
    SEED["Seed and cohort configs"] --> GEN["Simulator agents: households, partners, prices"]
    GEN -->|module services| DB[("PostgreSQL, data_source = synthetic")]
    GEN -->|ground truth| TRUTH[("sim_truth parquet")]
    DB --> SNAP["Nightly snapshot export"]
    SNAP --> TRAIN["Model training"]
    TRUTH --> EVAL["Evaluation harness"]
    TRAIN --> EVAL
```

## Decisions & open questions

- **D6** Postgres-centric database ([ADR-0006](../adr/0006-postgresql-centric-data-platform.md)).
- **D20** Neon Postgres (Singapore) as one shared `dev`/`staging` database, with no per-PR branches; day-to-day work uses local Docker Postgres; files on Cloudflare R2 ([ADR-0020](../adr/0020-free-tier-hosting-render-vercel-neon-r2.md), supersedes ADR-0011 and ADR-0015).
- **D4** Closed-loop wallet and KYC scope ([ADR-0004](../adr/0004-closed-loop-wallet-and-upi-autopay.md)).
- **D8** `data_source` tagging and simulator-first data ([ADR-0008](../adr/0008-simulator-first-data-source-tagging.md)).
- Open (Q3): Neon has no India region. Before the pilot (Phase P6), decide whether the pilot database stays on Neon Singapore or moves to a paid Postgres in Mumbai.

Related: [04 — System architecture](04-system-architecture.md) · [06 — Domain lifecycles](06-domain-lifecycles.md) · [08 — AI and ML](08-ai-and-ml.md) · [13 — Security and compliance](13-security-and-compliance.md)

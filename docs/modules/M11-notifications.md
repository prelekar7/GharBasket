# M11 — Notifications

**Status:** v1.2. §3.1 (inbox, preferences, push token, event map) frozen on 2026-10-08; §3.2 (templates, delivery log, WhatsApp and its webhook) refined for B5, frozen on approval.
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje (also template copy and translations)
**Phase / Sprint:** P2 / S3
**Postgres schema:** `notifications` · **Python package:** `gharbasket.modules.notifications`

## 1. Purpose
Turns domain events into the right message, in the right language, on the right channel: an in-app inbox (always), push (FCM) and WhatsApp (Cloud API, test number in the prototype, off until set up). Owns templates per event × channel × locale, preferences, quiet hours, messages and their delivery attempts. Contains no business decisions: it only reacts to events. When something must happen at a time of day (the box-ready notice, the cut-off reminder), the module that owns the data runs the job and publishes an event; M11 turns that event into a message.

## 2. User stories
| # | Story | Acceptance criteria |
|---|---|---|
| US-1 | As a customer, I want a notification when my week's box is ready so that I review it in time. | Sent at T-3 18:30 in my locale; deep link to Box. |
| US-2 | As a customer, I want a reminder before cut-off if I haven't approved so that I don't miss the week. | Sent at T-1 19:00 only to cycles still awaiting approval. |
| US-3 | As a customer, I want delivery updates so that I know when my bag reaches my captain and my door. | Pushes on `at_society` and `delivered`. |
| US-4 | As a customer, I want quiet hours so that I'm not disturbed at night. | No pushes 22:00–07:00 except delivery-critical; held pushes go out at the end of quiet hours. |
| US-5 | As a customer, I want to choose channels (push/WhatsApp) so that I control communication. | Preferences per category and channel; WhatsApp only after the WhatsApp consent. |
| US-6 | As a partner, I want assignment and route notifications so that I start on time. | Sent on `assignment_created` and `route_published` (arrive with M7/M8). |
| US-7 | As a support person, I want a delivery log so that I can see what a user received. | Messages and delivery attempts stored with status. |

## 3. API endpoints
| Method | Path | Permission / role | Idempotency | Notes |
|---|---|---|---|---|
| GET | `/v1/notifications` | Authenticated | — | The caller's inbox, newest first, cursor pagination (§3.1) |
| POST | `/v1/notifications/{id}/read` | Authenticated | Naturally idempotent | 204 |
| POST | `/v1/notifications/read-all` | Authenticated | Naturally idempotent | 204 |
| GET / PUT | `/v1/notifications/preferences` | Authenticated | — | Categories × channels and quiet hours (§3.1) |
| PUT | `/v1/me/push-token` | Authenticated | — | **Owned by M1** (the token lives on the session row); replaces the old `/v1/devices/push-token` |
| GET / PUT | `/v1/admin/notification-templates` | admin (`templates:write`) | — | Versioned templates with a preview per locale (§3.2) |
| GET | `/v1/admin/notifications?user_id=` | admin, hub_manager (support) | — | Delivery log: messages with their delivery attempts (§3.2) |
| GET / POST | `/v1/webhooks/whatsapp` | Meta (verify token / signature) | Event id dedupe | Subscription handshake and delivery receipts (§3.2) |

### 3.1 Inbox, preferences and push token contract (frozen on 2026-10-08)
The mobile Inbox and Notification preferences screens (`feat/mobile-notifications`) and the backend build against this. Changing it needs a PR reviewed by both Prathamesh and Yash.

```
Notification : { "id", "event_type": "orders.at_society", "category": "delivery",
                 "title": "Your box is with Meena tai", "body": "She brings it to B-402 by 10 AM.",
                 "link": "/orders" | null,           an app route, never a URL with a token
                 "created_at", "read_at": "…" | null }

GET  /v1/notifications?cursor&limit=20      (limit 1–100)
  200 { "items": [Notification], "unread": 3, "next_cursor": "…" | null }
      newest first; `unread` counts the whole inbox, not the page; title and body are rendered
      now, in the request language (Accept-Language: en / hi / mr, falling back to en), so a
      language switch re-renders old messages; Cache-Control: no-store
POST /v1/notifications/{id}/read    204; the first read_at is kept; someone else's id → 404
POST /v1/notifications/read-all     204; marks everything up to now as read

Preferences : { "categories": [ { "category": "weekly_box", "push": true, "whatsapp": false }, … ],
                "quiet_start": "22:00", "quiet_end": "07:00" }        IST clock times
GET  /v1/notifications/preferences   200 Preferences, always all five categories in this order:
                                     weekly_box, delivery, money, kindness, offers
PUT  /v1/notifications/preferences   body Preferences; 200 the saved Preferences
                                     categories left out keep their value; quiet_start/quiet_end
                                     are optional (the app shows them read-only today)

PUT  /v1/me/push-token    (M1)   { "push_token": "<FCM registration token>" | null }   → 204
                                 sets or clears the token of the calling session (device); the
                                 same token on another session of this user is cleared there
```

**Defaults.** Push on for every category except `offers`; WhatsApp off everywhere; quiet hours 22:00–07:00. A user who never saved preferences has no rows; the defaults are applied when reading.

**Rules**
- **The inbox always gets the message.** Preferences only decide push and WhatsApp. `offers` is the only category whose inbox messages also need the M1 `marketing` consent (no offers are sent in v1).
- **Account messages** (category `account`: your data export is ready; later, sign-in from a new device) are always in the inbox and pushed. They are not listed in the preferences and cannot be turned off.
- **WhatsApp needs consent.** Saving `whatsapp: true` for any category needs the M1 consent purpose `whatsapp` (new, version `v1`); without it the PUT is refused with 403 `notifications.whatsapp_consent_required`, so the app asks for the consent first. Revoking the consent stops WhatsApp at once (checked at send time); the switches are kept.
- **Quiet hours.** A push or WhatsApp message that falls inside the quiet window is held until `quiet_end` (IST) and then sent. Delivery-critical events skip quiet hours: `orders.at_society`, `orders.delivered`, `orders.delivery_failed`, `orders.payment_failed` (its grace period ends at 23:00). Both times are `HH:MM` in 15-minute steps and must differ, else 422 `notifications.quiet_hours_invalid`.
- **Once per event.** One message per (event, recipient) and one delivery per (message, channel), however often the outbox delivers the event. Push goes to every signed-in device of the user that has a token.
- **Push content.** Title and body in the user's saved locale (M1 `locale`), plus `{ link, message_id }` as data. No push carries a link with a token or a document; the data export push only opens the screen.
- **Retention.** Messages are kept 180 days, then deleted by `purge_notifications`. They are part of the data export (M1 §3.3, section `notifications`: event type, category, rendered English text, dates, delivery status per channel; no push tokens).

**Event map** (what produces a message in v1; recipient = the household owner for household events, `user_id` for billing events)

| Event | Category | Link | Push | Notes |
|---|---|---|---|---|
| `subscriptions.box_ready` (new, M3 `send_box_ready_notices`, T-3 18:30) | weekly_box | `/box` | yes | The pre-debit notice: M3 sets `notice_sent_at` when it publishes |
| `subscriptions.cutoff_reminder` (new, M3 `remind_unapproved`, T-1 19:00) | weekly_box | `/week` | yes | Only cycles still `awaiting_approval` and not set to auto-approve |
| `subscriptions.box_approved` with `auto_approved: true` (M3 adds the field) | weekly_box | `/box` | yes | Approvals the customer made themselves send nothing |
| `orders.locked` | money | `/orders` | yes | "₹690 paid from your wallet" |
| `orders.payment_failed` | money | `/wallet` | yes, skips quiet hours | Top up before `grace_until` |
| `orders.cancelled` with `reason: payment` | money | `/week` | yes | Other reasons are the customer's own action: nothing |
| `orders.dispatched` | delivery | `/orders` | yes | |
| `orders.at_society` | delivery | `/orders` | yes, skips quiet hours | |
| `orders.delivered` | delivery | `/orders` | yes, skips quiet hours | |
| `orders.delivery_failed` | delivery | `/orders` | yes, skips quiet hours | |
| `orders.shortage_substituted` | delivery | `/orders` | yes | Says what changed and the refund |
| `orders.issue_resolved` | money | `/orders` | yes | Credited or rejected; `issue_reported` sends nothing |
| `billing.wallet_topped_up` | money | `/wallet` | yes | |
| `identity.data_export_ready` (new, M1 `build_data_export`) | account | `/profile` | yes | Never carries the download link |

Arrive with their modules, same rules: `billing.points_credited` (kindness), `billing.refund_issued` (money), `community.donation_verified` and `community.society_live` (kindness), `logistics.route_published`, `partners.assignment_created`, `partners.verified` (partner apps). `subscriptions.cycle_generated` sends nothing: the cycle is still a draft until `box_ready`.

Partner messages (added 2026-10-10 with the first two) are category `account`: always in the inbox and pushed, not a preference. They go to the event's `user_id` and open that partner kind's home in the app.

| Event | Category | Link | Push | Notes |
|---|---|---|---|---|
| `partners.verified` | account | `/kitchen`, `/captain`, `/rider` or `/packer` | yes | One text per `kind`. A captain hears again on `captain_assigned` |
| `community.captain_assigned` (M8 §10, M9 §3.2) | account | `/captain` | yes | To the captain; names no society or flat |

**Error codes**

| Status | Code | When |
|---|---|---|
| 404 | `notifications.not_found` | A message id that isn't the caller's |
| 422 | `notifications.category_unknown` | A category that isn't one of the five |
| 403 | `notifications.whatsapp_consent_required` | `whatsapp: true` without the M1 `whatsapp` consent |
| 422 | `notifications.quiet_hours_invalid` | Not `HH:MM` in 15-minute steps, or start = end |
| 422 | `request.invalid_cursor` | A cursor that doesn't decode (M0) |

| Mock today (`mocks/notifications.js`) | Contract |
|---|---|
| Items have no `category` | Items carry `category` (the app may ignore it) |
| The weekly box message uses `subscriptions.cycle_generated` | It is `subscriptions.box_ready` (T-3 18:30); same text and link `/box` |
| No "mark all read" | `POST /v1/notifications/read-all` |
| `PUT` ignores quiet hours | `PUT` saves them when sent (optional) |
| `whatsapp: true` is always accepted | 403 `notifications.whatsapp_consent_required` until the WhatsApp consent is granted |
| Sign-in always sends `push_token: null` | The app registers with FCM after sign-in and calls `PUT /v1/me/push-token`; again on every token refresh, and with `null` when notifications are turned off on the phone |

### 3.2 Templates, delivery log and WhatsApp (frozen on approval)
The console's Templates editor and delivery log build against this; Meta calls the webhook.

```
Template : { "id", "kind": "orders.locked", "event_type": "orders.locked", "channel": "app" | "whatsapp",
             "locale": "en-IN", "version": 2, "title": "Your box for {day} is confirmed",
             "body": "{total} was paid from your wallet.", "wa_template_name": null | "box_confirmed",
             "active": true, "created_by": "…" | null, "created_at" }
Kind     : { "kind", "event_type", "category", "placeholders": ["total", "day"] }

GET  /v1/admin/notification-templates?kind&channel&locale&history=false        templates:write
  200 { "kinds": [Kind], "items": [Template] }   the active versions (every version with
      history=true) by kind, channel, locale, newest first; `kinds` is every kind the editor can fill
PUT  /v1/admin/notification-templates                                          templates:write
  { "kind", "channel", "wa_template_name": null | "box_confirmed",
    "texts": [ { "locale": "en-IN", "title", "body" }, { "locale": "hi-IN", … }, { "locale": "mr-IN", … } ] }
  200 { "items": [Template ×3] }  a new version (the kind and channel's highest + 1) per locale, made
      active; old versions stay, inactive, for the delivery log; audit-logged
      (`notifications.template_saved`, before and after)
POST /v1/admin/notification-templates/preview    the PUT body; nothing is saved  templates:write
  200 { "items": [ { "locale", "title", "body", "params": ["₹690", "Tuesday"] } ] }  rendered with
      sample values (₹690, Tuesday, 9 PM); `params` are the body's values in order (WhatsApp's {{1}}, {{2}} …)
```
A PUT or preview is refused with 422 `notifications.template_invalid` (`errors` lists each reason) when
the kind or channel is unknown; `texts` is not en-IN, hi-IN and mr-IN once each; a title or body uses a
placeholder the kind does not offer (`kinds.py`), a format spec or a stray brace; an `app` title is
empty; or `wa_template_name` (Meta's name, `[a-z0-9_]{1,100}`) is missing on `whatsapp` or set on `app`.

```
LogEntry : Notification (§3.1) + { "event_id", "kind",
           "deliveries": [ { "channel": "push" | "whatsapp", "status", "attempts", "last_error",
                             "provider_ref", "next_attempt_at", "sent_at" } ] }
GET /v1/admin/notifications?user_id&cursor&limit=20                    notifications:support
  200 { "items": [LogEntry], "next_cursor" }   newest first, rendered in the request language;
      a hub_manager sees only people whose household their hub serves (else 403 auth.forbidden)
```

**WhatsApp** (Cloud API) is off until `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`
and `WHATSAPP_VERIFY_TOKEN` are all set; until then WhatsApp deliveries are `skipped` with the reason.
Only Meta-approved template messages are sent: a `whatsapp` template row names the approved template
(`wa_template_name`) and its body mirrors that template's text, so its placeholders in order are the
`{{1}}`, `{{2}}` … parameters, formatted in the row's locale (Meta language `en`, `hi`, `mr`). A kind with
no active `whatsapp` row, or a person without the `whatsapp` consent (checked again at send time) or a
phone, is `skipped`. Errors map like FCM: 429, 5xx and Meta's throttling codes are retried after 1, 5 and
30 minutes, anything else is `failed`. Meta's message id (`wamid…`) is the delivery's `provider_ref`.

```
GET  /v1/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token&hub.challenge   Meta's handshake
  200 text/plain: the challenge · 403 notifications.bad_verify_token
POST /v1/webhooks/whatsapp     X-Hub-Signature-256: sha256=<hex HMAC-SHA256(app secret, raw body)>
  200 { "outcomes": ["applied" | "duplicate" | "unchanged" | "unknown_delivery"] }   one per status update
      in entry[].changes[].value.statuses[] · 401 notifications.bad_signature · 400 request.invalid
```
Each status update is stored once in `notifications.webhook_events` under the event id `<wamid>:<status>`
(a repeat is `duplicate`), then applied to the delivery whose `provider_ref` is the wamid: `delivered` and
`read` move it forward (sent → delivered → read, never back; anything else is `unchanged`); `failed` marks it `failed` with
Meta's error code and title unless it was already delivered. Incoming customer messages are ignored and
not stored. Each endpoint answers 503 `notifications.whatsapp_unavailable` while its secret
(`WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`) is unset. Webhook events are purged after 180 days.

| Status | Code | When |
|---|---|---|
| 422 | `notifications.template_invalid` | A template PUT or preview breaks a rule above |
| 401 | `notifications.bad_signature` | `X-Hub-Signature-256` does not match the raw body |
| 403 | `notifications.bad_verify_token` | The handshake's mode or verify token is wrong |
| 503 | `notifications.whatsapp_unavailable` | The webhook's secret is not configured |

## 4. Tables owned
| Table | Key columns |
|---|---|
| `notifications.templates` | id, event_type, channel (`app` for inbox and push, `whatsapp`), locale, version, title, body, wa_template_name, active, created_by, created_at; one active row per (event_type, channel, locale) |
| `notifications.messages` | id (uuidv7), user_id, event_id, event_type, category, params jsonb (only the event's declared placeholders), link, created_at, read_at; unique (event_id, user_id) |
| `notifications.deliveries` | id, message_id, channel (`push`, `whatsapp`), status (`queued`, `held`, `sent`, `delivered`, `read`, `failed`, `skipped`; delivered and read are WhatsApp receipts), attempts, next_attempt_at, last_error, provider_ref, sent_at; unique (message_id, channel) |
| `notifications.preferences` | user_id, category, push, whatsapp; primary key (user_id, category); rows only for saved choices |
| `notifications.quiet_hours` | user_id (primary key), start_time, end_time |
| `notifications.webhook_events` | id, provider, provider_event_id (unique), payload jsonb (the one status update), outcome, received_at; purged after 180 days |

Push tokens are not stored here: they stay on M1's `identity.sessions.push_token` and are read through `identity.api`.

## 5. Events
**Published:** none (terminal consumer).
**Consumed:** the event map in §3.1. Facades used: `identity.api` (`owner_of_household`, `household_hub`, `user_locale`, `user_phone`, `active_push_tokens`, `clear_push_token`, `has_consent`).

## 6. Jobs
| Job | Trigger | Notes |
|---|---|---|
| Outbox consumers (one per mapped event) | Per event (ADR-0021) | Write the message and its deliveries only; no network calls inside the consumer, because a failed event is retried as a whole |
| `send_deliveries` | Every minute (cron-job.org → `POST /v1/internal/jobs/send_deliveries`) | Sends due `queued`/`held` deliveries in batches: pushes through FCM, WhatsApp through the Cloud API (§3.2); FCM `UNREGISTERED` clears that token in M1; retries after 1, 5 and 30 minutes, then `failed` |
| `purge_notifications` | Daily 04:33 IST | Deletes messages older than 180 days with their deliveries, and webhook events as old |

The time-of-day notices are M3's jobs (`send_box_ready_notices` T-3 18:30, `remind_unapproved` T-1 19:00), which publish `subscriptions.box_ready` and `subscriptions.cutoff_reminder`.

## 7. Permissions
`notifications:read` own (any signed-in user); `templates:write` admin; `notifications:support` admin and hub_manager (delivery log).

## 8. Screens
**Mobile:** Inbox, Notification preferences (Yash, mocked on `feat/mobile-notifications`); FCM registration after sign-in. **Console:** Templates editor with preview per locale, delivery log search.

## 9. Tests required
- Unit: rendering per locale with fallback; placeholders checked against the event's list; quiet-hours window across midnight.
- Integration: a duplicate event creates one message and one delivery per channel; a consumer never makes a network call; quiet hours hold a push and `send_deliveries` sends it at `quiet_end`; delivery-critical events skip quiet hours; FCM success, `UNREGISTERED` (token cleared) and 5xx (retried, then `failed`) with an `httpx2.MockTransport`; WhatsApp only with preference, consent and the setting all on; inbox reads are own-only; unknown category 422; WhatsApp without consent 403.
- Content review: every template exists in en-IN, hi-IN, mr-IN before release (Rhythm, Manish).

## 10. Dependencies
M0 (outbox, jobs, pagination), M1 (users, locale, sessions with push tokens, consents), M3 (time-of-day notices), and the events of most modules. External: Firebase Cloud Messaging HTTP v1 (service-account key, `FCM_SERVICE_ACCOUNT`), WhatsApp Cloud API (`WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`; off until all are set).

## 11. Open questions
- WhatsApp template approval timelines for real (non-test) numbers after the pilot.
- ~~Whether SMS is needed at all in v1~~ Decided: no SMS in v1.
- ~~Templates in Jinja2?~~ Decided 2026-10-08: plain `{placeholders}`, no template engine.
- ~~Where push tokens live~~ Decided 2026-10-08: on `identity.sessions`; M11 has no devices table.

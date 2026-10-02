# M11 — Notifications

**Status:** Draft v1 — refine at phase start (spec-first)
**Owner:** Yash Salunkhe · **Reviewer:** Prathamesh Relekar · **QA:** Rhythm Thakur, Manish Raje (also template copy and translations)
**Phase / Sprint:** P2 / S3
**Postgres schema:** `notify` · **Python package:** `gharbasket.modules.notify`

## 1. Purpose
Turns domain events into the right message, in the right language, on the right channel: push (FCM), WhatsApp (Cloud API, test number in the prototype) and an in-app inbox. Owns templates per event × locale × channel, user preferences, quiet hours, device push tokens and delivery logs. Contains no business decisions — it only reacts to events.

## 2. User stories
| # | Story | Acceptance criteria |
|---|---|---|
| US-1 | As a customer, I want a notification when my week's box is ready so that I review it in time. | Sent at T-3 18:30 in my locale; deep link to Box. |
| US-2 | As a customer, I want a reminder before cut-off if I haven't approved so that I don't miss the week. | Sent at T-1 19:00 only to unapproved cycles. |
| US-3 | As a customer, I want delivery updates so that I know when my bag reaches my captain and my door. | Pushes on `at_society` and `delivered`. |
| US-4 | As a customer, I want quiet hours so that I'm not disturbed at night. | No pushes 22:00–07:00 except delivery-critical. |
| US-5 | As a customer, I want to choose channels (push/WhatsApp) so that I control communication. | Preferences per category and channel. |
| US-6 | As a partner, I want assignment and route notifications so that I start on time. | Sent on `assignment_created` and `route_published`. |
| US-7 | As a support person, I want a delivery log so that I can see what a user received. | Messages stored with status. |

## 3. API endpoints
| Method | Path | Permission / role | Idempotency | Notes |
|---|---|---|---|---|
| GET | `/v1/notifications` | Authenticated | — | In-app inbox, cursor pagination |
| POST | `/v1/notifications/{id}/read` | Authenticated | — | |
| GET / PUT | `/v1/notifications/preferences` | Authenticated | — | |
| POST / DELETE | `/v1/devices/push-token` | Authenticated | — | FCM token registration |
| GET / PUT | `/v1/admin/notification-templates` | admin | — | Versioned templates |
| POST | `/v1/webhooks/whatsapp` | Meta (signature) | Event id dedupe | Delivery receipts |

## 4. Tables owned
| Table | Key columns |
|---|---|
| `notify.templates` | id, event_type, channel, locale, version, body (Jinja2), active |
| `notify.messages` | id, user_id, event_id, channel, template_id, status, sent_at, read_at |
| `notify.preferences` | user_id, category, channel, enabled, quiet_start, quiet_end |
| `notify.devices` | user_id, platform, fcm_token, updated_at |

## 5. Events
**Published:** none (terminal consumer).
**Consumed:** `subscr.cycle_generated`, `subscr.box_approved`, `orders.*` (status), `billing.wallet_topped_up`, `billing.payment_failed`, `billing.refund_issued`, `billing.points_credited`, `logistics.route_published`, `partners.assignment_created`, `partners.verified`, `community.donation_verified`, `community.society_live`.

## 6. Jobs
| Job | Trigger | Notes |
|---|---|---|
| `send_notification` | Per event (Taskiq) | Deduplicated per (event_id, user, channel) |
| `retry_failed` | Every 10 min | Exponential backoff, max 3 |

## 7. Permissions
`notifications:read` own; `templates:write` admin.

## 8. Screens
**Mobile:** Inbox, Notification preferences. **Console:** Templates editor with preview per locale, delivery log search.

## 9. Tests required
- Unit: template rendering per locale with fallback; quiet-hours logic.
- Integration: duplicate event delivery sends once; FCM and WhatsApp clients mocked with respx.
- Content review: every template exists in en-IN, hi-IN, mr-IN before release.

## 10. Dependencies
M0, M1 (users, locale), consumes events from most modules. External: Firebase Cloud Messaging, WhatsApp Cloud API.

## 11. Open questions
- WhatsApp template approval timelines for real (non-test) numbers after the pilot.
- Whether SMS is needed at all in v1 (proposed: no).

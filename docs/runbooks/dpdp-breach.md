# Runbook: a personal data breach (DPDP Act 2023)

Owner: Prathamesh Relekar. Last reviewed: 11 November 2026.

This is the breach process named in handbook 13 §13.6: contain, assess, notify the Data Protection Board and affected
users as required. It is a working procedure, not legal advice. The Digital Personal Data Protection Act 2023 makes
GharBasket a data fiduciary. When personal data is breached, it must tell the Data Protection Board of India and each
affected person, in the form and within the time the DPDP Rules 2025 set. **This runbook states no deadline.** The
handbook names none, and a wrong number would do harm. Check the current DPDP Rules before the notice goes out, and
confirm the wording with the guide, Prof. Kranti Bade, or a CA (handbook 13, "Open").

A **personal data breach** is any unauthorised processing, or accidental disclosure, loss, destruction or change of
personal data, that puts its confidentiality, integrity or availability at risk. A leaked secret, a public bucket, a wrong
export sent to another person, a stolen laptop with a database dump, a lost backup file and an attacker in the database
are all breaches until shown otherwise.

**On staging.** Staging holds synthetic and consented test data (handbook 13 §13.6, "Academic data"). A breach there is
still handled with this runbook, as practice and as a lesson, but the duty to notify the Board and people arises when real
people's data is involved. If you are unsure whether a row belongs to a real person, treat it as real until you know
(`data_source` tags on the rows help).

## Symptoms

- A report from a user, a partner, a hub manager, a researcher or a platform (Neon, Render, Cloudflare, Razorpay, Firebase)
  that data was seen by someone who should not have seen it.
- A secret was published (a commit, a log, a chat): see [`rotate-secrets.md`](rotate-secrets.md).
- Unexpected reads: a burst of `GET` calls with one token, a role granted that nobody approved, an export (`POST
  /v1/me/data-export`) the person did not ask for, a download of a backup dump you do not recognise.
- A private bucket (KYC documents, delivery proofs, `gb-staging-backups`) found public, or a signed URL shared.
- A lost or stolen laptop or phone with access to Infisical, Neon, the console or a downloaded dump.
- The wrong message sent to the wrong person (a push, a WhatsApp message, an export link).

## Checks

Write the answers in the incident log as you find them, with the time. A short, honest log is what the Board and the
guide will ask for.

1. **What data, whose, and how much?** Use handbook 13 §13.1 as the list of what we hold and where:
   - identity (phone, name, devices): `identity.users`, `identity.devices`;
   - home location and household profile: `identity.addresses`, `households`, `household_members`;
   - **health tags**, sensitive, opt-in only (`household_members.health_tags`);
   - money records: `billing.*`;
   - **partner KYC**, sensitive: `partners.kyc_documents` in the private bucket, with encrypted number columns;
   - rider GPS trails: `logistics.gps_pings`.
2. **Which store?** Neon (Singapore; the shared dev and staging database), the private R2 buckets (including the weekly
   dump in `gb-staging-backups`, which holds all of the above for test users), Render logs, Infisical, Firebase (phone
   numbers for sign-in), PostHog (no names or phone numbers by design), the ML service (no household ids), the Claude API
   (scrubbed text only).
3. **How did it happen, and is it still happening?** A leaked key, a mistake in code (a missing scope check), a platform
   incident, a person. Ask whether the cause is still open.
4. **Since when, until when?** Use `platform.audit_log` (privileged actions, append-only, handbook 13 §13.11), the Render
   request logs (phone, address and names are masked there), Neon's and Cloudflare's own access logs, GitHub's audit log and
   Infisical's audit log.
5. **Was the data readable?** KYC numbers are encrypted with `KYC_ENCRYPTION_KEY`; a copy of the table without the key does
   not expose them, but a copy with the key does. Refresh tokens are stored only as HMACs. Neon and R2 encrypt at rest, which
   protects the disk, not a stolen login.
6. **Who can tell us more?** The person who found it, the platform's support, the code owner of the module (folder
   ownership in `CLAUDE.md`).

## Common causes and fixes

| Cause | Contain with |
|---|---|
| **A leaked secret** (key, token, password, connection string) | Rotate it at once ([`rotate-secrets.md`](rotate-secrets.md)); then find out what it could read and for how long. A leaked `KYC_ENCRYPTION_KEY` or `KYC_HASH_PEPPER` cannot be rotated yet: this is the one case where the decision needs the whole team and the guide |
| **A stolen or lost device or login** | Sign the person out of everything: remove them from Infisical, GitHub, Render, Neon, Cloudflare; revoke their sessions (`restore-from-backup.md`, "After a restore", point 2, has the statement); rotate what they held |
| **A public or shared bucket or file** | Make the bucket private again, rotate the R2 token, and check the access log for who read what. Signed URLs for private files last 5 minutes (handbook 13 §13.5), so a shared one has probably expired |
| **A bug that shows one person's data to another** (a missing scope or ownership check, a wrong cache) | Turn the feature off or roll back ([`deploy-rollback.md`](deploy-rollback.md)); then write a failing test, fix on a `fix/` branch, and list the requests that hit the bug from the logs |
| **A message to the wrong person** | Stop the job or consumer; list the recipients from the outbox and the notification rows; correct them directly |
| **A platform breach** (Neon, Render, Cloudflare, Firebase, Razorpay) | Follow their notice, rotate what they tell you, and ask them in writing what was accessed. They are processors; we remain responsible to the Board and to people |
| **A dump or export kept on a laptop** | Delete it, check who else had the laptop, rotate the R2 backup token |

## Recovery steps

1. **Call it.** The person who finds it tells Prathamesh Relekar at once (phone, then the team channel). Prathamesh is
   the incident lead; Rhythm Thakur or Manish Raje keep the incident log. Open a private note (not a public issue or PR)
   with the time found, who reported it and the first facts. Do not put personal data or secrets in it.
2. **Contain.** Stop the harm first, without deleting evidence. Rotate or revoke the credential, close the door, roll back
   the code, make the bucket private. Do not wipe logs, the database or a laptop. Take a Neon restore point if you are
   about to change data (`scripts/neon_restore_point.py create before-incident`).
3. **Assess.** Work through the checks above. Settle: which data, whose, how many people, what harm could follow (a stolen
   phone number and address is different from stolen health tags or KYC documents), and whether it is still open.
   Real people or test people? That decides whether the notices below are owed.
4. **Decide, with the guide.** Prathamesh and the team decide whether this is a reportable personal data breach, and read
   the current DPDP Rules for the form and deadline of the notice to the Board. Ask Prof. Kranti Bade or a CA if unsure.
   Note the decision, who made it and when.
5. **Notify the Data Protection Board** as the Rules require: what happened, when, which data and how many people, what
   we did and what we will do, and who to contact. Use the Board's channel and format as the current Rules name them.
6. **Notify the affected people**, in plain language in English, Hindi and Marathi (the languages of our notice, handbook
   13 §13.6): what happened, which of their data, what they can do (for example, be careful of calls and messages
   using their phone number), what we did, and whom to contact. Use the channel they gave us: a message in the app
   (M11), WhatsApp where they opted in, or a call through the hub manager. M11 has no broadcast kind for this yet, so
   the message is sent by hand or by adding a kind in a `feat/` PR; do not improvise a mass SMS from a personal
   phone. Tell partners and captains in person where the law or common sense says so.
7. **Tell the processors that need it**: Razorpay if payment identifiers were exposed, and any platform whose key was involved.
8. **Fix the root cause** on a branch with a test. Rotate every credential that may have been seen, not only the one you
   know about. If the breach was a gap in this runbook, change it.
9. **Close with a short review** within a week: the timeline, the cause, what was exposed, who was told and when, what
   changes. File it with the incident log. Rhythm and Manish turn the lessons into a drill for the next pre-pilot check
   (handbook 12 §12.8).

## What not to do

- Do not delete logs, rows or files to "tidy up" before the facts are written down.
- Do not guess a legal deadline or wording. Read the current Rules and ask the guide.
- Do not describe it as "no impact" before you have checked what was readable.
- Do not put personal data, secrets or the incident in a public issue, PR or chat. Use the private note and `infisical run`.
- Do not message affected people from personal accounts or in a way that exposes the other recipients.
- Do not rotate `KYC_ENCRYPTION_KEY` or `KYC_HASH_PEPPER` as a reflex. See `rotate-secrets.md`.
- Do not contact the person who caused it in anger; the aim is to learn how the system let it happen.

## Related

Handbook 13 §13.1 (what we protect), §13.3 (sessions), §13.5 (data protection and retention), §13.6 (privacy and the
breach line), §13.11 (the audit log); M1 §3.3 and §3.5 (exports and erasure);
[`rotate-secrets.md`](rotate-secrets.md), [`restore-from-backup.md`](restore-from-backup.md),
[`deploy-rollback.md`](deploy-rollback.md), [`README.md`](README.md).

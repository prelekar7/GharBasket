<!-- DRAFT for review (see docs/legal/README.md). Not legal advice. Replace every [PLACEHOLDER] before publishing. -->

# Privacy Notice

**Effective date:** [EFFECTIVE DATE] · **Notice version:** v1

This notice explains what personal data GharBasket collects, why, who we share it with, how long we keep it, and your rights under India's **Digital Personal Data Protection Act, 2023** (DPDP Act).

**Who is responsible for your data (Data Fiduciary):** [LEGAL BUSINESS NAME], [REGISTERED ADDRESS].
**Grievance Officer:** [GRIEVANCE OFFICER NAME], [SUPPORT EMAIL], [SUPPORT PHONE].

GharBasket is in an early test with a few housing societies. Payments are real, and this notice applies in full.

## 1. What we collect

**When you sign in**
- Your mobile number. We use it to send you a one-time password (OTP) through Google Firebase.
- Your name, language and display settings (for example senior mode).
- The devices you sign in on (type of device and dates). We do not keep your login tokens in readable form.

**About your household** (to plan your week)
- Household size, each member's **age band** (not date of birth), and optionally sex.
- Diet, fasting or Jain rules, cooking time, no-cook days, and an optional monthly budget.
- **Health tags** (for example "diabetic" or "low-salt"), only if you give separate consent. See section 3.
- Your choice for out-of-stock items (replace or refund).

**Your address**
- Your society, tower and flat, and a map point inside your society.

**Your orders and week**
- Your weekly boxes, edits, skips and approvals, tiffin bookings, and the taps you give after delivery ("ran out", "wasted", "just right").
- Problems you report, with your note and photos.
- Delivery records: status, times, how the delivery was proved (QR scan, delivery code or photo), and the door photo if one was taken.
- Tiffin ratings you give.

**Money**
- Your wallet balance, top-ups, payments for orders and refunds.
- Razorpay payment IDs and amounts. **We never see your full card number or UPI PIN.** Razorpay's payment messages to us can include the phone number, email and UPI ID used to pay.

**Community**
- Referrals you make or join with, donations you schedule (items and pickup details), donation photos, and your Kindness points.

**Messages and chat**
- Messages we send you in the app, by push notification and (if you turn it on) on WhatsApp, and whether they were delivered.
- Your notification choices and quiet hours.
- What you type to the chat assistant. Before we store or send it, we remove phone numbers, email addresses, ID-like numbers, PIN codes, flat and society details, and your household's own names. This removal is automatic and may not catch everything, so please do not type personal details.

**Technical data**
- Your internet address, used to limit repeated requests and keep the service safe.
- Error and performance records. These do not contain your name, phone number, address or account ID.

**We do not collect:** Aadhaar numbers or Aadhaar images, or KYC documents from customers. We do not collect data directly from children; only the adult account holder enters family members' age bands.

**If you are a partner** (Society Captain, home kitchen, rider or packer), we also collect what we need to verify and pay you: a **masked** ID reference, PAN, bank account details (checked with a small test deposit), FSSAI registration and kitchen address and photos for kitchens, a driving licence and vehicle details for riders, training results, and your earnings. KYC numbers are stored encrypted, and only a masked value is shown. For riders, we record **location only while a delivery route is in progress**.

## 2. Why we use it

| Purpose | What it covers | Your choice |
|---|---|---|
| **Service delivery** | Your account, orders, payments, deliveries, refunds, support | Required |
| **Personalisation** | Planning your meals and box from your household details and past boxes | Required for the weekly box |
| **Health personalisation** | Using health tags to plan suitable meals | Optional, separate consent |
| **WhatsApp updates** | Sending your updates on WhatsApp | Optional |
| **Marketing** | Offers and promotions | Optional (we send no offers at present) |
| **Anonymised research** | Using anonymised data in the project team's academic research | Optional |

We also use data to keep the service secure, to prevent misuse and fraud, to keep accounts and payment records, and to meet our legal duties.

## 3. Consent

- When you set up your household, you agree to the two required purposes. You can choose each optional purpose separately.
- Each consent is recorded with the date and the version of this notice.
- **You can withdraw an optional consent at any time** in the app.
  - Withdrawing **health personalisation** **deletes** the health tags of every member of your household at once.
  - Withdrawing **WhatsApp** stops WhatsApp messages at once.
- If you no longer want us to use your data for the required purposes, you can delete your account (section 7).
- If we change this notice, the app will ask you to accept the new version.

## 4. Who can see your data

**People who help deliver**
- **Your Society Captain** sees the tower and flat on your bag, only for her own society's orders of today and yesterday. She does not see your name or phone number.
- **Riders** see the society location for their route.
- **Our hub team** may see your name and phone number to call you back about a problem you reported. Each such look is logged.
- Captains' phone numbers and flats are never shown to customers.

**Service providers who process data for us**

| Provider | What they do | Where |
|---|---|---|
| Google Firebase | Sends sign-in OTPs; sends push notifications | Sign-in: United States (Firebase sign-in runs only in Google's US data centres). Push notifications: Google's data centres worldwide |
| Razorpay | Handles top-ups and refunds | India (Indian rules require payment data to be stored in India) |
| Render | Runs our servers (API and machine-learning service) | Singapore |
| Neon | Hosts our database | Singapore |
| Cloudflare (R2) | Stores photos, documents, data exports and database backups, in private storage | [R2 REGION — to be confirmed in the Cloudflare dashboard] |
| Vercel | Hosts our website and web app | Pages are served from Mumbai, India for visitors in India. The staff console's server runs in Washington, D.C., United States |
| GitHub | Runs scheduled tasks and makes the database backup copy | United States and other countries |
| Anthropic (Claude) | Reads chat assistant messages (after personal details are removed) to understand what you asked | Stored in the United States; may be processed in the United States, Europe, Asia or Australia |
| Meta (WhatsApp Business) | Sends WhatsApp messages, only if you turn them on | Meta's data centres in several countries |
| Grafana Cloud | Error and performance records (no names, phone numbers, addresses or account IDs) | [GRAFANA REGION — to be confirmed in the Grafana Cloud stack] |

We share personal data only with the people and providers listed here, or when the law requires it.

The machine-learning service that plans meals and routes does not receive your name, phone, address or account ID.

## 5. Where your data is stored

- Our database and servers are in **Singapore** (Neon and Render).
- Payment data stays in **India** (Razorpay).
- Some providers in section 4 process data in other countries, mainly the **United States**: sign-in OTPs (Google Firebase), the staff console's server (Vercel), scheduled tasks and the backup copy (GitHub), and the chat assistant (Anthropic, after personal details are removed).
- The DPDP Act allows personal data to be processed outside India, except in countries the Government of India restricts. We do not send personal data to a restricted country, and we will update this section if the Government restricts one we use.

## 6. How long we keep it

| Data | How long |
|---|---|
| Your account, household, address, consents | Until you delete your account (then see section 7) |
| Delivery and door photos | 90 days after the delivery |
| Photos of reported problems | 90 days after the problem is resolved |
| Rider location trails (partners) | 90 days |
| Messages we sent you (inbox, push, WhatsApp records) | 180 days |
| Razorpay and WhatsApp messages to our servers | 180 days |
| Your data export file | 7 days |
| Security audit log (who did what, without personal details) | 3 years |
| Kindness points | Expire 365 days after you earn them |
| Chat assistant conversations | Until you delete your account, then deleted within 30 days |
| Orders and money records (wallet, ledger, payments) | Kept after account deletion for accounting and legal record-keeping, but no longer linked to you (section 7) [RETENTION PERIOD — to be confirmed] |
| Database backups | Older copies are replaced over time (daily backups are kept for 30 days, then deleted) |

## 7. Deleting your account

You can delete your account in the app (**Profile → Delete my account**). For your safety, we ask for a fresh OTP first.

When you ask:
1. You are signed out on every device at once.
2. Your subscription ends. Weeks not yet past the cut-off are skipped. Boxes already paid are still delivered.
3. Your refundable wallet balance is sent back to the UPI account or card you paid with. Any part that cannot be sent back is kept for you to claim through [SUPPORT EMAIL].
4. We stop sending you messages.

You then have **30 days** to change your mind: sign in again and choose **"Keep my account"**. Your subscription stays ended, and you can restart it.

After 30 days we delete your personal data from each part of the service. We wait if something is still open, such as an order, a reported problem, a refund in progress, or items we are keeping for you; we delete as soon as it is settled.

**What we keep:** orders and money records, because the law and our accounts need them. They stay with an account number that no longer leads to you: your name, phone, address, household details, photos, chats, notification records and similar data are deleted. In stored Razorpay messages, your phone number, email and UPI ID are removed.

## 8. Your rights

Under the DPDP Act you can:
- **Get a copy of your data:** **Profile → Download my data**. The file is usually ready within 24 hours. The download link is valid for 5 minutes each time you open it, and the file is kept for 7 days. You can ask once a day.
- **Correct or update your data:** edit your profile, household and address in the app, or write to us.
- **Erase your data:** delete your account (section 7).
- **Withdraw consent:** in the app (section 3).
- **Nominate someone** to use these rights for you if you die or become unable to: write to the Grievance Officer.
- **Complain:** write to our Grievance Officer first. If you are not satisfied with the answer, you can complain to the **Data Protection Board of India**.

## 9. How we protect your data

- All connections are encrypted (HTTPS). Data is encrypted at rest by our hosting providers.
- Partner KYC numbers are encrypted in our database.
- Photos and documents are in private storage, opened only through links that expire after 5 minutes. We remove location and other hidden details from uploaded photos.
- Staff see only what their role needs, and sensitive actions are logged.
- Our logs mask phone numbers, addresses and names.
- If a breach affects your data, we will inform you and the Data Protection Board as the law requires.

## 10. Contact and grievances

**Grievance Officer:** [GRIEVANCE OFFICER NAME]
Email: [SUPPORT EMAIL] · Phone: [SUPPORT PHONE] · Address: [REGISTERED ADDRESS]
We acknowledge your message within 48 hours and resolve it within 15 days.

<!-- Sources:
Data collected: handbook 13 §13.1; M1 §1, US-1–US-7, §3.2 (household, members, consents, address rules), §3.4 (shortage preference), §3.3 (export contents list per module); M3 §3.1 (feedback taps); M4 §3.2 (issues, notes, photos); M7 §3.4–§3.5, Privacy (proofs, codes, photos, GPS only on route, 90 days); M8 US-9 (ratings); M5 §3.1–§3.3; M9 §3.3–§3.4; M11 §3.1–§3.2; M13 §3 data flow (scrub list, ML service gets no ids).
Razorpay webhook fields contact/email/vpa stored and redacted at purge: M1 §3.5 As built (Webhooks) and matrix row 9. No card data: handbook 13 §13.5. (Contradiction with M1 §3.3 "no card or UPI details ever reach us" — README.)
IP address for rate limits: handbook 13 §13.3 item 7; M0 §6.3. Traces without personal data: M0 tracing section ("Never in a span").
No Aadhaar; no customer KYC; children: handbook 13 §13.5, §13.6; ADR-0004.
Partner KYC: handbook 13 §13.8, §13.5 (field encryption, masked value); M8 §1.
Purposes and required/optional: M1 §3.2 Rules; handbook 13 §13.6. No offers in v1: M11 §3.1. Research: handbook 13 §13.6 (Academic data), handbook 05 data_source pilot.
Consent versioned, health revoke erases tags, WhatsApp stops at once, new version shown: M1 §3.2; M11 §3.1.
Who sees: M7 Privacy (captains); M9 §3.1 (captain phone/flat never returned); M4 §3.6 (contact for callback, audit-logged).
Processors: handbook 12 (Render Singapore, Neon Singapore, R2, Vercel, GitHub Actions jobs and backup-staging.yml), handbook 13 §13.2 (Firebase Auth/FCM, Razorpay, WhatsApp Cloud API, Claude API), M0 tracing (Grafana Cloud), M13 §3 (Anthropic via ML service, Singapore), M11 §1 (WhatsApp off until set up). PostHog (ADR-0020) left out: not found in the code (README).
Storage location Singapore: handbook 12 summary; handbook 13 "Decisions & open questions" (Mumbai later).
Provider locations (checked 2026-11-22 on each provider's own pages): Firebase https://firebase.google.com/support/privacy (Authentication runs only from US data centres; FCM is a global service); Razorpay: RBI directive "Storage of Payment System Data" (DPSS.CO.OD No.2785/06.08.005/2017-18, 6 Apr 2018), as Razorpay's privacy policy states no location; Vercel: response header x-vercel-id shows bom1 (Mumbai) at the edge and iad1 (Washington, D.C.) for the console's functions, regions per https://vercel.com/docs/regions; GitHub general privacy statement ("a variety of locations", the US and others); Anthropic https://privacy.claude.com/en/articles/7996890 (stored in the US; traffic may be routed to the US, Europe, Asia and Australia); Meta: the Cloud API stores data in its own data centres unless local storage (India is available) is turned on per number; R2 bucket region (automatic, chosen nearest to whoever created the bucket) and the Grafana stack region are not visible from the repo. Transfers: DPDP Act s.16.
Retention: handbook 13 §13.5 (GPS 90 d, webhooks 180 d, audit 3 y); M0 §3.1 file table (photos 90 d); M11 §3.1, §3.2 (180 d); M1 §3.3 (export 7 d, 24 h, 5-min link, once a day); M5 §3.6 (365 d); chat retention: the owner's default, 22 Nov 2026 (M13 §11 proposed 30 days); ADR-0037 §5 (30 daily backups, Proposed).
Deletion: M1 §3.5 (fresh OTP, sign-out, M3 ends subscription, wallet close, M11 stops, 30-day grace, keep account, purge preconditions, what is kept, webhook redaction); handbook 13 §13.6.
Rights: handbook 13 §13.6; DPDP Act ss. 11–14 (nomination, Board) — legal text for the reviewer, nomination handling not specified.
Security: handbook 13 §13.4–§13.5, §13.11; M0 §3.1 photo metadata. Breach: handbook 13 §13.6, runbook dpdp-breach.md.
-->

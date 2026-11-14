# Runbook: rotating a secret

Owner: Prathamesh Relekar. Last reviewed: 12 November 2026.

Rotate when a secret may have leaked, when someone who knew it leaves the team, or on a plan. Every secret has a home in
Infisical `staging` and one or more places it is copied to. A rotation changes all of them, in an order that keeps staging
working. Two secrets are rotated with a re-keying job, and one signs everyone out; those three are marked below.

**If a secret may have leaked, treat it as an incident first:** note who could see it and since when, rotate, and read
[`dpdp-breach.md`](dpdp-breach.md) if personal data could have been read.

## Symptoms

- A secret was pasted into chat, an issue, a PR, a log or a screenshot.
- gitleaks (CI) flags a committed value.
- A person with access to Infisical, Render, GitHub or the Oracle VM leaves, or loses a laptop.
- A third party (Razorpay, Neon, Cloudflare, Firebase, Render) mails about an exposed key.
- A planned rotation before the pilot.

## Checks

1. **Which secret, exactly?** Find its row in the table. The "Home" column is where it is changed first; "Copies" are the
   other places that must follow.
2. **Is it one of the three special ones?** `KYC_ENCRYPTION_KEY`, `KYC_HASH_PEPPER` (old value kept as retired until a
   job has moved every row) and
   `REFRESH_TOKEN_PEPPER` (signs everyone out). Read their rows first.
3. **What breaks while the copies disagree?** Pick a quiet time. The evening windows (18:30 to 23:30 IST) are the worst:
   a `JOBS_TOKEN` mismatch makes every job call fail with 401, which the scheduler does not retry (ADR-0032).
4. **Do you have access to every copy?** Infisical, the Render dashboard, GitHub repository settings (or `gh`), the
   Oracle VM key.

## Where each secret lives

All values start in Infisical `staging` (people use `infisical run --env=staging -- ...`). New random values: 
`openssl rand -base64 48`. Secrets must be at least 32 bytes (`platform/settings.py`).

| Secret | Home | Copies | How to rotate |
|---|---|---|---|
| `JWT_SIGNING_KEY` (with `JWT_KEY_ID`, `JWT_RETIRED_KEYS`) | Infisical | Render `gharbasket-api` | No one is signed out: see "JWT signing key" |
| `REFRESH_TOKEN_PEPPER` | Infisical | Render `gharbasket-api` | **Only on a leak.** Everyone signs in again: see below |
| `KYC_ENCRYPTION_KEY`, `KYC_KEY_VERSION`, `KYC_RETIRED_KEYS` | Infisical | Render `gharbasket-api` | Old key retired, then `rekey_kyc_documents`: see below |
| `KYC_HASH_PEPPER`, `KYC_PEPPER_VERSION`, `KYC_RETIRED_PEPPERS` | Infisical | Render `gharbasket-api` | The same steps: see below |
| `JOBS_TOKEN` | Infisical | Render `gharbasket-api`; GitHub secret `JOBS_TOKEN`; VM `~/.config/gharbasket/jobs.env` and `mandi.env` | See "JOBS_TOKEN" |
| `ML_SERVICE_TOKEN` | Infisical | Render `gharbasket-api` and `gharbasket-ml`; GitHub secret `ML_SERVICE_TOKEN` (used by `smoke-ml.yml`) | See "ML_SERVICE_TOKEN" |
| `DATABASE_URL` (Neon, pooled) | Neon role password | Infisical; Render `gharbasket-api` | Reset the role's password in Neon; see "Neon" |
| `DATABASE_URL_DIRECT` (Neon, direct) | Neon role password | Infisical; GitHub secret `DATABASE_URL_DIRECT` (used by `deploy-staging.yml`); `backup-staging.yml` once merged | As above, in the same step |
| `DATABASE_URL_RO` (role `gharbasket_ro`) | `scripts/neon-ro-role.sh` | GitHub secret `DATABASE_URL_RO` (training jobs) | `infisical run --env=staging -- scripts/neon-ro-role.sh` makes a new password and sets the GitHub secret without printing it |
| `NEON_API_KEY`, `NEON_PROJECT_ID` | Neon account settings | Infisical only | Make a new key in Neon, replace it in Infisical, delete the old key. Used by `just staging-demo` and `just staging-rollback` |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` (test mode) | Razorpay dashboard | Infisical; Render `gharbasket-api` | See "Razorpay" |
| `RAZORPAY_WEBHOOK_SECRET` | Razorpay dashboard (the webhook) | Infisical; Render `gharbasket-api` | See "Razorpay" |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_ENDPOINT_URL` on the API | Cloudflare R2 API token | Infisical; Render `gharbasket-api` | See "R2 tokens" |
| `ML_S3_ACCESS_KEY`, `ML_S3_SECRET_KEY` (entered on Render as `S3_ACCESS_KEY` / `S3_SECRET_KEY`) | Cloudflare R2 token, read only on `gb-staging-mlflow` | Infisical; Render `gharbasket-ml` | See "R2 tokens" |
| `TRAINING_S3_ACCESS_KEY`, `TRAINING_S3_SECRET_KEY` | Cloudflare R2 token on `gb-staging-lake` and `gb-staging-mlflow` | Infisical; GitHub secrets `S3_ACCESS_KEY`, `S3_SECRET_KEY` (and `S3_ENDPOINT_URL`); VM `mandi.env` | See "R2 tokens" |
| `BACKUP_S3_ENDPOINT_URL`, `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY` (once `backup-staging.yml` is merged) | Cloudflare R2 token on `gb-staging-backups` only | Infisical; the GitHub secrets of the same names | See "R2 tokens" |
| `FCM_SERVICE_ACCOUNT` | Firebase console, Service accounts | Infisical; Render `gharbasket-api` | Generate a new private key, set it on Render, redeploy, then delete the old key in Google Cloud (IAM, Service accounts, Keys). Pushes stop while it is wrong; the in-app inbox still gets every message |
| `DATA_GOV_IN_API_KEY` | data.gov.in, My account | Infisical; GitHub secret `DATA_GOV_IN_API_KEY`; VM `mandi.env` | New key at data.gov.in; then `infisical run --env=staging -- scripts/mandi-on-vm.sh env <ip> <key>` and the GitHub secret |
| `HC_PING_URL`, `HC_CUTOFF_URL`, `HC_OPS_URL` | healthchecks.io | Infisical; the VM `jobs.env`; GitHub secret `HC_OPS_URL` | A leaked URL can only send false alarms or hide real ones (ADR-0033). Make a new check with the same settings, put its URL in Infisical, then `infisical run --env=staging -- just jobs-vm env <ip> <key>` and the GitHub secret; delete the old check |
| `RENDER_DEPLOY_HOOK_URL` | Render, `gharbasket-api` Settings → Deploy Hook | GitHub secret `RENDER_DEPLOY_HOOK_URL` | Regenerate the hook in Render, then update the GitHub secret. Anyone with the URL can start a deploy |
| `CLAUDE_CODE_OAUTH_TOKEN` | The owner's Claude account | GitHub secret only (`claude.yml`) | Make a new token and set the GitHub secret; revoke the old one |
| Oracle VM SSH key (`<key>` in `just jobs-vm ... <ip> <key>`) | The owner's machine | `~/.ssh/authorized_keys` on the VM | Add the new public key to the VM, test it, then remove the old line |
| `ANTHROPIC_API_KEY`, `WHATSAPP_*` | — | Not set on staging | Nothing to rotate |
| Not secrets | — | `STAGING_API_URL`, `STAGING_ML_URL`, `FIREBASE_PROJECT_ID`, bucket names, `WEB_ORIGINS` | — |

Copy a value to a GitHub secret without printing it:

```
infisical run --env=staging -- sh -c 'printf %s "$JOBS_TOKEN" | gh secret set JOBS_TOKEN'
```

## Common causes and fixes

| Why you are rotating | What to do |
|---|---|
| **A leak of one secret** | Rotate that row at once, then check its blast radius: what could it read or do, and since when (git history, the log, who had access). If it reached personal data, open [`dpdp-breach.md`](dpdp-breach.md) |
| **Someone left the team** | Remove them from Infisical, GitHub, Render, Neon, Cloudflare, Firebase, healthchecks.io, UptimeRobot and the VM. Then rotate what they could read: at least `JOBS_TOKEN`, the database passwords and the R2 tokens |
| **A planned rotation** | One secret at a time, in the order of the table's "How to rotate" notes, at a quiet hour |
| **gitleaks flagged a bucket name or similar** | Bucket names are not secrets (`# gitleaks:allow` in `render.yaml`). Check, and only rotate if it was a real value |

## Recovery steps

### JWT signing key (nobody is signed out)

Access tokens are signed with a key that has an id (`kid`), and the API keeps checking old keys listed in
`JWT_RETIRED_KEYS` until the last old token has expired (15 minutes).

1. Note the current `JWT_KEY_ID` (default `k1`) and `JWT_SIGNING_KEY`.
2. In Infisical and on Render set, together in one save:
   - `JWT_RETIRED_KEYS` = `k1:<the old key>` (format `kid:key,kid:key`);
   - `JWT_KEY_ID` = a new id, for example `k2` (lower case letters, digits and `-`, up to 16);
   - `JWT_SIGNING_KEY` = the new key.
3. Save and deploy on Render. New tokens are signed with `k2`; tokens signed with `k1` still verify.
4. After 15 minutes, delete `JWT_RETIRED_KEYS` in Infisical and on Render, and deploy again. Old tokens now fail, and the
   apps ask for a new one with their refresh token.
5. If the old key **leaked**, skip the grace: leave `JWT_RETIRED_KEYS` empty at step 2. Everyone's current access token
   fails at once and the apps refresh (refresh tokens are separate), so people stay signed in.

### `REFRESH_TOKEN_PEPPER` (only on a leak; everyone signs in again)

Refresh tokens are stored only as `HMAC-SHA256(pepper, token)` (handbook 13 §13.3). A new pepper makes every stored token
unmatchable, so every device is signed out at its next refresh and must sign in again with a phone code. There is no
second-pepper setting, so there is no gradual way. Do it only if the pepper leaked **and** the database leaked with it
(the pepper alone cannot be used without the stored hashes). Tell the hub manager first; do not do it in an evening window.

### `KYC_ENCRYPTION_KEY` and `KYC_HASH_PEPPER` (retire, then re-key)

KYC numbers are stored as `pgp_sym_encrypt(number, key)` with the key's version, and as
`HMAC-SHA256(pepper, "<type>:<number>")` with the pepper's version (M8 §3.1). The API reads a row with the key of its
own version and matches duplicates under every configured pepper, so an old key or pepper keeps working while it is
listed as retired. The job `rekey_kyc_documents` moves rows to the current pair inside Postgres; it runs daily at 03:33
IST and is a no-op when every row is current. Rotating the key:

1. Generate the new key: `openssl rand -base64 48`.
2. In Infisical `staging`, add the current key to `KYC_RETIRED_KEYS` under its version (`1:<old key>`; more than one is
   comma-separated, `1:<key>,2:<key>`), then set `KYC_ENCRYPTION_KEY` to the new key and `KYC_KEY_VERSION` to the old
   version + 1. Copy the three values to Render `gharbasket-api`, which redeploys. The API refuses to start on a retired
   version equal to the current one, a version listed twice, a malformed pair, spaces around a version or value, or a
   value under 32 bytes, naming the variable only.
3. **Wait until the Render dashboard shows the new deploy live.** A job run that reaches the old instance still has the
   old settings, sees every row as current and answers `remaining: 0`.
4. Run the job: `gh workflow run jobs-staging.yml -f job=rekey_kyc_documents`, then
   `just job-runs rekey_kyc_documents`. The summary is
   `{ rekeyed, rehashed, remaining, missing_key, key_version, pepper_version }`. **Go on only when it names the new
   `key_version` (and `pepper_version`) and says `remaining: 0`.** `missing_key` above 0 means rows under a version with
   no key configured: put that key back in `KYC_RETIRED_KEYS` and run again. A run that fails with
   `KycStatementError (SQLSTATE 39000)` reached a row whose configured key is wrong: the rows before it have moved (the
   `kyc.rekeyed` audit row says how many, with `failed: true`); fix the key and run again.
5. Remove the retired key from Render (`KYC_RETIRED_KEYS` unset or without it). Keep it in Infisical for as long as the
   weekly backups hold rows encrypted with it (8 weeks, handbook 12), then delete it there too.

The pepper follows the same steps with `KYC_HASH_PEPPER`, `KYC_PEPPER_VERSION` and `KYC_RETIRED_PEPPERS`; both may be
rotated together and one run of the job does both. If a key or pepper leaked, the same steps apply, and the backups made
before the rotation are treated as exposed: open [`dpdp-breach.md`](dpdp-breach.md).

Checks: the `ops_check` probe `partners.kyc_key_missing` names any stored key or pepper version that is no longer
configured (with its row count), and any configured key that does not decrypt a sample row or pepper that does not
reproduce its hash ("KYC key version V does not decrypt", "KYC pepper version V does not match"); versions and counts
only, never a number. A payout read of such a row answers 503 `partners.kyc_key_unavailable`.

### `JOBS_TOKEN`

It protects `/v1/ready` and every `/v1/internal/jobs/*` call. Four places must agree, and the VM scheduler does not retry
a 401.

1. Pick a time outside the evening windows and the morning run (06:00 to 08:45 IST). Check `just jobs-vm due <ip> <key>
   <IST minute>` for what runs next.
2. New value into Infisical `staging`.
3. Render `gharbasket-api`: set it, save and deploy. From here the VM's calls fail with 401.
4. At once: `infisical run --env=staging -- just jobs-vm env <ip> <key>` (writes `jobs.env` and restarts the service),
   `infisical run --env=staging -- scripts/mandi-on-vm.sh env <ip> <key>`, and the GitHub secret `JOBS_TOKEN`.
5. Check: `just jobs-vm status <ip> <key>` shows calls answering 200; `infisical run --env=staging -- just job-runs`
   shows new runs. Run any job that was due in the gap by hand (README).

### `ML_SERVICE_TOKEN`

The same value in the API, the ML service and the GitHub secret, at least 32 bytes. While they differ the API gets 401 and
uses its fallbacks (`ml-service-down.md`); customers are not blocked.

1. New value into Infisical; set it on `gharbasket-ml`, then on `gharbasket-api`; save and deploy both.
2. Update the GitHub secret `ML_SERVICE_TOKEN`.
3. Check: `infisical run --env=staging -- just cloud-ml https://<ml-service>.onrender.com` shows nine lines of 200.

### Neon passwords

`DATABASE_URL` and `DATABASE_URL_DIRECT` contain the role's password. Resetting it in the Neon console (the project's
Roles) breaks every user of the old one at once, so expect a few minutes of errors.

1. Pick a quiet time. Reset the password; copy both new URLs (pooled for the API, direct for migrations).
2. Put them in Infisical `staging`; set `DATABASE_URL` on Render `gharbasket-api` and deploy; set the GitHub secret
   `DATABASE_URL_DIRECT`.
3. Run `infisical run --env=staging -- scripts/neon-ro-role.sh` too if the owner role was reset: it checks the read-only
   role and makes a fresh `DATABASE_URL_RO`.
4. Check `/v1/ready` (`just cloud <url>`) and the next `ops_check`.

### Razorpay

Test mode only: live keys (`rzp_live_`) are refused at start. Make the new key pair in the dashboard, update Infisical and
Render, deploy, then disable the old pair. For the webhook secret, change it on the dashboard's webhook for
`/v1/payments/webhook/razorpay` and on Render in the same minute. A webhook that arrives in between is refused and
healed by `reconcile_payments` (`razorpay-down.md`). Top-ups answer 503 while the keys disagree.

### R2 tokens

Each token covers its own buckets, so rotate them one at a time. In Cloudflare → R2 → Manage API tokens, make the new
token with the same permissions and buckets as the old one, put its key pair in Infisical, copy it to its places in the
table (Render and redeploy; GitHub secrets; for the training token also `scripts/mandi-on-vm.sh env`), check, then delete
the old token. Checks: `just cloud <url>` for uploads on the API, `just cloud-ml` for `registry_sync`, the next training or
backup run for the others. Keep the backup token on `gb-staging-backups` alone.

## What not to do

- Do not replace `KYC_ENCRYPTION_KEY` or `KYC_HASH_PEPPER` without listing the old value as retired, and do not remove
  a retired one before `rekey_kyc_documents` reports `remaining: 0`. Stored KYC numbers would be unreadable.
- Do not rotate `REFRESH_TOKEN_PEPPER` on a hunch. It signs out every user.
- Do not delete `JWT_RETIRED_KEYS` before 15 minutes have passed, unless the old key leaked.
- Do not change a secret in Render alone. Infisical is the source; the next person who copies from it would put the old
  value back.
- Do not print a secret to check it. Compare lengths or run the smoke tests.
- Do not paste a secret, a connection string or a ping URL into chat, an issue or a PR. Use `infisical run`.
- Do not commit `.env` files or real values. Update this table when a secret is added or removed.

## Related

Handbook 12 §12.2 ("One-time setup"), handbook 13 §13.3 and §13.5, `render.yaml`, `.env.example`,
`backend/api/src/gharbasket/platform/settings.py`, ADR-0024 (sign-in), ADR-0032 and ADR-0033,
[`dpdp-breach.md`](dpdp-breach.md), [`ml-service-down.md`](ml-service-down.md), [`razorpay-down.md`](razorpay-down.md),
[`jobs-scheduler-down.md`](jobs-scheduler-down.md), [`README.md`](README.md).

# Runbook: the ML service is down or not answering

Owner: Prathamesh Relekar. Last reviewed: 2 November 2026 (work package C1).

**Customers are never blocked.** Every call from the API to the ML service has a timeout and a rule-based fallback (handbook 08 §8.2, `intel_gateway/api.py`). When the service is asleep, slow, refusing the token or gone, the weekly plan comes from the rule planner, routes use straight-line distances, slot prices use the nearest-stop rule and the pantry twin uses its closed form. Each such answer is marked `source: "fallback"`. Nothing here is a customer outage; it is a loss of quality, so work through the steps in order and do not hurry a risky fix.

## Symptoms

- **AI health page** (console, admin): the ML service card shows `down`, or a high share of `fallback` predictions in the 7-day counts. Under the hood this is `GET /v1/analytics/ai-health`, field `ml_service.status` (`up`, `down`, `not_configured`). `down` also covers a refused token and a service that did not answer within 3 s. The answer is cached for about a minute.
- **API logs** (Render, `gharbasket-api`): `ml_fallback` lines. The `reason` says why: `ReadTimeout` or `ConnectError` (asleep or unreachable), `HTTPStatusError` with `status` 401 (token mismatch) or 5xx (the service is failing), `circuit_open` (the API's breaker is skipping that path for 60 s after three failures; it is a result, not a cause), `service_down` (a scheduled job found the service unreachable and stopped asking for the rest of its run). `ml_health_down` is the AI health probe itself.
- **`smoke-ml.yml`** (GitHub Actions, daily 06:40 IST) is red. Its log names the endpoint that broke.
- **An admin message** "Forecast drift at <hub>" is a different problem (a model scored worse than its backtest). Go to the AI health page; the service may be perfectly up.

## Checks

1. **Is it only asleep?** The free Render service sleeps after 15 idle minutes and takes about 60 s to wake (ADR-0030). `curl -sS -m 90 https://<ml-service>.onrender.com/health` wakes it. If this answers `{"status":"ok",...}`, wait a minute and reload the AI health page: it should now say `up`. A sleeping service is the usual cause and needs no fix.
2. **Render dashboard**, service `gharbasket-ml`: is the latest deploy live or failed? Events and Logs show crashes and out-of-memory kills (the free plan has 512 MB; peak use was 246 MB). Is the workspace out of its 750 free instance hours this month? Both free web services share them.
3. **Full smoke test with the token**, from the repo root:

   ```
   infisical run --env=staging -- just cloud-ml https://<ml-service>.onrender.com
   ```

   It prints one line per endpoint (`<path> <status> <summary>`) and exits 1 unless all nine answer 200. `/health/ai` in it shows the served models, OSRM (`not_configured` on staging is normal, ADR-0030) and `registry_sync`.
4. **Compare what each side knows.** `ml_service.registry_sync` on the AI health page (or `registry_sync` in `/health/ai`) has `last_success_at`, `last_failure_at` and `last_error`. A recent `last_failure_at` after the last success means the service cannot read the model registry on R2.

## Common causes and fixes

| Cause | How to tell | Fix |
|---|---|---|
| **Sleeping free instance** | Timeouts; `/health` answers after about 60 s | Nothing. The first calls fall back while it wakes. `smoke-ml.yml` wakes it at 06:40 IST. We do not keep it awake on purpose (ADR-0030) |
| **Token mismatch** | `ml_fallback` with `status: 401`; `/health` works but `/health/ai` and the smoke test get 401 | `ML_SERVICE_TOKEN` must be the same value in the API service, the ML service and the GitHub secret. Fix it in Infisical `staging`, copy it to both Render services, and redeploy both. Tokens under 32 bytes stop either service from starting |
| **Wrong or missing URL** | AI health says `not_configured` (unset) or `down` with `ConnectError` | Check `ML_SERVICE_URL` on `gharbasket-api` (the ML service's public https URL, no trailing path). A change needs a restart of the API |
| **R2 sync errors** | `/health/ai` has a recent `registry_sync.last_failure_at` and a `last_error`; the served versions are old | The service keeps serving the models it already has. Check the R2 keys on `gharbasket-ml` (`S3_*`), the bucket name, and Cloudflare status. The sync retries by itself; a restart of the service forces one |
| **A promotion broke a model** | Errors started after `just promote`; the failing endpoint is the one that uses the model | Point the model's `latest.json` back at the previous version: `just promote <model> <previous-version>` (also rolls back). The service picks it up at its next sync, or restart it |
| **Bad deploy** | The service started failing right after a Render deploy; Render shows the deploy as failed or crash-looping | In the Render dashboard, roll back to the previous deploy (Events → Rollback), then fix forward on a branch |
| **Out of memory or instance hours** | Render events show OOM kills or "suspended" | Check the usage. Wait for the month to roll over, or stop the service that is not needed. Moving to a paid plan is a decision for before the pilot (ADR-0030) |

## Recovery steps

1. Run check 1 (wake it) and read `ml_service.status` again after a minute. If `up`, you are done; note in the channel that it was asleep.
2. If not, run check 3 and work out which row of the table above matches. Fix that one thing.
3. After a fix, run `just cloud-ml` again until all nine lines are 200, then reload the AI health page (wait up to a minute for the cache) and confirm `up`, the served versions you expect, and a fresh `registry_sync.last_success_at`.
4. The API's circuit breaker closes by itself: one call a minute probes the service, and a good answer lets customers' calls through again. Nothing to reset. If you changed `ML_SERVICE_URL` or the token on the API, restart it.
5. Plans drafted by the rules while the service was down stay valid; the next draft asks the service again.
6. If the cause was a code change, open a `fix/` branch, add a test, and add a line to this runbook if the symptom was new.

## What not to do

- Do not set up a pinger to keep the free service awake (ADR-0030). It would starve the API's free hours.
- Do not delete predictions marked `fallback`; they are how AI health counts the fallback share.
- Do not paste `ML_SERVICE_TOKEN` into chat, an issue or a PR. Use `infisical run`.

## Related

Handbook 08 §8.1 (monitoring) and §8.2 (the designed fallback), handbook 12 §12.7, M10 §3.9 (timeouts and breaker) and §6 (`monitor_drift`), ADR-0030, `.github/workflows/smoke-ml.yml`.

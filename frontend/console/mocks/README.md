# Console mock API

Pages are built before the backend exists, so the console answers API calls in-process from
[MSW](https://mswjs.io) handlers. Nothing is intercepted over the network and no service worker is
involved: `lib/api.js` builds a `Request` and resolves it with `getResponse(handlers, request)`
(the same idea as `frontend/mobile/src/mocks/index.js`). Switch to the real API with
`GB_API_MOCK=false GB_API_URL=http://localhost:8000`; page code does not change.

## Real mode

`GB_API_MOCK=false GB_API_URL=http://localhost:8010 pnpm exec next dev --port 3160` (the API must allow the port in
`WEB_ORIGINS`, or be a dev API, which allows 3000). Sign in with the dev buttons (they call `POST /v1/auth/exchange`
with `dev_phone`, so the API needs `APP_ENV=dev`) or the phone form with the code 123456.

- **Session:** the API's cookies `__Host-gb_at` (access, 15 min, Path=/) and `gb_rt` (refresh, Path=/v1/auth) are relayed to the
  browser by the sign-in Server Action and by `app/v1/auth/session/route.js`; `gb_session` remembers which staff role
  (admin, hub_manager, franchise_owner, ngo_coordinator) they work as. Details: `lib/auth-api.js`.
- **Refresh:** when the access cookie is gone, `proxy.js` redirects a page navigation to `/v1/auth/session?next=…`, which
  refreshes and returns. `SessionKeeper` refreshes every 12 minutes while a tab is open.
- **Who is signed in:** `GET /v1/me` (`lib/auth.js`). Someone with no staff role sees `/not-staff`.
- **Every API call** carries `X-Active-Role: <role>[:<scope id>]` (`hub_manager:<hub uuid>`; `admin` alone).
- **Hubs:** from the `/v1/me` roles; admins get the proposed `GET /v1/hubs` or, while it 404s, the seed hub (`lib/hub.js`).
- Pages for endpoints that have not shipped show their error screen (404), as designed.

## Calling the API from a page

```js
import { apiGet, apiPost, ApiProblem } from "@/lib/api.js";

// Server Component (below the console's loading.jsx Suspense, so it may read cookies)
const { items } = await apiGet("/v1/products", { searchParams: { kind: "veg", category: "leafy-greens" } });

// Server Action: one Idempotency-Key per user intent, made when the form renders
await apiPost("/v1/hubs/kharghar/price-lists", body, { idempotencyKey });

// Errors are RFC 9457 problem+json: branch on `code`
try { await apiPost(...); } catch (e) { if (e instanceof ApiProblem && e.code === "orders.cutoff_passed") ... }
```

`apiGet(path, { searchParams, headers })`, `apiPost(path, body, { idempotencyKey, headers })`,
`apiPatch`, `apiPut` and `apiDelete`. They return the parsed JSON (`null` for 204) or throw
`ApiProblem` (`status`, `code`, `errors`, `extra`, `requestId`). The selected hub is not sent for
you: read it with `currentHub()` from `lib/hub.js` and put it in the path or query.

Money is integer paise (`price_paise`), never a float. Dates are ISO strings.

## Adding mocks for a domain

1. Create `mocks/<domain>.js` exporting `<domain>Handlers` (an array of `http.get/post/...`).
2. Register it in `mocks/index.js`.
3. Follow the contract in `docs/modules/Mx-*.md` and, when it exists, the generated OpenAPI types:
   the same paths (`*/v1/...`), the same field names, `{ items, next_cursor }` for lists.
4. Keep state in `mockStore(name, init)` (`mocks/util.js`), not in a module variable: Next can load
   a module once per bundle layer, and the store lives on `globalThis` so all layers share it. State
   lives for one server process and resets when the dev server restarts.
5. Errors come from `problem(status, "module.code", "Title")`. POSTs that create money or orders
   wrap their work in `idempotent(request, async (body) => ...)`, which implements the Idempotency-Key
   rules (missing key 400, same key and body replays, same key and other body 422).
6. `roleOf(request)` gives the signed-in console role (`x-gb-role`) when a handler must scope data.

```js
import { HttpResponse, http } from "msw";
import { idempotent, mockStore, problem } from "./util.js";

const lists = () => mockStore("prices", () => []);

export const pricesHandlers = [
  http.get("*/v1/prices", () => HttpResponse.json({ items: lists(), next_cursor: null })),
  http.post("*/v1/hubs/:hub/price-lists", ({ request }) =>
    idempotent(request, (body) => {
      if (!body.lines?.length) return problem(422, "request.invalid", "Add at least one line");
      lists().push(body);
      return HttpResponse.json(body, { status: 201 });
    }),
  ),
];
```

A request no handler matches throws `ApiProblem` with `code: "mock.not_implemented"` that names the
missing route.

## What exists

| File | Endpoints |
|---|---|
| `hubs.js` | `GET /v1/hubs`, `GET /v1/hubs/{id}` (one hub, Kharghar; admins would see all) |
| `catalog.js` | `GET /v1/products`, `/v1/products/{id}`, `/v1/search` (frozen S1 contract, 68 seed products with made-up Kharghar prices in paise; ids are uuids) and the admin product proposals |
| `admin.js` | One list of the admin pages' mocks: `community.js` (the real `GET /v1/societies` (SocietySummary), `GET /v1/societies/{id}` (SocietyOut) and `/unlock`; the proposed `GET`, `POST` and `PATCH /v1/admin/societies` with the boundary, hub, threshold and towers, and `GET /v1/admin/society-requests` with its `accept`, `duplicate` and `PATCH` steps), `identity.js` (the real `POST /v1/admin/user-roles` with `{ phone_e164, role, scope_id? }`; the proposed `GET /v1/admin/users` and `DELETE /v1/admin/user-roles/{id}`), `tiffin.js` (`GET /v1/hubs/{hub}/tiffin-menus`, `POST /v1/tiffin-menus/{id}/approve`, `send-back`, `reopen`) and `hubadmin.js` (`GET|POST /v1/admin/hubs`, `PATCH /v1/admin/hubs/{id}`). Paths the module specs name are used; the rest are proposals (`@proposal`), which a page treats as "not available yet" when the real API answers 404 (`lib/proposal.js`). They check the caller's role (`x-gb-role`) the way the API will. |

| `notifications.js` | The real M11 §3.2: `GET` and `PUT /v1/admin/notification-templates` (`{ kinds, items }`; the PUT is collection-level and adds the next active version in en-IN, hi-IN and mr-IN; 422 `notifications.template_invalid`), `POST …/preview` (sample values, nothing saved) and `GET /v1/admin/notifications?user_id&cursor&limit` (the delivery log with each message's deliveries; admin and hub_manager). Seeds the copy of six kinds, so the other kinds show as new in the editor. The log is by user id: the Delivery log page turns a mobile number into one with the proposed `GET /v1/admin/users?phone=` (or takes the id typed in). |
| `prices.js` | `GET /v1/prices` (PriceListOut), `POST /v1/hubs/{id}/price-lists` (whole list, PriceListCreated) and the proposed `GET /v1/hubs/{id}/price-lists` |
| `orders.js` | `GET /v1/orders` (OrderSummaryOut, paged by `cursor`, `from` and `to`), `GET /v1/slots` (SlotsOut), `POST /v1/admin/slots`, `PATCH /v1/admin/slots/{id}`, and the proposed `GET /v1/hubs/{id}/delivery-dates`, `GET /v1/hubs/{id}/cutoff`, `POST /v1/hubs/{id}/cutoff/run` |
| `cycles.js` | `GET` and `PUT /v1/societies/{id}/delivery-days` (weekday 0 = Monday, 1 to 3 items) and the proposed `GET /v1/hubs/{id}/delivery-days` |
| `plans.js` | `GET /v1/plans` (PlanOut), and the proposed `GET /v1/admin/plans`, `POST /v1/admin/plans`, `PATCH /v1/admin/plans/{code}` |
| `festivals.js` | `GET /v1/festivals` (FestivalOut, one row per date), and the proposed `POST /v1/admin/festivals`, `PATCH /v1/admin/festivals/{id}` |
| `partners.js` | The real M8: `GET /v1/hubs/{id}/partners` and `GET /v1/partners/{id}` (PartnerStaff), `POST …/suspend` (`reason_code`, `note`) and `…/reinstate`, `GET /v1/partners/{id}/card`, `GET /v1/hubs/{id}/partner-applications` (ApplicationReview, `status` defaults to `kyc_pending`), `POST /v1/partner-applications/{id}/decision` (approve, resubmit, reject), `POST /v1/kyc-documents/{id}/file-links`, `POST /v1/kitchens/{id}/inspection`, `GET /v1/hubs/{id}/partner-rates`; the proposed fairness dashboard (`GET /v1/hubs/{id}/fairness`, `PUT …/fairness-weight`) stays here. Seed partner ids are in `partner-ids.js`. |
| `payouts.js` | The real M5 §3.5: `GET /v1/admin/payouts?week=&status=&hub_id=` (ISO weeks relative to today; admin and hub_manager) and `POST /v1/admin/payouts/{id}/retry` (admin, a held or failed payout goes to `pending`) |

Proposals (marked `@proposal (not in the contract yet)` in the mocks; in real mode they 404 and the page says "not available yet",
see `lib/proposal.js`): `GET /v1/admin/products` (`?active=false`), `GET /v1/admin/products/{id}`, `POST/PATCH /v1/admin/products`,
`GET /v1/hubs/{id}/price-lists`, `GET /v1/admin/plans` and its writes, and `POST/PATCH /v1/admin/festivals` (the id of a run is
`code:region:starts_on`). The real contract has no category list, no `q` or `include_inactive` on products, and no `active` on
a product: pages derive categories from the products, filter in the browser, and treat a missing `active` as active.
Slots, societies and orders have uuids too (`ids.js`, `mockUuid`); the real order has no flat, society name or item summary, so the Orders board shows the society (named from `GET /v1/societies`), the kind and the slot. Product ids are uuids (`mockUuid` in `util.js`); the slug is in `slug`.

## Gotchas

- `@gharbasket/ui` has no `"use client"` barrel: import it from Client Components only. Server
  Components use plain markup and Tailwind token classes.
- Anything that reads cookies (`apiGet`, `getRole`, `currentHub`) must sit below a Suspense boundary
  (cacheComponents). Pages get one from `app/(console)/loading.jsx` for free.
- Page filters (search, date, status) go in the URL with nuqs (`NuqsAdapter` is in the console
  layout); the hub is a cookie, see `lib/hub.js`.

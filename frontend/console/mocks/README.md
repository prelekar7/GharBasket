# Console mock API

Pages are built before the backend exists, so the console answers API calls in-process from
[MSW](https://mswjs.io) handlers. Nothing is intercepted over the network and no service worker is
involved: `lib/api.js` builds a `Request` and resolves it with `getResponse(handlers, request)`
(the same idea as `frontend/mobile/src/mocks/index.js`). Switch to the real API with
`GB_API_MOCK=false GB_API_URL=http://localhost:8000`; page code does not change.

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
| `catalog.js` | `GET /v1/products`, `/v1/products/{id}`, `/v1/search` (frozen S1 contract, 68 seed products with made-up Kharghar prices in paise) and `GET /v1/categories` |

| `prices.js` | `GET /v1/prices`, `GET/POST /v1/hubs/{id}/price-lists` (the list in use, scheduled and past lists, bulk publish) |
| `plans.js` | `GET /v1/plans`, `POST /v1/admin/plans`, `PATCH /v1/admin/plans/{code}` |
| `festivals.js` | `GET /v1/festivals`, `POST /v1/admin/festivals`, `PATCH /v1/admin/festivals/{id}` |

Proposals beyond the specs (M2, M3): `GET /v1/admin/products/{id}`, `GET /v1/hubs/{id}/price-lists`, the admin plan writes,
and the extra festival fields (`name`, `summary`, `starts_on`/`ends_on`/`repeat`, `households`).

Mock-only additions beyond the real contract: `GET /v1/categories` (the Catalog filter needs a
list), and `q` and `include_inactive` on `GET /v1/products`. Ask the backend for them before a page
depends on them for good. `catalogStore()` exposes the products so a price-list mock can edit prices.
Product ids are slugs here; the real API uses UUIDs, so never parse an id.

## Gotchas

- `@gharbasket/ui` has no `"use client"` barrel: import it from Client Components only. Server
  Components use plain markup and Tailwind token classes.
- Anything that reads cookies (`apiGet`, `getRole`, `currentHub`) must sit below a Suspense boundary
  (cacheComponents). Pages get one from `app/(console)/loading.jsx` for free.
- Page filters (search, date, status) go in the URL with nuqs (`NuqsAdapter` is in the console
  layout); the hub is a cookie, see `lib/hub.js`.

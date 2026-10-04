# @gharbasket/api-client

The frontend's way to call the GharBasket API ([ADR-0022](../../../docs/adr/0022-api-client-openapi-fetch.md)).

- `src/generated/schema.d.ts` is **generated** from `backend/api/openapi.json` by `just gen-api`.
  Never edit it by hand. CI regenerates it and fails if it differs.
- `src/index.js` is a small runtime on top: an [openapi-fetch](https://openapi-ts.dev/openapi-fetch/)
  client with our sign-in headers, `ApiProblem` for RFC 9457 errors, and
  [openapi-react-query](https://openapi-ts.dev/openapi-react-query/) hooks. Changes to it are reviewed
  by both app owners.

```js
import { createApi, ApiProblem, newIdempotencyKey } from "@gharbasket/api-client";

// Capacitor app: Bearer token. Browsers (PWA, console): `web: true` sends cookies and X-GB-Client.
export const api = createApi({
  baseUrl: import.meta.env.VITE_API_URL,
  web: !Capacitor.isNativePlatform(),
  getAccessToken: () => useSession.getState().accessToken,
  getActiveRole: () => useSession.getState().activeGrant, // "captain:<society id>"
});

// React Query hooks, typed from the schema in the editor:
const { data } = api.$query.useQuery("get", "/v1/households/me");

// Plain calls; `unwrap` throws ApiProblem on an error response:
const key = newIdempotencyKey(); // one per user intent; a new one only after the user edits
const household = await api.unwrap(
  api.client.POST("/v1/households", { body, headers: { "Idempotency-Key": key } }),
);
```

Branch on `problem.code` (for example `identity.household_not_found`), never on the status or the text.
Extension members such as `household_id` on `identity.household_exists` are on `problem.extra`.

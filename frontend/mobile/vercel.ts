// The PWA's Vercel routes (handbook 12 "PWA", ADR-0024). Vercel runs this file at build time, so
// each Vercel project can send `/v1/*` to its own API (ADR-0039): production's PWA to
// api.gharbasket.co.in (the default), staging's to its own API once staging moves to `staging-*`.
// Set `GB_API_ORIGIN` (e.g. https://staging-api.gharbasket.co.in) on the staging PWA project.
//
// Same rules as the old vercel.json: only `routes` can transform request headers, so `/v1/(.*)`
// goes to the API with `x-origin-secret` set from the project's ORIGIN_SECRET (`set` replaces
// anything the visitor sent; Vercel expands it at request time, it is never in the build), then
// static files, then every other path to index.html (the single-page app).

const DEFAULT_API_ORIGIN = "https://api.gharbasket.co.in";

/** An https origin with no path, query or trailing slash; anything else fails the build. */
export function apiOrigin(value: string | undefined): string {
  const origin = (value ?? "").trim() || DEFAULT_API_ORIGIN;
  if (!/^https:\/\/[a-z0-9.-]+(:\d+)?$/i.test(origin)) {
    throw new Error(`GB_API_ORIGIN must look like https://host, with no path (got "${origin}")`);
  }
  return origin;
}

export const config = {
  $schema: "https://openapi.vercel.sh/vercel.json",
  routes: [
    {
      src: "/v1/(.*)",
      dest: `${apiOrigin(process.env.GB_API_ORIGIN)}/v1/$1`,
      transforms: [
        {
          type: "request.headers",
          op: "set",
          target: { key: "x-origin-secret" },
          args: "$ORIGIN_SECRET",
          env: ["ORIGIN_SECRET"],
        },
      ],
    },
    { handle: "filesystem" },
    { src: "/(.*)", dest: "/index.html" },
  ],
};

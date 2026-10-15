import { ApiProblem, apiDownload } from "@/lib/api.js";

/**
 * Streams a file the API renders (a PDF report) to the browser from a Route Handler, so the
 * download carries the session and the browser saves it under `filename`. Errors become a short
 * plain-text answer the browser shows, never the problem's raw `code`.
 * @param {string} path the API path, starts with /v1/
 * @param {import("@/lib/api.js").SearchParams} searchParams
 * @param {string} filename
 * @param {string} [accept] media type asked for from the API
 */
export async function streamFile(path, searchParams, filename, accept = "application/pdf") {
  try {
    const { body, contentType } = await apiDownload(path, { searchParams, accept });
    return new Response(body, {
      headers: {
        "content-type": contentType,
        "content-disposition": `attachment; filename="${filename}"`,
        "cache-control": "no-store",
      },
    });
  } catch (e) {
    if (e instanceof ApiProblem && e.status === 404)
      return new Response("There is no report for that month.", { status: 404 });
    if (e instanceof ApiProblem && e.status === 403)
      return new Response("You do not have access to this report.", { status: 403 });
    return new Response("Could not prepare the report. Try again.", { status: 500 });
  }
}

/**
 * The `month` query value ("2026-10"), or null when it is missing or malformed.
 * @param {Request} request
 */
export function monthOf(request) {
  const month = new URL(request.url).searchParams.get("month");
  return month && /^\d{4}-(0[1-9]|1[0-2])$/.test(month) ? month : null;
}

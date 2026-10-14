import { describe, expect, it } from "vitest";
import { ApiError, fieldError } from "@/lib/api.js";

/** @param {number} status @param {unknown} body @param {string} [type] */
const respond = (status, body, type = "application/problem+json") =>
  new Response(typeof body === "string" ? body : JSON.stringify(body), {
    status,
    headers: { "content-type": type },
  });

describe("ApiError", () => {
  it("keeps extension members, field errors and the request id", async () => {
    const e = await ApiError.from(
      respond(409, {
        type: "urn:gharbasket:problem:identity.household_exists",
        title: "Conflict",
        status: 409,
        code: "identity.household_exists",
        detail: "You already have a household.",
        instance: "/v1/households",
        request_id: "r-1",
        household_id: "h-1",
        errors: [{ loc: ["body", "phone"], message: "Enter 10 digits", type: "value_error" }],
      }),
    );
    expect(e).toBeInstanceOf(ApiError);
    expect([e.status, e.code, e.title, e.requestId]).toEqual([
      409,
      "identity.household_exists",
      "Conflict",
      "r-1",
    ]);
    expect(e.message).toBe("You already have a household.");
    expect(e.extra).toEqual({ household_id: "h-1" });
    expect(fieldError(e, "phone")).toBe("Enter 10 digits");
    expect(fieldError(e, "name")).toBeUndefined();
    expect(fieldError(new Error("x"), "phone")).toBeUndefined();
  });

  it("defaults the code to http.<status> when the body is not a problem", async () => {
    const e = await ApiError.from(respond(502, "<html>Bad gateway</html>", "text/html"));
    expect([e.status, e.code, e.errors, e.extra, e.requestId]).toEqual([
      502,
      "http.502",
      [],
      {},
      null,
    ]);
  });
});

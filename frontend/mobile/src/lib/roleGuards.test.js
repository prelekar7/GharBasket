import { isRedirect } from "@tanstack/react-router";
import { describe, expect, it } from "vitest";
import { useSession } from "@/lib/session.js";
import { Route as captain } from "@/routes/_auth/captain.jsx";
import { Route as kitchen } from "@/routes/_auth/kitchen.jsx";
import { Route as packer } from "@/routes/_auth/packer.jsx";
import { Route as rider } from "@/routes/_auth/rider.jsx";

const GUARDS = { captain, kitchen, packer, rider };

/** @param {string} role @param {string | null} active */
const guard = (role, active) => {
  useSession.setState({ activeRole: /** @type {any} */ (active) });
  try {
    /** @type {any} */ (GUARDS)[role].options.beforeLoad();
    return null;
  } catch (e) {
    return e;
  }
};

describe("field role shells", () => {
  it.each(Object.keys(GUARDS))("%s lets its own role in and sends others to their home", (role) => {
    expect(guard(role, role)).toBeNull();
    expect(guard(role, null)).toBeNull();
    const other = role === "packer" ? "rider" : "packer";
    const sent = guard(role, other);
    expect(isRedirect(sent)).toBe(true);
    expect(/** @type {any} */ (sent).options.to).toBe(`/${other}`);
  });
});

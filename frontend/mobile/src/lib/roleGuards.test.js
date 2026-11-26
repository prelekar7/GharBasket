import { isRedirect } from "@tanstack/react-router";
import { describe, expect, it } from "vitest";
import { HOME_BY_ROLE, useSession } from "@/lib/session.js";
import { Route as captain } from "@/routes/_auth/captain.jsx";
import { Route as kitchen } from "@/routes/_auth/kitchen.jsx";
import { Route as packer } from "@/routes/_auth/packer.jsx";
import { Route as rider } from "@/routes/_auth/rider.jsx";

const GUARDS = { captain, kitchen_partner: kitchen, packer, rider };

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
    expect(/** @type {any} */ (sent).options.to).toBe(
      HOME_BY_ROLE[/** @type {"packer"} */ (other)],
    );
  });

  it("keeps the /kitchen URL for the kitchen_partner role", () => {
    expect(HOME_BY_ROLE.kitchen_partner).toBe("/kitchen");
    const sent = guard("captain", "kitchen_partner");
    expect(/** @type {any} */ (sent).options.to).toBe("/kitchen");
  });
});

describe("signIn", () => {
  /** @param {string} role @param {string} status */
  const grant = (role, status) => ({ role, scope_type: null, scope_id: null, status });
  const user = {
    id: "u",
    phone_e164: "+919800001005",
    name: null,
    locale: "en-IN",
    senior_mode: false,
  };

  it("offers only verified kitchen_partner grants and sends X-Active-Role with the role name", () => {
    useSession.getState().signIn(
      /** @type {any} */ ({
        ...user,
        roles: [
          grant("customer", "verified"),
          grant("rider", "kyc_pending"),
          grant("kitchen_partner", "suspended"),
        ],
      }),
    );
    expect(useSession.getState().roles).toEqual(["customer"]);
    useSession.getState().signIn(
      /** @type {any} */ ({
        ...user,
        roles: [{ ...grant("kitchen_partner", "verified"), scope_id: "h1" }],
      }),
    );
    expect(useSession.getState().roles).toEqual(["kitchen_partner"]);
    expect(useSession.getState().activeRole).toBe("kitchen_partner");
  });

  it("remembers the chosen role for the same person only, not the next one on the phone", () => {
    const two = (/** @type {string} */ id, /** @type {string} */ field) =>
      /** @type {any} */ ({
        ...user,
        id,
        roles: [grant("customer", "verified"), grant(field, "verified")],
      });
    // Arjun (customer and rider) chooses customer, then signs out.
    useSession.setState({ activeRole: null, roleOwner: null });
    useSession.getState().signIn(two("arjun", "rider"));
    expect(useSession.getState().activeRole).toBeNull(); // two roles: the chooser asks
    useSession.getState().setActiveRole("customer");
    useSession.getState().clear();
    // Kavita (customer and packer) signs in on the same phone: she is asked, not put in customer.
    useSession.getState().signIn(two("kavita", "packer"));
    expect(useSession.getState().activeRole).toBeNull();
    useSession.getState().setActiveRole("packer");
    useSession.getState().clear();
    // Kavita again: her own choice comes back (handbook ch. 03 §2).
    useSession.getState().signIn(two("kavita", "packer"));
    expect(useSession.getState().activeRole).toBe("packer");
  });

  it("does not reuse a role saved before the owner was recorded", () => {
    useSession.setState({ activeRole: "customer", roleOwner: null });
    useSession.getState().signIn(
      /** @type {any} */ ({
        ...user,
        roles: [grant("customer", "verified"), grant("packer", "verified")],
      }),
    );
    expect(useSession.getState().activeRole).toBeNull();
  });
});

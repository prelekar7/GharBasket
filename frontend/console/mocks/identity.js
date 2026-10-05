// Identity mocks for staff role management (M1 §3 `POST /v1/admin/user-roles`). The user lookup
// (`GET /v1/admin/users`) and `DELETE /v1/admin/user-roles/{id}` are our proposals. People are the
// backend seed's staff. No Aadhaar anywhere: M1 does not hold it.
import { HttpResponse, http } from "msw";
import { deny, istDay } from "./adminShared.js";
import { idempotent, mockStore, problem } from "./util.js";

/** Roles an admin may give here; the others come from Partners and KYC (M8). */
export const STAFF_ROLES = ["admin", "hub_manager", "franchise_owner", "ngo_coordinator"];

/**
 * @typedef {{ id: string, role: string, scope_type: "hub" | null, scope_id: string | null,
 *   scope_label: string | null, status: "verified" | "pending", given_by: string }} UserRole
 * @typedef {{ id: string, name: string, phone_e164: string, locale: "en-IN" | "hi-IN" | "mr-IN",
 *   last_seen_at: string | null, roles: UserRole[] }} AdminUser
 */

const customer = (/** @type {string} */ id) => ({
  id: `r-${id}-customer`,
  role: "customer",
  scope_type: /** @type {null} */ (null),
  scope_id: null,
  scope_label: null,
  status: /** @type {const} */ ("verified"),
  given_by: "Everyone who signs in",
});

/** @param {string} id @param {string} role @param {string} by @param {boolean} [hub] @returns {UserRole} */
const staff = (id, role, by, hub = false) => ({
  id: `r-${id}-${role}`,
  role,
  scope_type: hub ? "hub" : null,
  scope_id: hub ? "kharghar" : null,
  scope_label: hub ? "Kharghar hub" : null,
  status: "verified",
  given_by: by,
});

/** @type {() => AdminUser[]} */
const seedUsers = () => [
  {
    id: "u-neha",
    name: "Neha Iyer",
    phone_e164: "+919800001011",
    locale: "en-IN",
    last_seen_at: istDay(0, 9, 12),
    roles: [customer("neha"), staff("neha", "admin", "Seed setup")],
  },
  {
    id: "u-vikram",
    name: "Vikram Desai",
    phone_e164: "+919800001008",
    locale: "en-IN",
    last_seen_at: istDay(0, 8, 40),
    roles: [customer("vikram"), staff("vikram", "hub_manager", "Seed setup", true)],
  },
  {
    id: "u-sanjay",
    name: "Sanjay Mehta",
    phone_e164: "+919800001009",
    locale: "en-IN",
    last_seen_at: istDay(-1, 19, 5),
    roles: [customer("sanjay"), staff("sanjay", "franchise_owner", "Seed setup", true)],
  },
  {
    id: "u-meena",
    name: "Meena Jadhav",
    phone_e164: "+919800001003",
    locale: "mr-IN",
    last_seen_at: istDay(0, 7, 55),
    roles: [
      customer("meena"),
      {
        id: "r-meena-captain",
        role: "captain",
        scope_type: null,
        scope_id: null,
        scope_label: "Sunrise Towers",
        status: "verified",
        given_by: "Partners and KYC",
      },
    ],
  },
  {
    id: "u-lata",
    name: "Lata Shinde",
    phone_e164: "+919800001004",
    locale: "mr-IN",
    last_seen_at: istDay(0, 6, 30),
    roles: [
      customer("lata"),
      {
        id: "r-lata-kitchen",
        role: "kitchen_partner",
        scope_type: null,
        scope_id: null,
        scope_label: "Kharghar hub",
        status: "verified",
        given_by: "Partners and KYC",
      },
    ],
  },
  {
    id: "u-arjun",
    name: "Arjun Pawar",
    phone_e164: "+919800001005",
    locale: "hi-IN",
    last_seen_at: istDay(0, 5, 45),
    roles: [
      customer("arjun"),
      {
        id: "r-arjun-rider",
        role: "rider",
        scope_type: null,
        scope_id: null,
        scope_label: "Kharghar hub",
        status: "verified",
        given_by: "Partners and KYC",
      },
    ],
  },
  {
    id: "u-kavita",
    name: "Kavita More",
    phone_e164: "+919800001006",
    locale: "mr-IN",
    last_seen_at: istDay(-1, 5, 10),
    roles: [
      customer("kavita"),
      {
        id: "r-kavita-packer",
        role: "packer",
        scope_type: null,
        scope_id: null,
        scope_label: "Kharghar hub",
        status: "verified",
        given_by: "Partners and KYC",
      },
    ],
  },
  {
    id: "u-priya",
    name: "Priya Kulkarni",
    phone_e164: "+919800000001",
    locale: "en-IN",
    last_seen_at: istDay(0, 7, 2),
    roles: [customer("priya")],
  },
];

const users = () => mockStore("identity.users", seedUsers);

/** Who is signed in, by role, for "Given by" and the self-removal guard. */
const ME = { admin: "u-neha", hub_manager: "u-vikram", franchise_owner: "u-sanjay" };

const HUB_LABELS = /** @type {Record<string, string>} */ ({ kharghar: "Kharghar hub" });

/** The 10 digits of an Indian mobile number typed any usual way, or null. @param {string} text */
function tenDigits(text) {
  const d = text.replace(/\D/g, "").replace(/^(91|0)(?=\d{10}$)/, "");
  return /^[6-9]\d{9}$/.test(d) ? d : null;
}

export const identityHandlers = [
  // ?phone=… finds one person by number; ?staff=true lists everyone holding a staff role.
  http.get("*/v1/admin/users", ({ request }) => {
    const refused = deny(request, ["admin"]);
    if (refused) return refused;
    const url = new URL(request.url);
    const phone = url.searchParams.get("phone");
    if (phone !== null) {
      const d = tenDigits(phone);
      if (!d)
        return problem(422, "request.invalid", "Enter a 10-digit mobile number", {
          errors: [{ field: "phone", message: "Enter a 10-digit mobile number" }],
        });
      const items = users().filter((u) => u.phone_e164 === `+91${d}`);
      return HttpResponse.json({ items, next_cursor: null });
    }
    const items = users().filter((u) => u.roles.some((r) => STAFF_ROLES.includes(r.role)));
    return HttpResponse.json({ items, next_cursor: null });
  }),

  http.post(
    "*/v1/admin/user-roles",
    ({ request }) =>
      deny(request, ["admin"]) ??
      idempotent(request, (body) => {
        const user = users().find((u) => u.id === body?.user_id);
        if (!user) return problem(404, "identity.user_not_found", "No such person");
        if (!STAFF_ROLES.includes(body.role))
          return problem(422, "request.invalid", "Pick a staff role", {
            errors: [{ field: "role", message: "Pick a staff role" }],
          });
        const hubRole = body.role === "hub_manager" || body.role === "franchise_owner";
        if (hubRole && !HUB_LABELS[body.scope_id])
          return problem(422, "identity.scope_required", "Choose a hub for this role");
        const scope = hubRole ? body.scope_id : null;
        if (user.roles.some((r) => r.role === body.role && r.scope_id === scope))
          return problem(409, "identity.role_exists", "They already have this role");
        const role = staff(user.id.slice(2), body.role, "Neha Iyer", hubRole);
        role.id = `r-${user.id.slice(2)}-${body.role}-${Date.now() % 1e6}`;
        role.scope_id = scope;
        role.scope_label = scope ? HUB_LABELS[scope] : null;
        user.roles.push(role);
        return HttpResponse.json({ ...role, user_id: user.id }, { status: 201 });
      }),
  ),

  http.delete("*/v1/admin/user-roles/:id", ({ request, params }) => {
    const refused = deny(request, ["admin"]);
    if (refused) return refused;
    const user = users().find((u) => u.roles.some((r) => r.id === params.id));
    const role = user?.roles.find((r) => r.id === params.id);
    if (!user || !role) return problem(404, "identity.role_not_found", "No such role");
    if (!STAFF_ROLES.includes(role.role))
      return problem(409, "identity.role_not_removable", "This role is managed elsewhere");
    const me = ME[/** @type {keyof typeof ME} */ (request.headers.get("x-gb-role") ?? "")];
    if (role.role === "admin" && user.id === me)
      return problem(
        409,
        "identity.cannot_remove_own_admin",
        "You cannot remove your own admin role",
      );
    user.roles = user.roles.filter((r) => r.id !== role.id);
    return new HttpResponse(null, { status: 204 });
  }),
];

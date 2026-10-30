// Identity mocks for staff role management. `POST /v1/admin/user-roles` is the real contract's (M1 §3.1: body
// { phone_e164, role, scope_id? }, answer RoleGrantOut with no `id`). The user lookup (`GET /v1/admin/users`)
// and `DELETE /v1/admin/user-roles/{id}` are a @proposal (not in the contract yet). People are the backend
// seed's staff. No Aadhaar anywhere: M1 does not hold it.
import { HttpResponse, http } from "msw";
import { deny, istDay } from "./adminShared.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { fieldError, idempotent, mockStore, mockUuid, problem } from "./util.js";

/** Roles an admin may give here; the others come from Partners and KYC (M8). */
export const STAFF_ROLES = [
  "admin",
  "hub_manager",
  "franchise_owner",
  "ngo_coordinator",
  "catalog_editor",
];

/** The contract's role enum (RoleGrantIn), and the place each role is tied to (`scope_type`). */
const ROLE_SCOPE = /** @type {Record<string, "hub" | "society" | "ngo" | null>} */ ({
  captain: "society",
  kitchen_partner: "hub",
  rider: "hub",
  packer: "hub",
  hub_manager: "hub",
  franchise_owner: "hub",
  ngo_coordinator: "ngo",
  admin: null,
  catalog_editor: null,
});

/**
 * `id` and `given_by` are for the proposed lookup and delete; the contract's role grant has neither. `status`
 * is the contract's.
 * @typedef {{ id: string, role: string, scope_type: "hub" | "society" | "ngo" | null, scope_id: string | null,
 *   scope_label: string | null, status: "applied" | "kyc_pending" | "verified" | "suspended", given_by: string }} UserRole
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
  scope_id: hub ? KHARGHAR_HUB_ID : null,
  scope_label: hub ? "Kharghar hub" : null,
  status: "verified",
  given_by: by,
});

/** @type {() => AdminUser[]} */
const seedUsers = () => [
  {
    id: mockUuid("b600", 1),
    name: "Neha Iyer",
    phone_e164: "+919800001011",
    locale: "en-IN",
    last_seen_at: istDay(0, 9, 12),
    roles: [customer("neha"), staff("neha", "admin", "Seed setup")],
  },
  {
    id: mockUuid("b600", 2),
    name: "Vikram Desai",
    phone_e164: "+919800001008",
    locale: "en-IN",
    last_seen_at: istDay(0, 8, 40),
    roles: [customer("vikram"), staff("vikram", "hub_manager", "Seed setup", true)],
  },
  {
    id: mockUuid("b600", 3),
    name: "Sanjay Mehta",
    phone_e164: "+919800001009",
    locale: "en-IN",
    last_seen_at: istDay(-1, 19, 5),
    roles: [customer("sanjay"), staff("sanjay", "franchise_owner", "Seed setup", true)],
  },
  {
    id: mockUuid("b600", 4),
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
    id: mockUuid("b600", 5),
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
    id: mockUuid("b600", 6),
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
    id: mockUuid("b600", 7),
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
    id: mockUuid("b600", 8),
    name: "Priya Kulkarni",
    phone_e164: "+919800000001",
    locale: "en-IN",
    last_seen_at: istDay(0, 7, 2),
    roles: [customer("priya")],
  },
];

const users = () => mockStore("identity.users", seedUsers);

/** Who is signed in, by role, for "Given by" and the self-removal guard. */
const ME = {
  admin: mockUuid("b600", 1),
  hub_manager: mockUuid("b600", 2),
  franchise_owner: mockUuid("b600", 3),
};

const HUB_LABELS = /** @type {Record<string, string>} */ ({ [KHARGHAR_HUB_ID]: "Kharghar hub" });

/** The 10 digits of an Indian mobile number typed any usual way, or null. @param {string} text */
function tenDigits(text) {
  const d = text.replace(/\D/g, "").replace(/^(91|0)(?=\d{10}$)/, "");
  return /^[6-9]\d{9}$/.test(d) ? d : null;
}

export const identityHandlers = [
  // GET /v1/admin/users → @proposal (not in the contract yet): ?phone=… finds one person by number; ?staff=true
  // lists everyone holding a staff role.
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

  // POST /v1/admin/user-roles (M1 §3.1, admin, Idempotency-Key required): { phone_e164, role, scope_id? } →
  // 201 RoleGrantOut { role, scope_type, scope_id, status, user_id }. 404 identity.user_not_found (they must
  // sign in once), 422 identity.invalid_scope (a hub role needs a hub id, admin takes none), 409 identity.role_exists.
  http.post(
    "*/v1/admin/user-roles",
    ({ request }) =>
      deny(request, ["admin"]) ??
      idempotent(request, (body) => {
        /** @type {ReturnType<typeof fieldError>[]} */
        const errors = [];
        const object = typeof body === "object" && body !== null && !Array.isArray(body);
        for (const k of Object.keys(object ? body : {}))
          if (!["phone_e164", "role", "scope_id"].includes(k))
            errors.push(fieldError(k, "Extra inputs are not permitted", "extra_forbidden"));
        if (typeof body?.phone_e164 !== "string" || !/^\+[1-9]\d{9,14}$/.test(body.phone_e164))
          errors.push(
            fieldError("phone_e164", "Value error, not a valid phone number", "value_error"),
          );
        if (!(body?.role in ROLE_SCOPE))
          errors.push(fieldError("role", "Input should be a valid role", "enum"));
        if (errors.length)
          return problem(422, "request.invalid", "Request validation failed", { errors });
        const scopeType = ROLE_SCOPE[body.role];
        const scopeId = body.scope_id ?? null;
        if ((scopeType === null) !== (scopeId === null))
          return problem(
            422,
            "identity.invalid_scope",
            scopeType
              ? `A ${body.role} role needs the id of its ${scopeType}.`
              : `A ${body.role} role is not limited to one place; leave scope_id out.`,
          );
        const user = users().find((u) => u.phone_e164 === body.phone_e164);
        if (!user)
          return problem(
            404,
            "identity.user_not_found",
            "No account with this number; they must sign in once.",
          );
        if (scopeType === "hub" && !HUB_LABELS[scopeId])
          return problem(422, "identity.invalid_scope", "No such hub.");
        if (user.roles.some((r) => r.role === body.role && r.scope_id === scopeId))
          return problem(409, "identity.role_exists", "This user already has this role.");
        user.roles.push({
          id: `r-${user.id.slice(-4)}-${body.role}-${user.roles.length}`,
          role: body.role,
          scope_type: scopeType,
          scope_id: scopeId,
          scope_label: scopeId ? (HUB_LABELS[scopeId] ?? null) : null,
          status: "verified",
          given_by: "Neha Iyer",
        });
        return HttpResponse.json(
          {
            role: body.role,
            scope_type: scopeType,
            scope_id: scopeId,
            status: "verified",
            user_id: user.id,
          },
          { status: 201 },
        );
      }),
  ),

  // DELETE /v1/admin/user-roles/{id} → @proposal (not in the contract yet): takes a staff role away (the id is the
  // role's id in the proposed lookup).
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

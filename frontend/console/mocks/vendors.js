// Vendor mocks: the real M6 §3.1 contract (built, backend/api/openapi.json). Vendors per hub, listed by
// name; POST needs an Idempotency-Key; PATCH needs If-Match (the vendor's version) and deactivates
// instead of deleting. `score` is the last weekly score (0 to 1), null until the vendor is scored; the
// score's four parts are not in the contract (backend ask), so only the score shows. Seed: the pilot
// hub's default vendor "Shinde Traders" (backend seed) plus two made-up sellers from the Vendors design.
import { HttpResponse, http } from "msw";
import { checkIfMatch, hubGuard, withEtag } from "./hubops.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { fieldError, idempotent, mockStore, mockUuid, problem } from "./util.js";

export const VENDOR_KINDS = ["apmc_trader", "fpo", "farmer", "wholesaler"];
const E164 = /^\+[1-9]\d{7,14}$/;
const FIELDS = ["name", "kind", "phone", "whatsapp", "is_default", "notes", "active"];

/**
 * @typedef {{ id: string, hub_id: string, name: string, kind: string, phone: string | null,
 *   whatsapp: boolean, is_default: boolean, notes: string | null, active: boolean,
 *   score: number | null, version: number, created_at: string }} Vendor
 */

/** @type {() => { vendors: Vendor[] }} */
export const vendorState = () =>
  mockStore("vendors", () => {
    /** @param {number} n @param {Partial<Vendor>} v @returns {Vendor} */
    const vendor = (n, v) => ({
      id: mockUuid("b200", n),
      hub_id: KHARGHAR_HUB_ID,
      kind: "apmc_trader",
      phone: null,
      whatsapp: true,
      is_default: false,
      notes: null,
      active: true,
      score: null,
      version: 1,
      created_at: "2026-09-01T06:30:00Z",
      name: "",
      ...v,
    });
    return {
      vendors: [
        vendor(1, {
          name: "Shinde Traders",
          phone: "+919800002001",
          is_default: true,
          notes: "Vegetables",
          score: 0.94,
        }),
        vendor(2, {
          name: "Patil Farm Fresh",
          kind: "farmer",
          phone: "+919800002002",
          notes: "Leafy greens",
          score: 0.86,
        }),
        vendor(3, {
          name: "Gokul Dairy Producers",
          kind: "fpo",
          phone: "+919800002003",
          notes: "Dairy and eggs",
          score: 0.97,
        }),
      ],
    };
  });

/** @param {string} id */
export const findVendor = (id) => vendorState().vendors.find((v) => v.id === id);

/** @param {Vendor} v @param {string} name */
const sameName = (v, name) => v.name.toLowerCase() === name.toLowerCase();

/** Makes `vendor` the hub's one default: the previous default loses it (and its version moves). @param {Vendor} vendor */
function takeDefault(vendor) {
  for (const other of vendorState().vendors) {
    if (other !== vendor && other.is_default) {
      other.is_default = false;
      other.version += 1;
    }
  }
  vendor.is_default = true;
}

/**
 * The 422 for a body's vendor fields, or null. Mirrors VendorIn / VendorPatch.
 * @param {any} body @param {boolean} partial a PATCH: every field optional, and only some may be null
 */
function invalid(body, partial) {
  /** @type {ReturnType<typeof fieldError>[]} */
  const errors = [];
  if (!body || typeof body !== "object" || Array.isArray(body))
    return problem(422, "request.invalid", "Send a JSON object");
  for (const key of Object.keys(body)) {
    if (!FIELDS.includes(key) || (!partial && key === "active"))
      errors.push(fieldError(key, "Not a vendor field", "extra_forbidden"));
  }
  const name = body.name;
  if (
    name !== undefined &&
    name !== null &&
    (typeof name !== "string" || name.trim().length < 2 || name.trim().length > 80)
  )
    errors.push(fieldError("name", "Use 2 to 80 characters", "string_length"));
  if (!partial && name === undefined) errors.push(fieldError("name", "Field required", "missing"));
  if (body.kind != null && !VENDOR_KINDS.includes(body.kind))
    errors.push(fieldError("kind", "Choose a kind of vendor", "enum"));
  if (!partial && body.kind === undefined)
    errors.push(fieldError("kind", "Field required", "missing"));
  if (body.phone != null && !(typeof body.phone === "string" && E164.test(body.phone)))
    errors.push(fieldError("phone", "Use an international number such as +919800002001"));
  if (body.notes != null && !(typeof body.notes === "string" && body.notes.length <= 500))
    errors.push(fieldError("notes", "Use up to 500 characters", "string_too_long"));
  for (const key of ["whatsapp", "is_default", "active"]) {
    if (body[key] != null && typeof body[key] !== "boolean")
      errors.push(fieldError(key, "Use true or false", "bool_type"));
  }
  // A field that may not be null, sent as null, is 422 (M6 §3.1 as built).
  for (const key of ["name", "kind", "whatsapp", "is_default", "active"])
    if (body[key] === null) errors.push(fieldError(key, "May not be null", "none_not_allowed"));
  return errors.length
    ? problem(422, "request.invalid", "Check the vendor’s details", { errors })
    : null;
}

export const vendorsHandlers = [
  http.get("*/v1/hubs/:id/vendors", ({ params, request }) => {
    const refused = hubGuard(request, params.id);
    if (refused) return refused;
    const active = new URL(request.url).searchParams.get("active");
    const items = vendorState()
      .vendors.filter((v) => active === null || v.active === (active === "true"))
      .sort((a, b) => a.name.localeCompare(b.name));
    return HttpResponse.json({ items });
  }),

  http.post("*/v1/hubs/:id/vendors", ({ params, request }) =>
    idempotent(request, (body) => {
      const refused = hubGuard(request, params.id, { write: true });
      if (refused) return refused;
      const bad = invalid(body, false);
      if (bad) return bad;
      const name = body.name.trim();
      if (vendorState().vendors.some((v) => sameName(v, name)))
        return problem(409, "hub_ops.vendor_name_taken", "A vendor with this name already exists");
      const { vendors } = vendorState();
      /** @type {Vendor} */
      const vendor = {
        id: mockUuid("b200", vendors.length + 1),
        hub_id: KHARGHAR_HUB_ID,
        name,
        kind: body.kind,
        phone: body.phone ?? null,
        whatsapp: body.whatsapp ?? true,
        is_default: false,
        notes: body.notes ?? null,
        active: true,
        score: null,
        version: 1,
        created_at: new Date().toISOString(),
      };
      vendors.push(vendor);
      if (body.is_default) takeDefault(vendor);
      return withEtag(vendor, vendor.version, 201);
    }),
  ),

  http.patch("*/v1/vendors/:id", async ({ params, request }) => {
    const vendor = findVendor(String(params.id));
    if (!vendor) return problem(404, "hub_ops.vendor_not_found", "No such vendor");
    const refused = hubGuard(request, vendor.hub_id, { write: true });
    if (refused) return refused;
    const stale = checkIfMatch(request, vendor.version);
    if (stale) return stale;
    /** @type {any} */
    const body = await request.json().catch(() => null);
    const bad = invalid(body, true);
    if (bad) return bad;
    if (
      body.name != null &&
      vendorState().vendors.some((v) => v !== vendor && sameName(v, body.name.trim()))
    )
      return problem(409, "hub_ops.vendor_name_taken", "A vendor with this name already exists");

    /** @type {Record<string, any>} */
    const next = { ...vendor };
    for (const key of FIELDS) if (body[key] !== undefined) next[key] = body[key];
    next.name = next.name.trim();
    if (!next.active) next.is_default = false; // deactivating clears the default, and an inactive vendor never has it
    const changed = FIELDS.some((k) => next[k] !== /** @type {any} */ (vendor)[k]);
    if (changed) {
      const becomesDefault = next.is_default && !vendor.is_default;
      Object.assign(vendor, next, { version: vendor.version + 1 });
      if (becomesDefault) takeDefault(vendor);
    }
    return withEtag(vendor, vendor.version);
  }),
];

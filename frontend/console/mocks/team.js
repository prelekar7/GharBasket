// Team mocks for the franchise pages: GET /v1/hubs/{hub_id}/staff (M1 section 3.6, HubStaff). The people are the
// backend seed (tools/seed.py). The customers page is M12's GET /v1/analytics/societies (see analytics.js).
import { HttpResponse, http } from "msw";
import { SOCIETY_ID } from "./ids.js";
import { mockStore, mockUuid, problem, roleOf } from "./util.js";

/** @typedef {import("../app/(console)/(franchise)/staff/data.js").StaffMember} StaffMember */

/**
 * @param {number} n @param {string | null} name @param {string} phone @param {string} role
 * @param {Partial<StaffMember>} [more]
 * @returns {StaffMember}
 */
const member = (n, name, phone, role, more = {}) => ({
  grant_id: mockUuid("b900", n),
  user_id: mockUuid("b901", n),
  name,
  phone_masked: `******${phone}`,
  role,
  works_as: ["captain", "kitchen_partner", "rider", "packer"].includes(role) ? "partner" : "staff",
  society: null,
  status: "verified",
  since: "2026-10-01T10:00:00+05:30",
  ...more,
});

/** @type {() => StaffMember[]} By role, then name, like the API. */
const seedStaff = () => [
  member(1, "Savita Naik", "1012", "captain", {
    society: { id: SOCIETY_ID["sunrise-towers"], name: "Sunrise Towers" },
  }),
  member(2, "Sanjay Mehta", "1009", "franchise_owner"),
  member(3, "Vikram Desai", "1008", "hub_manager"),
  member(4, "Lata Shinde", "1005", "kitchen_partner"),
  member(5, "Kavita More", "1007", "packer"),
  member(6, "Arjun Pawar", "1006", "rider", { status: "kyc_pending", since: null }),
  member(7, null, "4321", "hub_manager", {
    user_id: null,
    status: "invited",
    since: "2026-11-02T11:30:00+05:30",
  }),
];

const staff = () => mockStore("team-staff", seedStaff);

export const teamHandlers = [
  http.get("*/v1/hubs/:hub/staff", ({ request }) =>
    roleOf(request)
      ? HttpResponse.json({ items: staff(), next_cursor: null })
      : problem(401, "auth.unauthenticated", "Sign in again"),
  ),
];

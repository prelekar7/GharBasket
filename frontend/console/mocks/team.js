// Team mocks for the franchise pages. The people are the backend seed (tools/seed.py); "today" and earnings
// figures are made up. Paths are our proposals until M1/M8 publish theirs: GET /v1/hubs/{hub}/staff. The customers page is
// M12's GET /v1/analytics/societies (see analytics.js).
import { HttpResponse, http } from "msw";
import { mockStore, problem, roleOf } from "./util.js";

/**
 * @typedef {"active" | "verified" | "fix_bank_details" | "invited"} StaffStatus
 * @typedef {{ id: string, name: string, role: string, works_as: "staff" | "partner", society?: string,
 *   today: string, last_week_paise: number | null, status: StaffStatus }} StaffMember
 */

/** @type {() => StaffMember[]} */
const seedStaff = () => [
  {
    id: "vikram-desai",
    name: "Vikram Desai",
    role: "Hub manager",
    works_as: "staff",
    today: "On shift",
    last_week_paise: null,
    status: "active",
  },
  {
    id: "kavita-more",
    name: "Kavita More",
    role: "Packer",
    works_as: "partner",
    today: "Packed 58 bags",
    last_week_paise: 210_000,
    status: "verified",
  },
  {
    id: "arjun-pawar",
    name: "Arjun Pawar",
    role: "Rider",
    works_as: "partner",
    today: "Route 1, two stops",
    last_week_paise: 178_000,
    status: "fix_bank_details",
  },
  {
    id: "meena-jadhav",
    name: "Meena Jadhav",
    role: "Society Captain",
    works_as: "partner",
    society: "Sunrise Towers",
    today: "46 boxes handed over",
    last_week_paise: 128_000,
    status: "verified",
  },
  {
    id: "lata-shinde",
    name: "Lata Shinde",
    role: "Kitchen partner",
    works_as: "partner",
    today: "18 tiffins",
    last_week_paise: 329_000,
    status: "verified",
  },
];

const staff = () => mockStore("team-staff", seedStaff);

export const teamHandlers = [
  http.get("*/v1/hubs/:hub/staff", ({ request }) =>
    roleOf(request)
      ? HttpResponse.json({ items: staff(), next_cursor: null })
      : problem(401, "auth.unauthenticated", "Sign in again"),
  ),
];

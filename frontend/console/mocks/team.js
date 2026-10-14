// Team and customer mocks for the franchise pages. The people are the backend seed (tools/seed.py);
// "today" and earnings figures are made up. Paths are our proposals until M1/M8 and the analytics
// module publish theirs: GET /v1/hubs/{hub}/staff, POST /v1/hubs/{hub}/staff-invitations (idempotent)
// and GET /v1/analytics/customer-summary (aggregates only, M12 open question).
import { HttpResponse, http } from "msw";
import { mulDivRound } from "../lib/money.js";
import { idempotent, mockStore, problem, roleOf } from "./util.js";

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

const ROLES = ["Kitchen partner", "Rider", "Packer", "Society Captain"];

/**
 * @typedef {{ id: string, name: string, status: "live" | "unlocking" | "prospect", active_households: number | null,
 *   paused_households: number | null, approved_on_time_bp: number | null, average_box_paise: number | null }} SocietyRow
 */
/** @type {SocietyRow[]} */
const SOCIETIES = [
  {
    id: "sunrise-towers",
    name: "Sunrise Towers",
    status: "live",
    active_households: 50,
    paused_households: 4,
    approved_on_time_bp: 9200,
    average_box_paise: 64_000,
  },
  {
    id: "krishna-kunj",
    name: "Krishna Kunj",
    status: "live",
    active_households: 14,
    paused_households: 1,
    approved_on_time_bp: 8600,
    average_box_paise: 56_000,
  },
  {
    id: "green-valley-heights",
    name: "Green Valley Heights",
    status: "unlocking",
    active_households: null,
    paused_households: null,
    approved_on_time_bp: null,
    average_box_paise: null,
  },
  {
    id: "shree-ganesh-chs",
    name: "Shree Ganesh CHS",
    status: "unlocking",
    active_households: null,
    paused_households: null,
    approved_on_time_bp: null,
    average_box_paise: null,
  },
  {
    id: "panchavati-residency",
    name: "Panchavati Residency",
    status: "prospect",
    active_households: null,
    paused_households: null,
    approved_on_time_bp: null,
    average_box_paise: null,
  },
];

export const teamHandlers = [
  http.get("*/v1/hubs/:hub/staff", ({ request }) =>
    roleOf(request)
      ? HttpResponse.json({ items: staff(), next_cursor: null })
      : problem(401, "auth.unauthenticated", "Sign in again"),
  ),

  // Invite: the phone number goes to the server and is not kept or shown again.
  http.post("*/v1/hubs/:hub/staff-invitations", ({ request }) =>
    idempotent(request, (body) => {
      const name = String(body?.name ?? "").trim();
      const phone = String(body?.phone ?? "").replace(/\D/g, "");
      if (!name || !/^[6-9]\d{9}$/.test(phone) || !ROLES.includes(body?.role)) {
        return problem(422, "request.invalid", "Check the name, phone number and role");
      }
      /** @type {StaffMember} */
      const person = {
        id: `invited-${staff().length + 1}`,
        name,
        role: body.role,
        works_as: "partner",
        today: "Invite sent",
        last_week_paise: null,
        status: "invited",
      };
      staff().push(person);
      return HttpResponse.json(person, { status: 201 });
    }),
  ),

  // Aggregates only: how the hub's families are doing, never who they are.
  http.get("*/v1/analytics/customer-summary", ({ request }) => {
    const role = roleOf(request);
    if (role !== "franchise_owner" && role !== "admin") {
      return problem(403, "analytics.forbidden", "This page is for franchise owners");
    }
    const live = SOCIETIES.filter((s) => s.active_households !== null);
    const active = live.reduce((n, s) => n + (s.active_households ?? 0), 0);
    const weighted = live.reduce(
      (n, s) => n + (s.active_households ?? 0) * (s.approved_on_time_bp ?? 0),
      0,
    );
    return HttpResponse.json({
      hub_id: "kharghar",
      active_households: active,
      active_households_change: 6,
      paused_households: live.reduce((n, s) => n + (s.paused_households ?? 0), 0),
      approved_on_time_bp: mulDivRound(weighted, 1, active),
      joining_societies: SOCIETIES.filter((s) => s.status === "unlocking").map((s) => s.name),
      societies: SOCIETIES,
    });
  }),
];

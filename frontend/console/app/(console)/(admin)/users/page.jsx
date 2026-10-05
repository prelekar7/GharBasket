import { NoAccess } from "@/components/admin/Page.jsx";
import { isForbidden } from "@/lib/admin/safe.js";
import { ApiProblem, apiGet } from "@/lib/api.js";
import { listHubs } from "@/lib/hub.js";
import { UsersView } from "./UsersView.jsx";

export const metadata = { title: "Users and roles" };

/**
 * @param {{ searchParams: Promise<{ phone?: string }> }} props
 */
export default async function Page({ searchParams }) {
  const { phone } = await searchParams;
  try {
    const hubs = await listHubs();
    /** @type {import("./UsersView.jsx").Lookup} */
    let lookup;
    if (phone) {
      try {
        const { items } = await apiGet("/v1/admin/users", { searchParams: { phone } });
        lookup = items[0] ? { kind: "found", person: items[0] } : { kind: "none" };
      } catch (error) {
        if (!(error instanceof ApiProblem) || error.status !== 422) throw error;
        lookup = { kind: "invalid" };
      }
    } else {
      const { items } = await apiGet("/v1/admin/users", { searchParams: { staff: true } });
      lookup = { kind: "staff", people: items };
    }
    return <UsersView phone={phone ?? ""} lookup={lookup} hubs={hubs} now={Date.now()} />;
  } catch (error) {
    if (isForbidden(error)) return <NoAccess title="Users and roles" />;
    throw error;
  }
}

import { PageHead } from "@/components/admin/Page.jsx";
import { apiGet } from "@/lib/api.js";
import { getRole } from "@/lib/auth.js";
import { currentHub } from "@/lib/hub.js";
import { monthOptions } from "@/lib/months.js";
import { currentNgoId } from "@/lib/ngo.js";
import { NOT_SHIPPED_MESSAGE, orNotShipped } from "@/lib/proposal.js";
import { HandoffsView } from "./HandoffsView.jsx";

export const metadata = { title: "NGO hand-offs" };

/**
 * M9 NGO hand-offs: what the hub gave the NGO, where it went, and the month's report. An NGO coordinator sees
 * their own NGO; hub staff see the hand-offs of the hub.
 * @param {{ searchParams: Promise<{ month?: string }> }} props
 */
export default async function Page({ searchParams }) {
  const { month: asked } = await searchParams;
  const [hub, role] = await Promise.all([currentHub(), getRole()]);
  const months = monthOptions(Date.now());
  const month = months.some((m) => m.value === asked)
    ? /** @type {string} */ (asked)
    : months[0].value;
  const ngoId = role === "ngo_coordinator" ? await currentNgoId() : undefined;
  const [list, ready, ngos, offers] = await Promise.all([
    orNotShipped(
      apiGet("/v1/ngo-handoffs", { searchParams: { hub_id: hub.id, ngo_id: ngoId, month } }),
    ),
    orNotShipped(apiGet("/v1/ngo-handoffs/ready", { searchParams: { hub_id: hub.id } })),
    orNotShipped(
      apiGet("/v1/ngos", {
        searchParams: { hub_id: role === "ngo_coordinator" ? undefined : hub.id },
      }),
    ),
    orNotShipped(apiGet("/v1/ngo/offers", { searchParams: { hub_id: hub.id, status: "open" } })),
  ]);
  // M9 is not built yet: the whole screen needs all four reads.
  if (!list || !ready || !ngos || !offers)
    return <PageHead title="NGO hand-offs">{NOT_SHIPPED_MESSAGE}</PageHead>;
  return (
    <HandoffsView
      handoffs={list.items}
      report={list.report}
      ready={ready}
      ngos={ngos.items}
      offers={offers.items}
      months={months}
      month={month}
      role={role}
      hubId={hub.id}
    />
  );
}

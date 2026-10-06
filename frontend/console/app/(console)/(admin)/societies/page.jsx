import Link from "next/link";
import { createLoader, parseAsString } from "nuqs/server";
import { NoAccess } from "@/components/admin/Page.jsx";
import { isForbidden } from "@/lib/admin/safe.js";
import { apiGet } from "@/lib/api.js";
import { apiAll } from "@/lib/api-pages.js";
import { currentHub, listHubs } from "@/lib/hub.js";
import { orNotShipped } from "@/lib/proposal.js";
import { SocietiesView } from "./SocietiesView.jsx";

export const metadata = { title: "Societies" };

const loadParams = createLoader({ society: parseAsString });

/**
 * Societies as the page edits them. The admin list (`GET /v1/admin/societies`, with the boundary, hub,
 * unlock threshold and towers) is a @proposal, not in the contract yet. Without it the page shows the
 * public list (`GET /v1/societies`, a summary only) and fills in the selected society from the real
 * detail (towers, flats: `GET /v1/societies/{id}`) and `GET /v1/societies/{id}/unlock` (the threshold).
 * @param {string} hubId @param {string | null} selected
 * @returns {Promise<{ items: import("@/lib/admin/types.js").Society[], limited: boolean }>}
 */
async function loadSocieties(hubId, selected) {
  const admin = await orNotShipped(
    apiGet("/v1/admin/societies", { searchParams: { hub_id: hubId, with_unassigned: true } }),
  );
  if (admin) return { items: admin.items, limited: false };
  const { items: summaries } = await apiAll("/v1/societies", {}, { limit: 50 });
  const items = summaries.map((/** @type {Record<string, any>} */ s) => ({
    id: s.id,
    slug: s.slug,
    name: s.name,
    area: s.area ?? "",
    status: s.status,
    hub_id: null,
    unlock_threshold: 0,
    flats: null,
    towers: [],
    boundary: null,
    location: null,
    version: 0,
  }));
  const open = items.find((s) => s.id === selected) ?? items[0];
  if (open) {
    const [detail, unlock] = await Promise.all([
      apiGet(`/v1/societies/${open.id}`),
      apiGet(`/v1/societies/${open.id}/unlock`),
    ]);
    Object.assign(open, {
      flats: detail.flats,
      towers: detail.towers,
      unlock_threshold: unlock.threshold,
    });
  }
  return { items, limited: true };
}

/** @param {{ searchParams: Promise<import("nuqs/server").SearchParams> }} props */
export default async function Page({ searchParams }) {
  try {
    const [hub, { society }] = await Promise.all([currentHub(), loadParams(searchParams)]);
    const [{ items, limited }, requests, hubs] = await Promise.all([
      loadSocieties(hub.id, society),
      // @proposal (not in the contract yet): the society requests review
      orNotShipped(apiGet("/v1/admin/society-requests")),
      listHubs(),
    ]);
    const fresh = (requests?.items ?? []).filter(
      (/** @type {{ status: string }} */ r) => r.status === "received",
    ).length;
    return (
      <>
        <div className="flex flex-wrap items-end gap-x-4 gap-y-1">
          <h1 className="font-display text-[28px] leading-[34px] font-semibold">Societies</h1>
          <Link
            href="/societies/requests"
            className="min-h-6 rounded-xs text-link underline outline-none focus-visible:shadow-focus"
          >
            Requests{fresh > 0 ? ` · ${fresh} new` : ""}
          </Link>
        </div>
        <SocietiesView hubName={hub.name} societies={items} hubs={hubs} limited={limited} />
      </>
    );
  } catch (error) {
    if (isForbidden(error)) return <NoAccess title="Societies" />;
    throw error;
  }
}

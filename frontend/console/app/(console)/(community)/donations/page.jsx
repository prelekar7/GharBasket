import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { DonationsView } from "./DonationsView.jsx";

export const metadata = { title: "Donation verification" };

/** M9 donation verification: what the return leg brought back, counted by the hub. */
export default async function Page() {
  const hub = await currentHub();
  const queue = (/** @type {string} */ status) =>
    apiGet("/v1/admin/donations", { searchParams: { hub_id: hub.id, status } });
  // The queue (picked up, oldest first), then what was verified lately; the rates and drives are for the preview.
  const [toVerify, verified, rates, drives] = await Promise.all([
    queue("picked_up"),
    queue("verified"),
    apiGet("/v1/point-rates"),
    apiGet("/v1/drives"),
  ]);
  return (
    <DonationsView
      donations={[...toVerify.items, ...verified.items]}
      rates={rates.items}
      drives={drives.items}
    />
  );
}

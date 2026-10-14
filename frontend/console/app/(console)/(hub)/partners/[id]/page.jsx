import { notFound } from "next/navigation";
import { ApiProblem } from "@/lib/api.js";
import { getPartner } from "./data.js";
import { PartnerProfileView } from "./PartnerProfileView.jsx";

export const metadata = { title: "Partner" };

/** @param {{ params: Promise<{ id: string }> }} props */
export default async function Page({ params }) {
  const { id } = await params;
  try {
    return <PartnerProfileView data={await getPartner(id)} />;
  } catch (e) {
    if (e instanceof ApiProblem && e.status === 404) notFound();
    throw e;
  }
}

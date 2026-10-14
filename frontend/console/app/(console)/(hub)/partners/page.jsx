import { getPartnerLists } from "./data.js";
import { PartnersView } from "./PartnersView.jsx";

export const metadata = { title: "Partners and KYC" };

export default async function Page() {
  const { applications, partners, now } = await getPartnerLists();
  return <PartnersView applications={applications} partners={partners} now={now} />;
}

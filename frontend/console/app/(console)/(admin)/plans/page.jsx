import { apiGet } from "@/lib/api.js";
import { PlansView } from "./PlansView.jsx";

export const metadata = { title: "Plans" };

export default async function Page() {
  const { items } = await apiGet("/v1/plans");
  return <PlansView plans={items} />;
}

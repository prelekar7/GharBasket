import { DeliveriesView } from "./DeliveriesView.jsx";
import { getDeliveries } from "./data.js";

export const metadata = { title: "Deliveries" };

export default async function Page() {
  const data = await getDeliveries();
  return <DeliveriesView {...data} />;
}

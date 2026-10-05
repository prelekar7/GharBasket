import { getVehicles } from "./data.js";
import { VehiclesView } from "./VehiclesView.jsx";

export const metadata = { title: "Vehicles" };

export default async function Page() {
  const { items } = await getVehicles();
  return <VehiclesView vehicles={items} />;
}

import { CustomersView } from "./CustomersView.jsx";
import { loadCustomers } from "./data.js";

export const metadata = { title: "Customers" };

export default async function Page() {
  return <CustomersView summary={await loadCustomers()} />;
}

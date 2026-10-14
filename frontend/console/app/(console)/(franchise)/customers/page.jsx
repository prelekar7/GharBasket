import { CustomersView } from "./CustomersView.jsx";
import { loadCustomers } from "./data.js";

export const metadata = { title: "Customers" };

/** @param {{ searchParams: Promise<{ source?: string }> }} props */
export default async function Page({ searchParams }) {
  const { source } = await searchParams;
  return <CustomersView summary={await loadCustomers(source)} />;
}

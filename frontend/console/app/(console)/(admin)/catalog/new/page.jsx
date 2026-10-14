import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { ProductEditView } from "../ProductEditView.jsx";

export const metadata = { title: "Add product" };

export default async function Page() {
  const [hub, categories] = await Promise.all([currentHub(), apiGet("/v1/categories")]);
  return <ProductEditView product={null} categories={categories.items} hubName={hub.name} />;
}

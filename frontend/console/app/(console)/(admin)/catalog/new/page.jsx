import { apiAll } from "@/lib/api-pages.js";
import { categoriesOf } from "@/lib/catalog.js";
import { currentHub } from "@/lib/hub.js";
import { ProductEditView } from "../ProductEditView.jsx";

export const metadata = { title: "Add product" };

export default async function Page() {
  const [hub, products] = await Promise.all([currentHub(), apiAll("/v1/products")]);
  return (
    <ProductEditView product={null} categories={categoriesOf(products.items)} hubName={hub.name} />
  );
}

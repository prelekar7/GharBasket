import { apiGet } from "@/lib/api.js";
import { TemplatesView } from "./TemplatesView.jsx";

export const metadata = { title: "Templates" };

/** @typedef {import("@/mocks/notifications.js").Template} Template */

export default async function Page() {
  /** @type {{ items: Template[] }} */
  const { items } = await apiGet("/v1/admin/notification-templates");
  return <TemplatesView templates={items} />;
}

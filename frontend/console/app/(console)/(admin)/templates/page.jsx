import { apiGet } from "@/lib/api.js";
import { entriesOf } from "@/lib/notifications.js";
import { TemplatesView } from "./TemplatesView.jsx";

export const metadata = { title: "Templates" };

export default async function Page() {
  // The active versions, and every kind the editor can fill (M11 §3.2).
  const listing = await apiGet("/v1/admin/notification-templates");
  return <TemplatesView entries={entriesOf(listing)} />;
}

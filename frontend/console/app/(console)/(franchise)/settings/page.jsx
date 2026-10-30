import { loadHub } from "./data.js";
import { SettingsView } from "./SettingsView.jsx";

export const metadata = { title: "Settings" };

export default async function Page() {
  const hub = await loadHub();
  // A new version remounts the form, so it starts from what the server now holds.
  return <SettingsView key={hub.version} hub={hub} />;
}

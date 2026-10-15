import { loadSettings } from "./data.js";
import { SettingsView } from "./SettingsView.jsx";

export const metadata = { title: "Settings" };

export default async function Page() {
  return <SettingsView settings={await loadSettings()} />;
}

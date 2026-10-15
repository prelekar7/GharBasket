import { loadStaff } from "./data.js";
import { StaffView } from "./StaffView.jsx";

export const metadata = { title: "Staff" };

export default async function Page() {
  return <StaffView people={await loadStaff()} />;
}

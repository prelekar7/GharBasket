import { useState } from "react";
import {
  ConsentCard,
  GullakMeter,
  OrderTimeline,
  RoleSwitcher,
  ScanButton,
  SocietyUnlockBar,
  WalletCard,
} from "./Community.jsx";

/** @type {import("@storybook/react-vite").Meta} */
const meta = { title: "Wallet, society and orders" };
export default meta;

export const Wallet = {
  render: () => (
    <div className="flex flex-col gap-3">
      <WalletCard balancePaise={131000} weekTotalPaise={69000} autopay />
      <WalletCard
        balancePaise={40000}
        weekTotalPaise={69000}
        labels={{ name: "Wallet, low balance" }}
      />
      <GullakMeter points={340} goal={500} label="one meal for a child" />
    </div>
  ),
};

export const Society = {
  render: () => (
    <div className="flex flex-col gap-3">
      <SocietyUnlockBar society="Green Valley CHS" joined={14} target={20} />
      <SocietyUnlockBar society="Sunrise Towers" joined={20} target={20} captain="Meena" />
    </div>
  ),
};

export const Order = {
  render: () => (
    <OrderTimeline
      steps={[
        { label: "Box packed", time: "6:40 AM", state: "done" },
        { label: "With your captain", time: "8:10 AM", note: "Meena, B-402", state: "current" },
        { label: "Delivered", state: "todo" },
      ]}
    />
  ),
};

function ConsentDemo() {
  const [health, setHealth] = useState(false);
  return (
    <ConsentCard
      title="Use health tags to plan meals"
      why="So diabetic-friendly and low-salt meals are planned for the right people."
      uses={["Only for planning your family's meals", "Never shared with kitchens or riders"]}
      version="v1 · 1 Oct 2026"
      switchLabel="Allow"
      granted={health}
      onChange={setHealth}
      footnote="You can withdraw this any time in Profile."
    />
  );
}
export const Consent = { render: () => <ConsentDemo /> };

function FieldDemo() {
  const [role, setRole] = useState(/** @type {"customer" | "captain"} */ ("captain"));
  const [scan, setScan] = useState(/** @type {"idle" | "success" | "error"} */ ("idle"));
  return (
    <div className="flex flex-col gap-3">
      <RoleSwitcher
        roles={["customer", "captain"]}
        active={role}
        onChange={(r) => setRole(/** @type {any} */ (r))}
        roleNames={{
          customer: "Customer",
          captain: "Society Captain",
          kitchen: "Kitchen partner",
          rider: "Rider",
          packer: "Packer",
        }}
      />
      <ScanButton
        state={scan}
        label="Scan bag QR"
        onPress={() =>
          setScan((s) => (s === "idle" ? "success" : s === "success" ? "error" : "idle"))
        }
      />
    </div>
  );
}
export const FieldRoles = { render: () => <FieldDemo /> };

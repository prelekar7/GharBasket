import {
  BasketIcon,
  CalendarBlankIcon,
  HeartIcon,
  ReceiptIcon,
  UserIcon,
} from "@phosphor-icons/react";
import { useState } from "react";
import { Button } from "./Button.jsx";
import { ProgressBar, TabBar } from "./Navigation.jsx";
import { Dialog, Sheet, Toaster, toast } from "./Overlays.jsx";
import { EmptyState, ErrorState, OfflineBanner, Skeleton } from "./States.jsx";

/** @type {import("@storybook/react-vite").Meta} */
const meta = { title: "Overlays, navigation and states" };
export default meta;

export const BottomSheet = {
  render: () => (
    <Sheet
      open
      title="Tamatar · 1 kg"
      description="Tomato is up 40% this week."
      footer={[
        <Button key="r" variant="quiet">
          Remove
        </Button>,
        <Button key="s" variant="primary">
          Save
        </Button>,
      ]}
    >
      <p>Autopilot used lauki in two meals instead.</p>
    </Sheet>
  ),
};

export const MoneyDialog = {
  render: () => (
    <Dialog isOpen title="Top up ₹1,000?" confirmLabel="Pay ₹1,000 with UPI" cancelLabel="Not now">
      Your wallet goes from ₹1,310 to ₹2,310.
    </Dialog>
  ),
};

export const UndoToast = {
  render: () => (
    <>
      <Toaster />
      <Button
        onPress={() =>
          toast("Methi removed from your box", { action: { label: "Undo", onClick: () => {} } })
        }
      >
        Remove methi
      </Button>
    </>
  ),
};

const TABS = [
  { value: "week", label: "Week", icon: <CalendarBlankIcon size={24} /> },
  { value: "box", label: "Box", icon: <BasketIcon size={24} /> },
  { value: "orders", label: "Orders", icon: <ReceiptIcon size={24} /> },
  { value: "kindness", label: "Kindness", icon: <HeartIcon size={24} /> },
  { value: "profile", label: "Profile", icon: <UserIcon size={24} /> },
];

export const Tabs = {
  render: function Render() {
    const [value, setValue] = useState("week");
    return (
      <div className="flex flex-col gap-6">
        <TabBar items={TABS} value={value} onChange={setValue} />
        <TabBar items={TABS} value="profile" onChange={() => {}} senior />
      </div>
    );
  },
};

export const ProgressAndStates = {
  render: () => (
    <div className="flex max-w-[380px] flex-col gap-5">
      <ProgressBar label="Household" value={3} max={5} valueLabel="Step 3 of 5" />
      <ProgressBar
        label="Budget Lock · October"
        value={690}
        max={3000}
        valueLabel="₹690 of ₹3,000"
        tone="accent"
      />
      <Skeleton lines={3} />
      <OfflineBanner message="Offline. 3 actions waiting to sync" retryLabel="Retry" />
      <EmptyState
        title="No tiffins booked this week"
        body="Book by Monday 9 PM."
        action="Book a tiffin"
      />
      <ErrorState
        title="We couldn’t load your box"
        body="Your changes are saved on this phone."
        retryLabel="Try again"
        support="Help: 022 4000 1234"
      />
    </div>
  ),
};

import { ArrowLeftIcon, MagnifyingGlassIcon } from "@phosphor-icons/react";
import { Checkbox, Switch } from "./Checkbox.jsx";
import { Badge, IconButton, StatusPill } from "./Status.jsx";
import { TextField } from "./TextField.jsx";

/** @type {import("@storybook/react-vite").Meta} */
const meta = { title: "Inputs and status" };
export default meta;

export const TextFields = {
  render: () => (
    <div className="flex flex-col gap-4">
      <TextField
        label="Phone number"
        prefix="+91"
        type="tel"
        inputMode="tel"
        placeholder="98200 12345"
        description="We send a 6-digit code by SMS."
      />
      <TextField label="फ़्लैट नंबर" placeholder="जैसे B-402" />
      <TextField
        label="Top-up amount"
        prefix="₹"
        inputMode="numeric"
        defaultValue="50"
        isInvalid
        errorMessage="Top up at least ₹100"
      />
    </div>
  ),
};

export const Toggles = {
  render: () => (
    <div className="flex flex-col">
      <Checkbox label="Jain" description="No onion, garlic or root vegetables" defaultSelected />
      <Checkbox label="Shravan Mondays fasting" />
      <Switch
        label="Senior mode"
        description="Bigger text and buttons everywhere"
        defaultSelected
      />
      <Switch label="Auto-approve my box" />
    </div>
  ),
};

export const StatusAndBadges = {
  render: () => (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        <StatusPill status="planned">Planned</StatusPill>
        <StatusPill status="preparing">Ready to check</StatusPill>
        <StatusPill status="out">On the way</StatusPill>
        <StatusPill status="delivered">Delivered</StatusPill>
        <StatusPill status="issue">Needs attention</StatusPill>
      </div>
      <div className="flex flex-wrap gap-2">
        <Badge tone="accent">Recommended</Badge>
        <Badge>Jain</Badge>
        <Badge tone="outline">Diabetic-friendly</Badge>
        <Badge tone="primary">14</Badge>
        <Badge tone="danger">3</Badge>
      </div>
      <div className="flex gap-2">
        <IconButton label="Back" variant="quiet">
          <ArrowLeftIcon size={24} />
        </IconButton>
        <IconButton label="Search society">
          <MagnifyingGlassIcon size={24} />
        </IconButton>
      </div>
    </div>
  ),
};

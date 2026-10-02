import { Button } from "./Button.jsx";

/** @type {import("@storybook/react-vite").Meta<typeof Button>} */
const meta = {
  title: "Actions/Button",
  component: Button,
  args: { children: "Approve this week", variant: "primary" },
  argTypes: {
    variant: { control: "inline-radio", options: ["primary", "secondary", "quiet", "danger"] },
    size: { control: "inline-radio", options: ["md", "lg"] },
  },
};
export default meta;

/** @typedef {import("@storybook/react-vite").StoryObj<typeof Button>} Story */

/** @type {Story} */
export const Primary = {};

/** @type {Story} */
export const Variants = {
  render: () => (
    <div className="flex flex-wrap items-center gap-2">
      <Button variant="primary">Approve this week</Button>
      <Button>Edit box</Button>
      <Button variant="quiet">Skip this week</Button>
      <Button variant="danger">Cancel Autopay</Button>
      <Button isDisabled>Top up first</Button>
    </div>
  ),
};

/** @type {Story} */
export const BottomAction = {
  args: { size: "lg", block: true },
  parameters: { layout: "padded" },
};

/** Hindi copy uses the same component; Mukta draws Devanagari natively. */
/** @type {Story} */
export const Hindi = {
  args: { children: "यह हफ़्ता मंज़ूर करें", size: "lg" },
};

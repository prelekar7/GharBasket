import * as Icons from "./Icons.jsx";
import { EmptyBasket, GullakCoin, TiffinSteam } from "./Illustrations.jsx";

/** @type {import("@storybook/react-vite").Meta} */
const meta = { title: "Icons and illustrations" };
export default meta;

/** The 15 custom icons at 24 and 32 px; they take the text colour. */
export const CustomIcons = {
  render: () => (
    <ul className="grid grid-cols-3 gap-4 text-fg">
      {Object.entries(Icons).map(([name, Icon]) => (
        <li key={name} className="flex items-center gap-2 text-sm">
          <Icon />
          <Icon size={32} />
          <span>{name.replace(/Icon$/, "")}</span>
        </li>
      ))}
    </ul>
  ),
};

/** Empty-state illustrations. */
export const Illustrations = {
  render: () => (
    <div className="flex flex-wrap gap-6 text-fg">
      <EmptyBasket label="Empty basket" />
      <TiffinSteam label="Tiffin with steam" />
      <GullakCoin label="Gullak with a coin" />
    </div>
  ),
};

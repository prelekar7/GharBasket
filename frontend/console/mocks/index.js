// Every mock handler the console knows. A new domain adds one line here (see README.md).
import { catalogHandlers } from "./catalog.js";
import { communityHandlers } from "./community.js";
import { hubsHandlers } from "./hubs.js";
import { intelHandlers } from "./intel.js";
import { publicHandlers } from "./public.js";

export const handlers = [
  ...hubsHandlers,
  ...catalogHandlers,
  ...communityHandlers,
  ...intelHandlers,
  ...publicHandlers,
];

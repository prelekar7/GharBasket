// Every mock handler the console knows. A new domain adds one line here (see README.md).
import { catalogHandlers } from "./catalog.js";
import { communityHandlers } from "./community.js";
import { festivalsHandlers } from "./festivals.js";
import { franchiseHandlers } from "./franchise.js";
import { hubsHandlers } from "./hubs.js";
import { impactHandlers } from "./impact.js";
import { intelHandlers } from "./intel.js";
import { plansHandlers } from "./plans.js";
import { pricesHandlers } from "./prices.js";
import { publicHandlers } from "./public.js";
import { teamHandlers } from "./team.js";

export const handlers = [
  ...hubsHandlers,
  ...catalogHandlers,
  ...communityHandlers,
  ...festivalsHandlers,
  ...franchiseHandlers,
  ...impactHandlers,
  ...intelHandlers,
  ...plansHandlers,
  ...pricesHandlers,
  ...publicHandlers,
  ...teamHandlers,
];

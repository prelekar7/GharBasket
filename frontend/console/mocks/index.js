// Every mock handler the console knows. A new domain adds one line here (see README.md).
import { adminHandlers } from "./admin.js";
import { analyticsHandlers } from "./analytics.js";
import { billingHandlers } from "./billing.js";
import { catalogHandlers } from "./catalog.js";
import { communityHandlers } from "./community.js";
import { cyclesHandlers } from "./cycles.js";
import { festivalsHandlers } from "./festivals.js";
import { franchiseHandlers } from "./franchise.js";
import { hubsHandlers } from "./hubs.js";
import { impactHandlers } from "./impact.js";
import { intelHandlers } from "./intel.js";
import { logisticsHandlers } from "./logistics.js";
import { notificationsHandlers } from "./notifications.js";
import { opsBoardHandlers } from "./opsboard.js";
import { ordersHandlers } from "./orders.js";
import { packingHandlers } from "./packing.js";
import { partnersHandlers } from "./partners.js";
import { payoutsHandlers } from "./payouts.js";
import { plansHandlers } from "./plans.js";
import { pricesHandlers } from "./prices.js";
import { procurementHandlers } from "./procurement.js";
import { publicHandlers } from "./public.js";
import { receivingHandlers } from "./receiving.js";
import { settingsHandlers } from "./settings.js";
import { teamHandlers } from "./team.js";
import { vendorsHandlers } from "./vendors.js";
import { wasteHandlers } from "./waste.js";

export const handlers = [
  ...hubsHandlers,
  ...catalogHandlers,
  ...adminHandlers,
  ...analyticsHandlers,
  ...billingHandlers,
  ...communityHandlers,
  ...cyclesHandlers,
  ...festivalsHandlers,
  ...franchiseHandlers,
  ...impactHandlers,
  ...intelHandlers,
  ...logisticsHandlers,
  ...notificationsHandlers,
  ...ordersHandlers,
  ...partnersHandlers,
  ...payoutsHandlers,
  ...plansHandlers,
  ...pricesHandlers,
  ...publicHandlers,
  ...settingsHandlers,
  ...teamHandlers,
  ...opsBoardHandlers,
  ...packingHandlers,
  ...procurementHandlers,
  ...receivingHandlers,
  ...vendorsHandlers,
  ...wasteHandlers,
];

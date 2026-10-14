// Every mock handler the console knows. A new domain adds one line here (see README.md).
import { catalogHandlers } from "./catalog.js";
import { hubsHandlers } from "./hubs.js";

export const handlers = [...hubsHandlers, ...catalogHandlers];

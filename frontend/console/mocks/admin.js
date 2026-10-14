// The admin pages' mocks (societies and requests, users and roles, tiffin menus, hubs) as one list,
// so mocks/index.js needs a single line for them.
import { communityHandlers } from "./adminCommunity.js";
import { hubadminHandlers } from "./hubadmin.js";
import { identityHandlers } from "./identity.js";
import { tiffinHandlers } from "./tiffin.js";

export const adminHandlers = [
  ...communityHandlers,
  ...identityHandlers,
  ...tiffinHandlers,
  ...hubadminHandlers,
];

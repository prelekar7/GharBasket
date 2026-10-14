// The admin pages' mocks as one list, so mocks/index.js needs a single line for them.
import { communityHandlers } from "./adminCommunity.js";
import { identityHandlers } from "./identity.js";
import { tiffinHandlers } from "./tiffin.js";

export const adminHandlers = [...communityHandlers, ...identityHandlers, ...tiffinHandlers];

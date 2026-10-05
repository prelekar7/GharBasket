// The admin pages' mocks as one list, so mocks/index.js needs a single line for them.
import { communityHandlers } from "./adminCommunity.js";

export const adminHandlers = [...communityHandlers];

// The seed partners' ids, shared by the partner and payout mocks (one partner, one id everywhere).
import { mockUuid } from "./util.js";

export const PARTNER_ID = {
  lata: mockUuid("b300", 1),
  arjun: mockUuid("b300", 2),
  meena: mockUuid("b300", 3),
  kavita: mockUuid("b300", 4),
};

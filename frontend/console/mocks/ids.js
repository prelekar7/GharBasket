// Ids the mocks share, so one record has one id everywhere. They are uuids like the real API's; the seed
// societies are the backend seed's (tools/seed.py), the slots are made up (the backend seed has one slot).
import { mockUuid } from "./util.js";

/** The backend seed's societies, by slug. */
export const SOCIETY_ID = {
  "sunrise-towers": "0199a000-0000-7000-8000-00000000b001",
  "green-valley-heights": "0199a000-0000-7000-8000-00000000b002",
  "shree-ganesh-chs": "0199a000-0000-7000-8000-00000000b003",
  "panchavati-residency": "0199a000-0000-7000-8000-00000000b004",
  "krishna-kunj": "0199a000-0000-7000-8000-00000000b005",
  "sai-darshan-apartments": "0199a000-0000-7000-8000-00000000b006",
};

/** The Kharghar hub's delivery slots. */
export const SLOT_ID = {
  early: mockUuid("b200", 1),
  am: mockUuid("b200", 2),
  pm: mockUuid("b200", 3),
};

/** The partner NGOs of the pilot hub (M9 section 3.5); the first one's coordinator is the mock's NGO sign-in. */
export const NGO_ID = {
  first: mockUuid("b800", 1),
  second: mockUuid("b800", 2),
};

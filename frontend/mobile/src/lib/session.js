import { create } from "zustand";
import { persist } from "zustand/middleware";

/** @typedef {import("./api.js").FieldRole} FieldRole */

/**
 * @typedef {object} Session
 * @property {string | null} accessToken Kept in memory only (15 min).
 * @property {FieldRole[]} roles Verified field roles from GET /v1/me.
 * @property {FieldRole | null} activeRole Last choice is remembered (handbook ch. 03 §2); sent as X-Active-Role.
 * @property {(token: string, roles: FieldRole[]) => void} signIn
 * @property {(role: FieldRole) => void} setActiveRole
 * @property {() => void} signOut
 */

const FIELD_ROLES = ["customer", "captain", "kitchen", "rider", "packer"];

/** Session store. The refresh token moves to Capacitor Preferences in the auth PR; never localStorage. */
export const useSession = create(
  /** @type {import("zustand").StateCreator<Session, [], [["zustand/persist", unknown]]>} */ (
    persist(
      (set, get) => ({
        accessToken: null,
        roles: [],
        activeRole: null,
        signIn: (accessToken, roles) => {
          const verified = roles.filter((r) => FIELD_ROLES.includes(r));
          const remembered = get().activeRole;
          set({
            accessToken,
            roles: verified,
            activeRole:
              remembered && verified.includes(remembered)
                ? remembered
                : verified.length === 1
                  ? verified[0]
                  : null,
          });
        },
        setActiveRole: (activeRole) => set({ activeRole }),
        signOut: () => set({ accessToken: null, roles: [] }),
      }),
      { name: "gb-session", partialize: (s) => ({ activeRole: s.activeRole }) },
    )
  ),
);

/** First screen for each role (handbook ch. 09 §9.7). */
export const HOME_BY_ROLE = /** @type {const} */ ({
  customer: "/week",
  captain: "/captain",
  kitchen: "/kitchen",
  rider: "/rider",
  packer: "/packer",
});

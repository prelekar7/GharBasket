import { create } from "zustand";
import { persist } from "zustand/middleware";
import { logout, logoutAll, me, setActiveRoleHeader } from "@/lib/api.js";

/** @typedef {import("./api.js").FieldRole} FieldRole */
/** @typedef {import("./api.js").RoleGrant} RoleGrant */
/** @typedef {import("./api.js").UserOut} UserOut */

/**
 * @typedef {object} Session
 * @property {boolean} signedIn True once GET /v1/me has succeeded in this app run.
 * @property {UserOut | null} user
 * @property {RoleGrant[]} grants Verified field-role grants from GET /v1/me.
 * @property {FieldRole[]} roles The same, as role names.
 * @property {FieldRole | null} activeRole Last choice is remembered (handbook ch. 03 §2); sent as X-Active-Role.
 * @property {(profile: UserOut & { roles: RoleGrant[] }) => void} signIn
 * @property {(role: FieldRole) => void} setActiveRole
 * @property {() => Promise<boolean>} restore Restores the session from the API (cookie on web, stored refresh token on native).
 * @property {(options?: { everywhere?: boolean }) => Promise<void>} signOut
 * @property {() => void} clear Forget the session locally (after it expired).
 */

const FIELD_ROLES = ["customer", "captain", "kitchen", "rider", "packer"];

/** @param {RoleGrant[]} grants @param {FieldRole | null} role */
function headerFor(grants, role) {
  const grant = grants.find((g) => g.role === role);
  if (!grant) return null;
  return grant.scope_id ? `${grant.role}:${grant.scope_id}` : grant.role;
}

/** @type {Promise<boolean> | null} */
let restoring = null;

export const useSession = create(
  /** @type {import("zustand").StateCreator<Session, [], [["zustand/persist", unknown]]>} */ (
    persist(
      (set, get) => ({
        signedIn: false,
        user: null,
        grants: [],
        roles: [],
        activeRole: null,
        signIn: ({ roles: all, ...user }) => {
          const grants = all.filter((g) => g.status === "verified" && FIELD_ROLES.includes(g.role));
          const roles = /** @type {FieldRole[]} */ (grants.map((g) => g.role));
          const remembered = get().activeRole;
          const activeRole =
            remembered && roles.includes(remembered)
              ? remembered
              : roles.length === 1
                ? roles[0]
                : null;
          setActiveRoleHeader(headerFor(grants, activeRole));
          set({ signedIn: true, user, grants, roles, activeRole });
        },
        setActiveRole: (activeRole) => {
          setActiveRoleHeader(headerFor(get().grants, activeRole));
          set({ activeRole });
        },
        restore: () => {
          if (get().signedIn) return Promise.resolve(true);
          restoring ??= me()
            .then((profile) => {
              get().signIn(profile);
              return true;
            })
            .catch(() => false)
            .finally(() => {
              restoring = null;
            });
          return restoring;
        },
        signOut: async ({ everywhere = false } = {}) => {
          await (everywhere ? logoutAll() : logout()).catch(() => {});
          get().clear();
        },
        clear: () => {
          setActiveRoleHeader(null);
          set({ signedIn: false, user: null, grants: [], roles: [] });
        },
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

import { create } from "zustand";
import { persist } from "zustand/middleware";
import {
  releasePushToken,
  stopPushToken,
  syncPushToken,
} from "@/features/notifications/pushToken.js";
import { logout, logoutAll, me, refresh, setActiveRoleHeader, setSigningOut } from "@/lib/api.js";
import { NATIVE } from "@/lib/env.js";
import { clearLocalData } from "@/lib/localData.js";
import { queryClient } from "@/lib/queryClient.js";
import { tokens } from "@/lib/tokens.js";

/** @typedef {import("./api.js").FieldRole} FieldRole */
/** @typedef {import("./api.js").RoleGrant} RoleGrant */
/** @typedef {import("./api.js").UserOut} UserOut */

/**
 * @typedef {object} Session
 * @property {boolean} signedIn True once GET /v1/me has succeeded in this app run.
 * @property {UserOut | null} user
 * @property {RoleGrant[]} grants Verified field-role grants from GET /v1/me (applied, kyc_pending and suspended ones are not selectable, M1 US-5).
 * @property {FieldRole[]} roles The same, as role names.
 * @property {FieldRole | null} activeRole Last choice is remembered (handbook ch. 03 §2); sent as X-Active-Role.
 * @property {(profile: UserOut & { roles: RoleGrant[] }) => void} signIn
 * @property {(role: FieldRole) => void} setActiveRole
 * @property {(user: UserOut) => void} setUser After PATCH /v1/me (name, language, senior mode).
 * @property {() => Promise<boolean>} restore Restores the session from the API (cookie on web, stored refresh token on native).
 * @property {(options?: { everywhere?: boolean }) => Promise<void>} signOut
 * @property {() => void} clear Forget the session locally (after it expired).
 */

const FIELD_ROLES = ["customer", "captain", "kitchen_partner", "rider", "packer"];

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
          syncPushToken();
        },
        setUser: (user) => set({ user }),
        setActiveRole: (activeRole) => {
          setActiveRoleHeader(headerFor(get().grants, activeRole));
          set({ activeRole });
        },
        restore: () => {
          if (get().signedIn) return Promise.resolve(true);
          restoring ??= (async () => {
            if (NATIVE && !tokens.access) {
              // Cold start of the app: no refresh token means nobody ever signed in here, so
              // answer at once instead of waiting for the API to say 401. With one, refresh first
              // rather than send GET /v1/me to a certain 401.
              if (!(await tokens.refreshToken()) || !(await refresh())) return false;
            }
            get().signIn(await me());
            return true;
          })()
            .catch(() => false)
            .finally(() => {
              restoring = null;
            });
          return restoring;
        },
        signOut: async ({ everywhere = false } = {}) => {
          // Screens may still be refetching: stop them first, so no request goes out (or reports
          // an expired session) while the old session is being ended, and none after it either
          // (the next sign-in's exchange turns this off, see setSigningOut).
          setSigningOut(true);
          try {
            await queryClient.cancelQueries();
            await releasePushToken();
            await (everywhere ? logoutAll() : logout()).catch(() => {});
            await clearLocalData();
            get().clear();
          } catch (e) {
            setSigningOut(false); // still signed in: the app must keep working
            throw e;
          }
        },
        clear: () => {
          stopPushToken();
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
  kitchen_partner: "/kitchen",
  rider: "/rider",
  packer: "/packer",
});

/**
 * A role's key in @gharbasket/ui (role colours, TabBar) and in its URL: the backend's
 * `kitchen_partner` is "kitchen" there.
 * @param {FieldRole} role
 */
export const uiRole = (role) => (role === "kitchen_partner" ? "kitchen" : role);

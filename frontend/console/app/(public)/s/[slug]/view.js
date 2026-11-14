/**
 * What the society page can say about GET /v1/public/societies/{slug} (PublicSocietyOut). Two fields
 * may be missing on the real API:
 * - `homes_joined` is null under 5 households (privacy, M9 §3.7), so there is no count and no
 *   "n homes away" until then;
 * - `area` is null when the society has none on record.
 * A live society counts as unlocked whatever its count: staff can unlock early or change the target.
 * @param {import("../../../../mocks/public.js").PublicSociety} society
 */
export function societyView(society) {
  const live = society.status === "live";
  const joined = society.homes_joined ?? null;
  const away = joined === null ? null : Math.max(society.unlock_target - joined, 0);
  /** @type {"liveTitle" | "unlockingTitleEarly" | "unlockingTitleOne" | "unlockingTitle"} */
  const titleKey = live
    ? "liveTitle"
    : away === null
      ? "unlockingTitleEarly"
      : away === 1
        ? "unlockingTitleOne"
        : "unlockingTitle";
  return {
    live,
    joined,
    away,
    titleKey,
    /** "Sunrise Towers, Sector 20, Kharghar", or just the name */
    place: society.area ? `${society.name}, ${society.area}` : society.name,
    /** "Sunrise Towers (Sector 20, Kharghar)", or just the name */
    named: society.area ? `${society.name} (${society.area})` : society.name,
  };
}

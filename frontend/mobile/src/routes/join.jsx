import { createFileRoute, redirect } from "@tanstack/react-router";
import { joinSearch } from "@/features/community/join.js";
import { useOnboardingDraft } from "@/features/onboarding/draft.js";

/**
 * A neighbour's invite: `/join?s=<society slug>` from the society page (M12 §3.7). Signed in or not,
 * the slug goes into the onboarding draft and the usual way in takes over (welcome or sign-in, role,
 * then the first onboarding step for someone with no household), where the society step finds it.
 * Someone who already has a household just lands on their home.
 */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/join")({
  validateSearch: joinSearch,
  beforeLoad: (/** @type {{ search: { s?: string } }} */ { search }) => {
    if (search.s) useOnboardingDraft.getState().update({ inviteSlug: search.s });
    throw redirect({ to: "/" });
  },
});

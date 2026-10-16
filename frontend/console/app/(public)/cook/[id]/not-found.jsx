import Link from "next/link";
import { linkButton, PublicFooter, PublicHeader } from "@/components/public/Chrome.jsx";

export const metadata = {
  title: { absolute: "Profile not available · GharBasket" },
  robots: { index: false },
};

/** Unknown cook, or a cook who has not agreed to a public profile: the page cannot tell which. */
export default function CookNotFound() {
  return (
    <>
      <PublicHeader />
      <main
        id="content"
        className="mx-auto flex w-full max-w-[480px] flex-1 flex-col items-start gap-3 px-4 py-8"
      >
        <h1 className="font-display text-[30px] leading-9 font-semibold">
          This profile is not available
        </h1>
        <p className="text-fg-secondary">
          A cook’s profile is shown only when she agrees to it. Try the link again, or ask your
          Society Captain.
        </p>
        <Link href="/" className={`${linkButton.base} ${linkButton.secondary}`}>
          Back to GharBasket
        </Link>
      </main>
      <PublicFooter />
    </>
  );
}

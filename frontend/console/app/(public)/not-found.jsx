import Link from "next/link";
import { linkButton, PublicFooter, PublicHeader } from "@/components/public/Chrome.jsx";

export const metadata = {
  title: { absolute: "Page not found · GharBasket" },
  robots: { index: false },
};

export default function PublicNotFound() {
  return (
    <>
      <PublicHeader />
      <main
        id="content"
        className="mx-auto flex w-full max-w-[480px] flex-1 flex-col items-start gap-3 px-4 py-8"
      >
        <h1 className="font-display text-[30px] leading-9 font-semibold">
          We couldn’t find that page
        </h1>
        <p className="text-fg-secondary">
          The link may be mistyped or out of date. You can look for your society on the home page.
        </p>
        <Link href="/#societies" className={`${linkButton.base} ${linkButton.primary}`}>
          Find my society
        </Link>
      </main>
      <PublicFooter />
    </>
  );
}

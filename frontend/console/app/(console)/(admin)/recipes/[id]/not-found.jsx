import Link from "next/link";

export default function NotFound() {
  return (
    <section className="mx-auto flex max-w-[360px] flex-col items-center gap-2 py-10 text-center">
      <h1 className="font-display text-[22px] leading-7 font-semibold">
        We cannot find this recipe
      </h1>
      <p className="text-sm leading-5 text-fg-secondary">
        It may have been removed. Go back to the recipes and search for it again.
      </p>
      <Link href="/recipes" className="mt-2 rounded-xs font-semibold text-link underline">
        Back to the recipes
      </Link>
    </section>
  );
}

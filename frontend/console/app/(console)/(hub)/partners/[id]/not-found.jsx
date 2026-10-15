import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex flex-col items-start gap-2">
      <h1 className="font-display text-[28px] leading-[34px] font-semibold">
        We could not find this partner
      </h1>
      <p className="text-fg-secondary">
        The link may be old, or the partner belongs to another hub.
      </p>
      <Link href="/partners" className="font-semibold text-link underline">
        Back to Partners and KYC
      </Link>
    </div>
  );
}

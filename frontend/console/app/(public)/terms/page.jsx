import { LegalDocument } from "@/components/public/LegalDocument.jsx";

export const metadata = {
  title: { absolute: "Terms of Service · GharBasket" },
  description:
    "The terms for using GharBasket: the weekly box, the wallet, tiffins, deliveries and Kindness.",
  alternates: { canonical: "/terms" },
  robots: { index: true, follow: true },
};

/** Rendered from docs/legal/terms.md (copied to content/legal/) at build time. */
export default function TermsPage() {
  return <LegalDocument doc="terms" />;
}

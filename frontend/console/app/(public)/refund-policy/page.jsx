import { LegalDocument } from "@/components/public/LegalDocument.jsx";

export const metadata = {
  title: { absolute: "Refund, Cancellation and Delivery Policy · GharBasket" },
  description:
    "How GharBasket payments, cancellations, deliveries and refunds work, and where refunds go.",
  alternates: { canonical: "/refund-policy" },
  robots: { index: true, follow: true },
};

/** Rendered from docs/legal/refunds.md (copied to content/legal/) at build time. */
export default function RefundPolicyPage() {
  return <LegalDocument doc="refunds" />;
}

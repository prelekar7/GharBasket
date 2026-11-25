import { LegalDocument } from "@/components/public/LegalDocument.jsx";

export const metadata = {
  title: { absolute: "Privacy Notice · GharBasket" },
  description:
    "What GharBasket collects, why, who sees it, and your rights under India's Digital Personal Data Protection Act, 2023.",
  alternates: { canonical: "/privacy" },
  robots: { index: true, follow: true },
};

/** Rendered from docs/legal/privacy.md (copied to content/legal/) at build time. */
export default function PrivacyPage() {
  return <LegalDocument doc="privacy" />;
}

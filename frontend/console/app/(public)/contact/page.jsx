import { LegalDocument } from "@/components/public/LegalDocument.jsx";

export const metadata = {
  title: { absolute: "Contact Us · GharBasket" },
  description: "How to reach GharBasket's support and grievance officer, and the business details.",
  alternates: { canonical: "/contact" },
  robots: { index: true, follow: true },
};

/** Rendered from docs/legal/contact.md (copied to content/legal/) at build time. */
export default function ContactPage() {
  return <LegalDocument doc="contact" />;
}

import { SITE_URL } from "@/lib/public-lang.js";

/** Crawlers may read the landing page, the legal pages and society pages; the console and cook profiles stay out. */
export default function robots() {
  return {
    rules: [
      {
        userAgent: "*",
        allow: ["/$", "/privacy", "/terms", "/refund-policy", "/contact", "/s/"],
        disallow: "/",
      },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
  };
}

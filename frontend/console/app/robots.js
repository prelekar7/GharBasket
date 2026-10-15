import { SITE_URL } from "@/lib/public-lang.js";

/** Crawlers may read the landing page, the privacy notice and society pages; the console and cook profiles stay out. */
export default function robots() {
  return {
    rules: [
      {
        userAgent: "*",
        allow: ["/$", "/privacy", "/s/"],
        disallow: "/",
      },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
  };
}

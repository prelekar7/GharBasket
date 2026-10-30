import { SITE_URL } from "@/lib/public-lang.js";

/** The landing page and the privacy notice. Society pages are reached from invite links: v1 has no public list of them to list here. */
export default function sitemap() {
  return [
    { url: `${SITE_URL}/`, changeFrequency: "monthly", priority: 1 },
    { url: `${SITE_URL}/privacy`, changeFrequency: "yearly", priority: 0.3 },
  ];
}

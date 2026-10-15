import { getSocieties } from "@/lib/public-api.js";
import { SITE_URL } from "@/lib/public-lang.js";

export default async function sitemap() {
  const societies = await getSocieties("en");
  return [
    { url: `${SITE_URL}/`, changeFrequency: "monthly", priority: 1 },
    { url: `${SITE_URL}/privacy`, changeFrequency: "yearly", priority: 0.3 },
    ...societies.map((s) => ({
      url: `${SITE_URL}/s/${s.slug}`,
      changeFrequency: "weekly",
      priority: 0.8,
    })),
  ];
}

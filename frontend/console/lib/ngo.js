import { apiGet } from "./api.js";

/**
 * The NGO an NGO coordinator works for: the one `GET /v1/ngos` lists for them (M9 section 3.5 answers a
 * coordinator with their own NGO only). Null when the list is empty.
 * @returns {Promise<string | null>}
 */
export async function currentNgoId() {
  const { items } = await apiGet("/v1/ngos");
  return items[0]?.id ?? null;
}

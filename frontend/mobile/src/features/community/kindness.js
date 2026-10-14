import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { request } from "@/lib/api.js";
import { uploadFile } from "@/lib/upload.js";

/**
 * @typedef {{ code: string, qty?: number, kg?: number }} DonationItem
 * @typedef {{ id: string, items: DonationItem[], photos: string[], status: string, pickup_on: string, points_awarded: number | null }} Donation
 * @typedef {{ id: string, code: string, starts_on: string, ends_on: string, multiplier: number }} Drive
 */

export const DONATION_ITEMS = ["clothes", "books", "toys", "school_bags", "utensils"];

/** Running drives, e.g. Diwali at 2× points (GET /v1/drives, M9). */
export function useDrives() {
  return useQuery({
    queryKey: ["drives"],
    queryFn: async () => /** @type {{ items: Drive[] }} */ (await request("/v1/drives")).items,
  });
}

/** Your donations and the next pickup day (GET /v1/donations, our proposal for M9). */
export function useDonations() {
  return useQuery({
    queryKey: ["donations"],
    queryFn: () =>
      /** @type {Promise<{ items: Donation[], next_cursor: string | null, next_pickup_on: string }>} */ (
        request("/v1/donations")
      ),
  });
}

/**
 * Book a pickup on the van's way back (POST /v1/donations, M9). The key is kept until it
 * succeeds, so a retry after a dropped connection never books twice.
 */
export function useScheduleDonation() {
  const client = useQueryClient();
  const key = useRef(/** @type {string | null} */ (null));
  return useMutation({
    mutationFn: async (/** @type {{ items: string[], photos: File[], pickupOn: string }} */ v) => {
      key.current ??= crypto.randomUUID();
      const photos = await Promise.all(v.photos.map((f) => uploadFile(f)));
      return /** @type {Promise<Donation>} */ (
        request("/v1/donations", {
          method: "POST",
          body: { items: v.items, photos, pickup_on: v.pickupOn },
          headers: { "idempotency-key": key.current },
        })
      );
    },
    onSuccess: () => {
      key.current = null;
      client.invalidateQueries({ queryKey: ["donations"] });
    },
  });
}

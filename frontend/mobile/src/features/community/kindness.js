import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { request } from "@/lib/api.js";
import { uploadFile } from "@/lib/upload.js";

/**
 * M9 §3.4. `qty` is whole pieces for "each" and kilograms (0.5 steps) for "kg"; `counted` and
 * `points` are set by the hub when it verifies. Photos are only counted: customers never get links.
 * @typedef {{ code: string, unit: "kg" | "each", qty: number, counted: number | null, points: number | null }} DonationItem
 * @typedef {"scheduled" | "picked_up" | "verified" | "rejected" | "handed_over" | "cancelled"} DonationStatus
 * @typedef {{ id: string, status: DonationStatus, items: DonationItem[], photos: number,
 *   drive: { id: string, name: string, multiplier: number } | null, pickup_on: string,
 *   estimated_points: number, points_awarded: number | null }} Donation
 * @typedef {{ code: string, label: string, unit: "kg" | "each", points: number, max_qty: number }} PointRate
 * @typedef {{ id: string, code: string, name: string, description: string, codes: string[], multiplier: number,
 *   starts_on: string, ends_on: string, state: "upcoming" | "running" | "ended" | "cancelled" }} Drive
 */

/** What each thing earns and how many can go in one donation (GET /v1/point-rates, M9). */
export function usePointRates() {
  const { i18n } = useTranslation();
  return useQuery({
    queryKey: ["point-rates", i18n.resolvedLanguage],
    queryFn: async () =>
      /** @type {{ items: PointRate[] }} */ (await request("/v1/point-rates")).items,
  });
}

/** Running and upcoming drives, e.g. Diwali at 2× points, named in your language (GET /v1/drives, M9). */
export function useDrives() {
  const { i18n } = useTranslation();
  return useQuery({
    queryKey: ["drives", i18n.resolvedLanguage],
    queryFn: async () => /** @type {{ items: Drive[] }} */ (await request("/v1/drives")).items,
  });
}

/** Your donations and the next two pickup days you can choose (GET /v1/donations, M9). */
export function useDonations() {
  return useQuery({
    queryKey: ["donations"],
    queryFn: () =>
      /** @type {Promise<{ items: Donation[], next_cursor: string | null, pickup_dates: string[] }>} */ (
        request("/v1/donations")
      ),
  });
}

/**
 * Book a pickup on the van's way back (POST /v1/donations, M9): lines of { code, qty }, photo keys
 * from the presigned uploads, the drive that multiplies them, and one of the pickup dates. The key
 * is kept until it succeeds, so a retry after a dropped connection never books twice.
 */
export function useScheduleDonation() {
  const client = useQueryClient();
  const key = useRef(/** @type {string | null} */ (null));
  return useMutation({
    mutationFn: async (
      /** @type {{ items: { code: string, qty: number }[], photos: File[], driveId?: string, pickupOn: string }} */ v,
    ) => {
      key.current ??= crypto.randomUUID();
      const photoKeys = await Promise.all(v.photos.map((f) => uploadFile(f, "donation_photo")));
      return /** @type {Promise<Donation>} */ (
        request("/v1/donations", {
          method: "POST",
          body: {
            items: v.items,
            photo_keys: photoKeys,
            drive_id: v.driveId ?? null,
            pickup_on: v.pickupOn,
          },
          headers: { "idempotency-key": key.current },
        })
      );
    },
    onSuccess: () => {
      key.current = null;
      client.invalidateQueries({ queryKey: ["donations"] });
      client.invalidateQueries({ queryKey: ["drives"] });
    },
  });
}

/**
 * "School bag and 3 kg clothes": donated items in words (Kindness history; Our impact, mid-sentence).
 * @param {import("i18next").TFunction} t @param {string} lang @param {{ code: string, unit: string, qty: number }[]} items
 */
export function describeItems(t, lang, items, capitalise = true) {
  const text = new Intl.ListFormat(lang, { type: "conjunction" }).format(
    items.map(({ code, unit, qty }) =>
      unit === "kg"
        ? t("kindness.kg", { kg: qty, item: t(`kindness.of.${code}`) })
        : qty
          ? t(`kindness.count.${code}`, { count: qty })
          : t(`kindness.of.${code}`),
    ),
  );
  return capitalise ? text.charAt(0).toLocaleUpperCase(lang) + text.slice(1) : text;
}

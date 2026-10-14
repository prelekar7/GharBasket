// Words for the partner screens (docs/modules/M8-partners.md §3). No imports, so server and client code can both use it.

/** @typedef {"kitchen" | "captain" | "rider" | "packer"} PartnerKind */

/** @type {Record<PartnerKind, string>} */
export const KIND_LABEL = {
  kitchen: "Kitchen partner",
  captain: "Society Captain",
  rider: "Rider",
  packer: "Packer",
};

/** The flat as the API stores it: the wing id in capitals, then the number ("B-402"), M1 AddressOut.flat. @param {string} towerId @param {string} number */
export const flatLabel = (towerId, number) => `${towerId.toUpperCase()}-${number.trim()}`;

/** The number the person typed, without the wing prefix. @param {{ tower_id: string, flat: string }} address */
export function flatNumber({ tower_id: towerId, flat }) {
  const prefix = `${towerId.toUpperCase()}-`;
  return flat.startsWith(prefix) ? flat.slice(prefix.length) : flat;
}

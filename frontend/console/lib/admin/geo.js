// Boundaries travel as GeoJSON Polygons (one closed ring of [lng, lat]); the PolygonEditor works on
// an open ring (the first point is not repeated). These convert between the two.

/** @typedef {[number, number]} LngLat */
/** @typedef {{ type: "Polygon", coordinates: number[][][] }} GeoPolygon */

/** Six decimals is about 10 cm. The editor's drawing library refuses longer numbers (it keeps nine). @param {number} n */
const round = (n) => Math.round(n * 1e6) / 1e6;

/**
 * @param {GeoPolygon | null} polygon
 * @returns {LngLat[]} the open ring, or [] for no boundary
 */
export function ringOf(polygon) {
  const ring = polygon?.coordinates?.[0];
  if (!ring || ring.length < 4) return [];
  return ring.slice(0, -1).map(([lng, lat]) => /** @type {LngLat} */ ([round(lng), round(lat)]));
}

/**
 * @param {LngLat[]} ring an open ring of at least 3 corners
 * @returns {GeoPolygon}
 */
export function polygonOf(ring) {
  const closed = [...ring, ring[0]].map(([lng, lat]) => [round(lng), round(lat)]);
  return { type: "Polygon", coordinates: [closed] };
}

import type { GeoFeature, GeoPosition, LngLat } from "./types";

export const METERS_PER_DEG_LAT = 110540;
const METERS_PER_DEG_LNG = 111320;

/** Equirectangular projection around a center, returns meters (x east, y north). */
export function toMeters(p: LngLat, center: LngLat): { x: number; y: number } {
  const cosLat = Math.cos(((center.lat + p.lat) / 2) * (Math.PI / 180));
  return {
    x: (p.lng - center.lng) * METERS_PER_DEG_LNG * cosLat,
    y: (p.lat - center.lat) * METERS_PER_DEG_LAT,
  };
}

export function fromMeters(x: number, y: number, center: LngLat): LngLat {
  const cosLat = Math.cos(center.lat * (Math.PI / 180));
  return {
    lng: center.lng + x / (METERS_PER_DEG_LNG * cosLat),
    lat: center.lat + y / METERS_PER_DEG_LAT,
  };
}

/** Convert a ring of [lng,lat] to an SVG path string in meter space (y flipped). */
export function ringToPath(ring: GeoPosition[], center: LngLat): string {
  return ring
    .map((pt, i) => {
      const { x, y } = toMeters({ lng: pt[0], lat: pt[1] }, center);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${(-y).toFixed(1)}`;
    })
    .join(" ");
}

/** Polygon / MultiPolygon feature -> array of path strings (with holes handled via fill-rule). */
export function featureToPathData(feature: GeoFeature, center: LngLat): string {
  const g = feature.geometry;
  if (!g) return "";
  if (g.type === "Polygon") {
    return (g.coordinates as GeoPosition[][])
      .map((ring) => `${ringToPath(ring, center)} Z`)
      .join(" ");
  }
  if (g.type === "MultiPolygon") {
    return (g.coordinates as GeoPosition[][][])
      .map((poly) => poly.map((ring) => `${ringToPath(ring, center)} Z`).join(" "))
      .join(" ");
  }
  if (g.type === "LineString") {
    return ringToPath(g.coordinates as GeoPosition[], center);
  }
  if (g.type === "MultiLineString") {
    return (g.coordinates as GeoPosition[][])
      .map((line) => ringToPath(line, center))
      .join(" ");
  }
  return "";
}

export function isPolygonLike(feature: GeoFeature): boolean {
  return (
    feature.geometry?.type === "Polygon" ||
    feature.geometry?.type === "MultiPolygon"
  );
}

/** Extract exterior rings (for BMapGL polygon construction). */
export function featureToRings(feature: GeoFeature): GeoPosition[][] {
  const g = feature.geometry;
  if (!g) return [];
  if (g.type === "Polygon") return g.coordinates as GeoPosition[][];
  if (g.type === "MultiPolygon") {
    return (g.coordinates as GeoPosition[][][]).map((poly) => poly[0]);
  }
  if (g.type === "LineString") return [g.coordinates as GeoPosition[]];
  if (g.type === "MultiLineString") return g.coordinates as GeoPosition[][];
  return [];
}

export function distanceMeters(a: LngLat, b: LngLat): number {
  const { x, y } = toMeters(a, b);
  return Math.hypot(x, y);
}

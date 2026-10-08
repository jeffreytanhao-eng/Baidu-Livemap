import type {
  BlindCategory,
  CheckupFastResponse,
  CheckupResponse,
  FabricResponse,
  LngLat,
  NearbyResponse,
  SamplePoint,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) {
        detail =
          typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch {
      // ignore
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export async function postCheckup(
  center: LngLat,
  minutes: number,
  phase: "fast",
  engine?: string,
  blindFilter?: BlindCategory[] | null
): Promise<CheckupFastResponse>;
export async function postCheckup(
  center: LngLat,
  minutes: number,
  phase: "full",
  engine?: string,
  blindFilter?: BlindCategory[] | null
): Promise<CheckupResponse>;
export async function postCheckup(
  center: LngLat,
  minutes: number,
  phase: "fast" | "full",
  engine: string = "map-fabric",
  blindFilter: BlindCategory[] | null = null
): Promise<CheckupFastResponse | CheckupResponse> {
  return request("/api/v1/checkup", {
    method: "POST",
    body: JSON.stringify({
      center: { ...center, coordType: "bd09ll" },
      minutes,
      engine,
      includeOfficialIsochrone: false,
      includeBlindWalk: true,
      phase,
      blindFilter,
    }),
  });
}

export async function getFabric(
  center: LngLat,
  radiusM: number
): Promise<FabricResponse> {
  const q = new URLSearchParams({
    lng: String(center.lng),
    lat: String(center.lat),
    radiusM: String(Math.round(radiusM)),
  });
  return request(`/api/v1/fabric?${q.toString()}`);
}

export async function getSamples(): Promise<SamplePoint[]> {
  return request("/api/v1/samples");
}

export interface LocateResponse {
  matchedBy: "name" | "coord" | "none";
  community: import("./types").CommunityInfo | null;
  distanceM?: number;
  hitFace?: { name: string | null; placeholder: boolean } | null;
}

/** 点击定位小区：三态（name 命中 / coord 兜底 / none 未命中） */
export async function locateCommunity(center: LngLat): Promise<LocateResponse> {
  const q = new URLSearchParams({
    lng: String(center.lng),
    lat: String(center.lat),
    coordType: "bd09ll",
  });
  return request(`/api/v1/community/locate?${q.toString()}`);
}

/** 附近小区列表：radiusM 内按距离升序（坐标 BD09LL，不含得分） */
export async function getNearbyCommunities(
  lng: number,
  lat: number,
  radiusM: number = 2000
): Promise<NearbyResponse> {
  const q = new URLSearchParams({
    lng: String(lng),
    lat: String(lat),
    coordType: "bd09ll",
    radiusM: String(Math.round(radiusM)),
  });
  return request(`/api/v1/community/nearby?${q.toString()}`);
}

export async function geocode(
  query: string
): Promise<{ lng: number; lat: number; address: string }> {
  return request("/api/v1/geocode", {
    method: "POST",
    body: JSON.stringify({ query }),
  });
}

export async function reverseGeocode(
  center: LngLat
): Promise<{ address: string }> {
  return request("/api/v1/reverse", {
    method: "POST",
    body: JSON.stringify(center),
  });
}

export async function health(): Promise<{ status?: string }> {
  return request("/health");
}

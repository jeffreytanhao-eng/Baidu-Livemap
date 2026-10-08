// ---- GeoJSON (minimal) ----
export interface Position {
  0: number;
  1: number;
}
export type GeoPosition = number[];

export interface Geometry {
  type: string;
  coordinates: any;
}

export interface GeoFeature {
  type: "Feature";
  geometry: Geometry;
  properties?: Record<string, any>;
}

export interface GeoFeatureCollection {
  type: "FeatureCollection";
  features: GeoFeature[];
}

// ---- API contract ----
export interface LngLat {
  lng: number;
  lat: number;
}

export type PoiCategory =
  | "convenience"
  | "leisure"
  | "mall"
  | "education"
  | "health"
  | "public"
  | "transport";

export interface Poi {
  id: string;
  name: string;
  category: PoiCategory;
  lng: number;
  lat: number;
  walkMinutes: number;
  within: boolean;
  /** 直线估算（POI 吸附不到路网节点时），分钟值偏乐观 */
  estimated?: boolean;
  /** 仅 transport：地铁站/公交站（一票制判定用） */
  transportKind?: "metro" | "bus";
  /** 仅 education：school=学校 / kindergarten=幼儿园（小学/中学按名称识别） */
  eduKind?: "school" | "kindergarten";
  /** 重点设施打标：key_school=重点学校 / sanjia=三甲医院 / metro=地铁站 */
  tags?: string[];
}

export interface IsoLayer {
  minutes: number;
  polygon: GeoFeature;
  areaM2: number;
}

export type SectorDirection =
  | "N"
  | "NE"
  | "E"
  | "SE"
  | "S"
  | "SW"
  | "W"
  | "NW";

export interface SectorGap {
  direction: SectorDirection;
  isochroneArea: number;
  circleArea: number;
  gapRatio: number;
  barrierNote?: string;
}

export interface CoverageCategory {
  id: PoiCategory;
  name: string;
  count: number;
  /** 圈内设施数（截断前真实数，可能 >20；前端超 20 显示「20+」） */
  withinCount?: number;
  /** 清洗去重后全量召回数（不截断；无打标路径回退为 count） */
  total?: number;
  nearestMinutes: number | null;
  passed: boolean;
  /** 圈内重点设施名称清单（按步行分钟升序，cap 5） */
  hot?: {
    keySchool?: string[];
    sanjia?: string[];
    metro?: string[];
  };
  /** 仅 transport：地铁站/公交站最近步行分钟（一票制口径） */
  metroNearestMinutes?: number | null;
  busNearestMinutes?: number | null;
  /** 仅 transport：决定性方式（地铁达标 / 仅公交达标 / 均不达标 null） */
  transportMode?: "metro" | "bus" | null;
}

/** 小区租金（某户型 min/max 月租） */
export interface CommunityRent {
  min: number;
  max: number;
}

/** 小区资讯（/api/v1/community/locate 返回；数据来自内置小区库） */
export interface CommunityInfo {
  id: number;
  name: string;
  lng: number;
  lat: number;
  district?: string | null;
  bizcircle?: string | null;
  /** 售卖均价（元/㎡），缺失 → 信息不足 */
  saleAvg?: number | null;
  rent: {
    l1: CommunityRent | null;
    l2: CommunityRent | null;
    l3: CommunityRent | null;
  };
  photoUrl?: string | null;
  intro?: string | null;
}

export interface Coverage {
  score: number;
  categories: CoverageCategory[];
  anchor3Passed: number;
}

// 百度步行对照折线（第三拍注入，BD09LL 坐标）
export interface BaiduRoute {
  name: string;
  path: [number, number][];
  injectedEdges: number;
}

// 盲区筛选口径（合法子集；空数组 = 不统计盲区）
export type BlindCategory = "convenience" | "health" | "education";

export interface CheckupMeta {
  minutes: number;
  engine: string;
  demoMode: boolean;
  degraded: boolean;
  cityWhitelist: boolean;
  methodOnly: boolean;
  center: LngLat;
  elapsedMs: number;
  isochroneArea: number;
  circleArea: number;
  areaRatio: number;
  snapNote?: string;
}

export interface CheckupResponse {
  meta: CheckupMeta;
  fastLayer: GeoFeature;
  layers: IsoLayer[];
  enclaves: GeoFeature[];
  pois: Poi[];
  coverage: Coverage;
  sectorGaps: SectorGap[];
  blindSpots: GeoFeatureCollection;
  blindSummary: {
    conveniencePct: number;
    healthPct: number;
    educationPct: number;
  };
  diagnosis: string[];
  straightCircle: { radiusM: number };
  baiduRoutes: BaiduRoute[];
}

export interface CheckupFastResponse {
  meta: CheckupMeta;
  fastLayer: GeoFeature;
}

export interface FabricResponse {
  roads: GeoFeatureCollection;
  buildings: GeoFeatureCollection;
  /** OSM 建筑空洞占位面（landuse=residential 大院，楼栋未细化） */
  residential?: GeoFeatureCollection;
  water: GeoFeatureCollection;
  barriers: GeoFeatureCollection;
}

export interface SamplePoint {
  id: string;
  name: string;
  center: LngLat;
  minutes: number;
}

/** 附近小区列表项（坐标 BD09LL，与地图一致；不含得分） */
export interface NearbyCommunity {
  id: number;
  name: string;
  lng: number;
  lat: number;
  distanceM: number;
}

export interface NearbyResponse {
  radiusM: number;
  communities: NearbyCommunity[];
}

// ---- UI state ----
export type Phase = "idle" | "fast" | "full";

export interface LayerVisibility {
  isochrone: boolean;
  roads: boolean;
  buildings: boolean;
  water: boolean;
  barriers: boolean;
  pois: boolean;
  straightCircle: boolean;
  baiduRoutes: boolean;
  idw: boolean;
}

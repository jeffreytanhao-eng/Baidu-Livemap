import type {
  CheckupResponse,
  CommunityInfo,
  FabricResponse,
  GeoFeature,
  LayerVisibility,
  LngLat,
  Phase,
} from "@/lib/types";

export interface MapImplProps {
  /** 当前参数中心（标记位置） */
  center: LngLat;
  /** 当前参数分钟（仅用于无结果时的视野估算） */
  minutes: number;
  /** 投影/数据锚点（上次取数中心） */
  anchor: LngLat;
  fabric: FabricResponse | null;
  fastLayer: GeoFeature | null;
  /** true 时显示 fastLayer（full 返回后由精拍替换） */
  showFast: boolean;
  result: CheckupResponse | null;
  visible: LayerVisibility;
  phase: Phase;
  onCenterChange: (c: LngLat) => void;
  /** 报告栏点选的高亮设施（地图上以倒圆锥标出；id 仅作标识，设施为 string、小区为 number） */
  highlightPoi?: { id: string | number; name: string; lng: number; lat: number } | null;
  /** 小区资讯（定位点右侧资讯框；null 不显示） */
  communityInfo?: CommunityInfo | null;
  /** 资讯框坐标兜底距离 */
  communityDistanceM?: number | null;
  /** 关闭资讯框 */
  onCloseCommunityInfo?: () => void;
}

"use client";

import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { EChartsOption } from "echarts";

export function useECharts(option: EChartsOption | null) {
  const ref = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const hasOption = option !== null;

  // 组件首挂载时往往还是无数据空态（图表容器不存在），等 option 首次非空、
  // 容器已挂载后再初始化；此后容器持续存在，option 变化只走 setOption
  useEffect(() => {
    if (!ref.current || !hasOption || chartRef.current) return;
    const chart = echarts.init(ref.current);
    chartRef.current = chart;
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
      chartRef.current = null;
    };
  }, [hasOption]);

  useEffect(() => {
    if (chartRef.current && option) {
      chartRef.current.setOption(option, true);
    }
  }, [option]);

  return ref;
}

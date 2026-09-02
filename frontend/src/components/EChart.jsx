import React, { useEffect, useRef } from "react";
import * as echarts from "echarts";

// Thin React wrapper around Apache ECharts: init on mount, setOption on change,
// auto-resize with the container, and clean up on unmount. `onEvents` is a map of
// echarts event name → handler (e.g. { click: (p) => ... }).
export default function EChart({ option, height = 300, className, onEvents, notMerge = true }) {
  const elRef = useRef(null);
  const chartRef = useRef(null);
  const eventsRef = useRef(onEvents);
  eventsRef.current = onEvents;

  useEffect(() => {
    const chart = echarts.init(elRef.current, null, { renderer: "canvas" });
    chartRef.current = chart;
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(elRef.current);
    return () => { ro.disconnect(); chart.dispose(); chartRef.current = null; };
  }, []);

  useEffect(() => {
    if (chartRef.current && option) chartRef.current.setOption(option, notMerge);
  }, [option, notMerge]);

  // (re)bind events whenever the handler map identity changes
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !onEvents) return;
    const entries = Object.entries(onEvents);
    entries.forEach(([ev, fn]) => chart.on(ev, fn));
    return () => entries.forEach(([ev]) => chart.off(ev));
  }, [onEvents]);

  return <div ref={elRef} className={className} style={{ width: "100%", height }} />;
}

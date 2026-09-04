import React, { useMemo, useState, useEffect } from "react";
import { createPortal } from "react-dom";
import EChart from "../components/EChart.jsx";
import SegTabs from "../components/SegTabs.jsx";
import SelectBox from "../components/SelectBox.jsx";
import SmoothInput from "../components/SmoothInput.jsx";
import DashGrid from "../components/DashGrid.jsx";
import { api, fmt } from "../api";
import { useAsync, Loading, ErrorBox, Stat } from "../components/ui.jsx";
import { Truck, Wallet, Handshake, Package, Target, ClipboardList, BarChart3, TriangleAlert, TrendingUp, Eye, Dna, CalendarDays, CircleCheck } from "lucide-react";

// My Dashboard — permission-scoped dispatch view. The backend resolves the
// user's CRM data grants (stg_user_scope) and returns a compact cube
// (JC × collector × segment) already filtered to their scope; every chart here
// derives from that cube, so the click-to-cross-filter stays instant.

const TT = {
  backgroundColor: "#fff", borderColor: "#e2e8f0", borderWidth: 1, padding: [8, 11],
  textStyle: { color: "#1a202c", fontSize: 12 },
  extraCssText: "box-shadow:0 12px 30px rgba(15,23,42,.16);border-radius:10px;",
};
const ANIM = { animationDuration: 650, animationEasing: "cubicOut" };
const gradV = (c) => ({ type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: c + "55" }, { offset: 1, color: c + "05" }] });
const grad = (c1, c2) => ({ type: "linear", x: 0, y: 0, x2: 1, y2: 0, colorStops: [{ offset: 0, color: c1 }, { offset: 1, color: c2 }] });
const PAL = ["#2a9d8f", "#4880ff", "#b7791f", "#805ad5", "#2f855a", "#c53030", "#28b5e1", "#90a1ac",
  "#d69e2e", "#3182ce", "#38a169", "#e53e3e", "#718096"];
const SHAPE_DIST = [{ id: "donut", label: "Donut" }, { id: "pie", label: "Pie" }, { id: "bar", label: "Bar" }];

// Where each card sits until the user arranges the page themselves (12
// columns; one row unit is 30px + a 14px gutter). A saved layout wins.
const DASH_DEFAULTS = {
  byColl:     { x: 0, y: 0, w: 6, h: 8 },
  bySeg:      { x: 6, y: 0, w: 6, h: 8 },
  projCanvas: { x: 0, y: 8, w: 12, h: 10 },
  jcQty:      { x: 0, y: 18, w: 6, h: 8 },
  jcAcc:      { x: 6, y: 18, w: 6, h: 8 },
  jcItems:    { x: 0, y: 26, w: 6, h: 8 },
  status:     { x: 6, y: 26, w: 6, h: 8 },
  group:      { x: 0, y: 34, w: 12, h: 11 },
  compare:    { x: 0, y: 45, w: 12, h: 12 },
  pipeline:   { x: 0, y: 57, w: 12, h: 10 },
  missing:    { x: 0, y: 67, w: 12, h: 12 },
};
const abbr = (v) => {
  const n = Math.abs(v);
  if (n >= 1e7) return (v / 1e7).toFixed(n >= 1e8 ? 0 : 1) + "Cr";
  if (n >= 1e5) return (v / 1e5).toFixed(n >= 1e6 ? 0 : 1) + "L";
  if (n >= 1e3) return (v / 1e3).toFixed(0) + "K";
  return fmt.num(v);
};

// projection-accuracy flags (same ±20% band the RM plan uses)
const FLAGS = {
  ontrack: { label: "On-track", color: "#2f855a" },
  over: { label: "Over-projected", color: "#b7791f" },
  under: { label: "Under-projected", color: "#3182ce" },
  none: { label: "No projection", color: "#c53030" },
  new: { label: "New (no sales yet)", color: "#90a1ac" },
};

function distOption(rows, { shape, unit, center, selected }) {
  const data = rows.map((r, i) => ({
    value: r.value, name: r.name,
    itemStyle: {
      color: r.color || PAL[i % PAL.length],
      opacity: selected && selected !== r.name ? 0.28 : 1,
    },
  }));
  if (shape === "bar") {
    const rev = [...data].reverse();
    return {
      ...ANIM, grid: { left: 8, right: 24, top: 12, bottom: 8, containLabel: true },
      tooltip: { ...TT, trigger: "axis", axisPointer: { type: "shadow" },
        formatter: (ps) => `${ps[0].name}<br/><b>${fmt.num(ps[0].value)}</b> ${unit}` },
      xAxis: { type: "value", splitLine: { lineStyle: { color: "#eef1f5" } },
        axisLabel: { color: "#90a1ac", fontSize: 11, formatter: abbr, hideOverlap: true },
        axisLine: { show: false }, axisTick: { show: false } },
      yAxis: { type: "category", data: rev.map((d) => d.name),
        axisLabel: { color: "#414d55", fontSize: 11, width: 110, overflow: "truncate", hideOverlap: true },
        axisTick: { show: false }, axisLine: { show: false } },
      series: [{ type: "bar", barWidth: "56%", itemStyle: { borderRadius: [0, 6, 6, 0] }, data: rev }],
    };
  }
  const inner = shape === "pie" ? "0%" : "54%";
  return {
    ...ANIM,
    tooltip: { ...TT, trigger: "item",
      formatter: (p) => `${p.marker} ${p.name}<br/><b style="font-size:13px">${fmt.num(p.value)}</b> ${unit} · ${p.percent}%` },
    legend: { bottom: 0, icon: "circle", itemWidth: 9, itemHeight: 9, type: "scroll",
      textStyle: { color: "#414d55", fontSize: 11 } },
    ...(shape === "donut" ? {
      title: { text: abbr(rows.reduce((a, d) => a + d.value, 0)), subtext: center,
        left: "center", top: "34%",
        textStyle: { fontSize: 20, fontWeight: 700, color: "#1f3a5f" },
        subtextStyle: { fontSize: 11, color: "#90a1ac" } },
    } : {}),
    series: [{
      type: "pie", radius: [inner, "76%"], center: ["50%", shape === "donut" ? "42%" : "45%"],
      avoidLabelOverlap: true, itemStyle: { borderRadius: 6, borderColor: "#fff", borderWidth: 2 },
      label: { show: false }, labelLine: { show: false },
      emphasis: { scale: true, scaleSize: 8, itemStyle: { shadowBlur: 14, shadowColor: "rgba(0,0,0,.18)" } },
      data,
    }],
  };
}

// Projection-vs-sales as a bordered GRID: every data point in its own cell,
// mini bars kept for the visual ratio. Rows click through to the item's
// JC-wise trend popup.
const CELL = { border: "1px solid var(--border)", padding: "8px 10px", verticalAlign: "middle" };
const HCELL = { ...CELL, background: "#f7fafc", fontSize: 12, color: "#414d55",
  fontWeight: 600, whiteSpace: "nowrap" };

function MiniBar({ label, value, max, color }) {
  const pct = Math.max(value > 0 ? 3 : 0, Math.round((value / (max || 1)) * 100));
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <span style={{ fontSize: 10, color: "var(--muted)", width: 32, flex: "none" }}>{label}</span>
      <div style={{ flex: 1, height: 7, background: "#eef2f7", borderRadius: 4, overflow: "hidden" }}>
        <i style={{ display: "block", height: "100%", width: "100%", background: color,
          borderRadius: 4, transform: `scaleX(${pct / 100})`, transformOrigin: "left",
          transition: "transform .45s cubic-bezier(.2,.7,.3,1)" }} />
      </div>
    </div>
  );
}

function ProjCompareTable({ rows, onItem }) {
  return (
    <div className="tbl-wrap">
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
      <thead>
        <tr>
          <th style={{ ...HCELL, textAlign: "left" }}>Item</th>
          <th style={{ ...HCELL, textAlign: "left", width: "22%" }}>Sales vs Projection</th>
          <th style={{ ...HCELL, textAlign: "right" }}>3-JC avg sales (KG)</th>
          <th style={{ ...HCELL, textAlign: "right" }}>Projection (KG)</th>
          <th style={{ ...HCELL, textAlign: "right" }}>Projected %</th>
          <th style={{ ...HCELL, textAlign: "center" }}>Status</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => {
          const f = FLAGS[r.flag] || FLAGS.ontrack;
          const max = Math.max(r.avg3, r.proj);
          const acc = r.avg3 > 0 ? Math.round((r.proj / r.avg3) * 100) : null;
          return (
            <tr key={i} onClick={() => onItem(r)} style={{ cursor: "pointer" }}
              title="Click to see this item's JC-wise graph">
              <td style={{ ...CELL, maxWidth: 280 }}>
                <div title={r.name} style={{ fontWeight: 600, color: "#1f3a5f", whiteSpace: "nowrap",
                  overflow: "hidden", textOverflow: "ellipsis" }}>{r.name}</div>
                <div style={{ fontSize: 10.5, color: "var(--muted)" }}>{r.code || "—"}</div>
              </td>
              <td style={CELL}>
                <div style={{ display: "grid", gap: 5 }}>
                  <MiniBar label="Sales" value={r.avg3} max={max} color="#2a9d8f" />
                  <MiniBar label="Proj" value={r.proj} max={max} color="#4880ff" />
                </div>
              </td>
              <td style={{ ...CELL, textAlign: "right", fontWeight: 600 }}>{fmt.num(r.avg3)}</td>
              <td style={{ ...CELL, textAlign: "right", fontWeight: 600 }}>{fmt.num(r.proj)}</td>
              <td style={{ ...CELL, textAlign: "right", fontWeight: 600,
                color: acc == null ? "var(--muted)" : f.color }}>
                {acc == null ? "—" : `${acc}%`}
              </td>
              <td style={{ ...CELL, textAlign: "center" }}>
                <span style={{ display: "inline-block", fontSize: 11, fontWeight: 600, color: f.color,
                  background: f.color + "16", padding: "3px 9px", borderRadius: 999, whiteSpace: "nowrap" }}>
                  {f.label}
                </span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
    </div>
  );
}

// popup: one item's dispatched KG per JC (scoped) + projection reference lines
function ItemGraphModal({ target, idParams, onClose }) {
  const { data, loading, error } = useAsync(
    () => (target ? api.myDashboardItem({ ...idParams, item: target.name, code: target.code || "" })
      : Promise.resolve(null)),
    [target && target.name, target && target.code]
  );
  useEffect(() => {
    if (!target) return;
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [target, onClose]);

  const opt = useMemo(() => {
    if (!data) return null;
    const marks = [];
    if (data.proj > 0) marks.push({ yAxis: data.proj, lineStyle: { color: "#4880ff" },
      label: { formatter: `Projection ${abbr(data.proj)}`, color: "#4880ff", fontSize: 11 } });
    if (data.avg3 > 0) marks.push({ yAxis: data.avg3, lineStyle: { color: "#2a9d8f" },
      label: { formatter: `3-JC avg ${abbr(data.avg3)}`, color: "#2a9d8f", fontSize: 11 } });
    return {
      ...ANIM, grid: { left: 8, right: 90, top: 24, bottom: 8, containLabel: true },
      tooltip: { ...TT, trigger: "axis", axisPointer: { type: "shadow" },
        formatter: (ps) => {
          const j = (data.jcs || [])[ps[0].dataIndex] || {};
          const d = j.from ? `<br/><span style="color:#90a1ac;font-size:11px">${j.from} → ${j.to}</span>` : "";
          return `${ps[0].name}${d}<br/><b>${fmt.num(ps[0].value)}</b> KG despatched`;
        } },
      xAxis: { type: "category", data: (data.jcs || []).map((j) => j.label),
        axisTick: { show: false }, axisLabel: { color: "#414d55", fontSize: 11, hideOverlap: true } },
      yAxis: { type: "value", splitLine: { lineStyle: { color: "#eef1f5" } },
        axisLabel: { color: "#90a1ac", fontSize: 11, formatter: abbr, hideOverlap: true } },
      series: [{ type: "bar", barWidth: "55%", data: data.qty || [],
        itemStyle: { borderRadius: [5, 5, 0, 0], color: "#7aa7ff" },
        markLine: marks.length ? { symbol: "none", lineStyle: { type: "dashed", width: 1.6 },
          data: marks } : undefined }],
    };
  }, [data]);

  if (!target) return null;
  const f = data ? (FLAGS[data.flag] || FLAGS.ontrack) : null;
  return createPortal(
    <div className="modal-overlay" onMouseDown={onClose}>
      <div className="modal-container" role="dialog" aria-modal="true"
        style={{ maxWidth: 780, width: "94vw" }} onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-container-header">
          <div className="modal-container-title" style={{ minWidth: 0 }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 7, overflow: "hidden" }}>
              <TrendingUp size={16} style={{ flex: "none" }} /> <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{target.name}</span>
            </span>
          </div>
          <button className="icon-button" type="button" aria-label="Close" onClick={onClose}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
              strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="modal-container-body">
          {loading && <Loading what="item trend" />}
          {error && <ErrorBox msg={error} />}
          {data && !loading && (
            <>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center",
                marginBottom: 10, fontSize: 12 }}>
                {f && (
                  <span style={{ fontSize: 11, fontWeight: 600, color: f.color,
                    background: f.color + "16", padding: "3px 9px", borderRadius: 999 }}>{f.label}</span>
                )}
                <span style={{ color: "var(--muted)" }}>
                  Projection JC{data.plan_jc}: <b style={{ color: "#1f3a5f" }}>{fmt.num(data.proj)}</b> ·
                  Next JC: <b style={{ color: "#1f3a5f" }}>{fmt.num(data.next1)}</b> ·
                  JC after next: <b style={{ color: "#1f3a5f" }}>{fmt.num(data.next2)}</b> ·
                  3-JC avg sales: <b style={{ color: "#1f3a5f" }}>{fmt.num(data.avg3)}</b> KG
                  {data.basis === "collector" ? " · projections for your collectors" : ""}
                </span>
              </div>
              <EChart option={opt} height={300} />
              <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 8 }}>
                Despatched KG per job cycle within your scope · dashed lines mark the JC{data.plan_jc}
                projection and your 3-JC sales average.
              </div>
            </>
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}

// the 3-cycle projection pipeline: current JC + the two after it
function pipeOption(pipe) {
  const COLORS = ["#4880ff", "#7aa7ff", "#b9cdfd"];
  return {
    ...ANIM, grid: { left: 8, right: 16, top: 34, bottom: 8, containLabel: true },
    tooltip: { ...TT, trigger: "axis", axisPointer: { type: "shadow" },
      formatter: (ps) => {
        const b = pipe[ps[0].dataIndex] || {};
        return `${b.label}<br/><b>${fmt.num(b.kg)}</b> KG projected` +
          `<br/><span style="color:#90a1ac">${fmt.num(b.items)} items with a projection</span>`;
      } },
    xAxis: { type: "category", data: pipe.map((b) => b.label), axisTick: { show: false },
      axisLabel: { color: "#414d55", fontSize: 11 } },
    yAxis: { type: "value", splitLine: { lineStyle: { color: "#eef1f5" } },
      axisLabel: { color: "#90a1ac", fontSize: 11, formatter: abbr, hideOverlap: true } },
    series: [{ type: "bar", barWidth: "48%",
      label: { show: true, position: "top", fontSize: 12, fontWeight: 600,
        color: "#1f3a5f", formatter: (o) => abbr(o.value) },
      data: pipe.map((b, i) => ({ value: b.kg,
        itemStyle: { color: COLORS[i % 3], borderRadius: [6, 6, 0, 0] } })) }],
  };
}

// The three single-number projection metrics share one canvas: pick the metric,
// then read it as a chart or as the table behind it.
const PROJ_METRICS = [
  { id: "accuracy", label: "Accuracy", icon: Target, title: "Accuracy on projected items" },
  { id: "volume", label: "Volume projected", icon: BarChart3, title: "Sales volume projected" },
  { id: "items", label: "Items missing", icon: TriangleAlert, title: "Items with no projection" },
];

// Single-number projection metrics, drawn rather than printed on a card.
const accColor = (v) => (v == null ? "#90a1ac" : v < 40 ? "#c53030" : v < 70 ? "#b7791f" : "#2f855a");

export function gaugeOption(value, caption) {
  const c = accColor(value);
  return {
    ...ANIM,
    tooltip: { ...TT, trigger: "item",
      formatter: () => `Accuracy on projected items<br/><b style="font-size:14px">${value == null ? "—" : value + "%"}</b>` +
        `<br/><span style="color:#90a1ac">100 − WMAPE across completed JCs</span>` },
    series: [{
      type: "gauge", startAngle: 205, endAngle: -25, min: 0, max: 100,
      radius: "80%", center: ["50%", "58%"],
      progress: { show: true, width: 14, roundCap: true, itemStyle: { color: c } },
      axisLine: { lineStyle: { width: 14, color: [[1, "#eef2f7"]] } },
      pointer: { show: false }, anchor: { show: false },
      axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false },
      // valueAnimation feeds the formatter RAW interpolated floats — without
      // rounding it renders "8.42361111111111%" mid-animation, which overflows
      // the card at this font size.
      detail: { valueAnimation: true, fontSize: 26, fontWeight: 700, offsetCenter: [0, "2%"],
        formatter: (v) => (value == null ? "—" : `${Number(v).toFixed(1)}%`), color: c },
      title: { offsetCenter: [0, "36%"], fontSize: 11, color: "#90a1ac", width: 150, overflow: "truncate" },
      data: [{ value: value == null ? 0 : value, name: caption }],
    }],
  };
}

// Donut for a two-way split (projected vs not), with the share in the middle.
export function ratioOption(rows, { centerText, centerSub, unit }) {
  return {
    ...ANIM,
    tooltip: { ...TT, trigger: "item",
      formatter: (o) => `${o.marker} ${o.name}<br/><b style="font-size:13px">${fmt.num(o.value)}</b> ${unit} · ${o.percent}%` },
    legend: { bottom: 0, icon: "circle", itemWidth: 9, itemHeight: 9,
      textStyle: { color: "#414d55", fontSize: 11 } },
    title: { text: centerText, subtext: centerSub, left: "center", top: "31%",
      textStyle: { fontSize: 24, fontWeight: 700, color: "#1f3a5f" },
      subtextStyle: { fontSize: 11, color: "#90a1ac" } },
    series: [{
      type: "pie", radius: ["58%", "80%"], center: ["50%", "42%"], avoidLabelOverlap: true,
      itemStyle: { borderRadius: 6, borderColor: "#fff", borderWidth: 2 },
      label: { show: false }, labelLine: { show: false },
      emphasis: { scale: true, scaleSize: 8, itemStyle: { shadowBlur: 14, shadowColor: "rgba(0,0,0,.18)" } },
      data: rows.map((r) => ({ value: r.value, name: r.name, itemStyle: { color: r.color } })),
    }],
  };
}

// ── projection analytics charts (JC trend / accuracy / items / item group) ────

// Projected KG per JC (bars) against actual sales (line on its own axis) — the
// two live on very different scales, so a shared axis would flatten one of them.
function jcQtyOption(trend) {
  return {
    ...ANIM, grid: { left: 8, right: 8, top: 34, bottom: 8, containLabel: true },
    legend: { top: 0, icon: "roundRect", itemWidth: 10, itemHeight: 10,
      textStyle: { color: "#414d55", fontSize: 11 } },
    tooltip: { ...TT, trigger: "axis", axisPointer: { type: "shadow" },
      formatter: (ps) => {
        const t = trend[ps[0].dataIndex] || {};
        const when = t.from ? `<span style="color:#90a1ac;font-size:11px"> ${t.from} → ${t.to}</span>` : "";
        if (!t.done) {
          return `<b>${t.label}</b>${when}<br/>Projected: <b>${fmt.num(t.proj)}</b> KG` +
            `<br/><span style="color:#90a1ac">planning cycle — not dispatched yet</span>`;
        }
        return `<b>${t.label}</b>${when}` +
          `<br/>Projected: <b>${fmt.num(t.proj)}</b> KG` +
          `<br/>Actual sales: <b>${fmt.num(t.actual)}</b> KG` +
          `<br/><span style="color:#90a1ac">${t.items_projected} of ${t.items_sold} selling items projected</span>`;
      } },
    xAxis: { type: "category", data: trend.map((t) => t.label), axisTick: { show: false },
      axisLabel: { color: "#414d55", fontSize: 11 } },
    yAxis: [
      { type: "value", name: "Projected", nameTextStyle: { color: "#90a1ac", fontSize: 10 },
        splitLine: { lineStyle: { color: "#eef1f5" } },
        axisLabel: { color: "#90a1ac", fontSize: 11, formatter: abbr, hideOverlap: true } },
      { type: "value", name: "Actual", nameTextStyle: { color: "#90a1ac", fontSize: 10 },
        splitLine: { show: false },
        axisLabel: { color: "#90a1ac", fontSize: 11, formatter: abbr, hideOverlap: true } },
    ],
    series: [
      { name: "Projected KG", type: "bar", barWidth: "48%", yAxisIndex: 0,
        data: trend.map((t) => ({ value: t.proj,
          itemStyle: { borderRadius: [5, 5, 0, 0], color: t.done ? "#4880ff" : "#b9cdfd" } })) },
      { name: "Actual sales KG", type: "line", yAxisIndex: 1, smooth: true,
        symbol: "circle", symbolSize: 7, lineStyle: { width: 3, color: "#2a9d8f" },
        itemStyle: { color: "#2a9d8f" }, data: trend.map((t) => t.actual) },
    ],
  };
}

// Accuracy (100 - WMAPE) on the items that WERE projected, plus how much of the
// scope's sales volume carried a projection at all.
function jcAccOption(trend) {
  return {
    ...ANIM, grid: { left: 8, right: 8, top: 34, bottom: 8, containLabel: true },
    legend: { top: 0, icon: "roundRect", itemWidth: 10, itemHeight: 10,
      textStyle: { color: "#414d55", fontSize: 11 } },
    tooltip: { ...TT, trigger: "axis",
      formatter: (ps) => {
        const t = trend[ps[0].dataIndex] || {};
        return `<b>${t.label}</b>` +
          `<br/>Accuracy on projected items: <b>${t.accuracy_proj == null ? "—" : t.accuracy_proj + "%"}</b>` +
          `<br/>Sales volume projected: <b>${t.coverage_pct == null ? "—" : t.coverage_pct + "%"}</b>` +
          `<br/><span style="color:#90a1ac">incl. unprojected items: ${t.accuracy == null ? "—" : t.accuracy + "%"}</span>`;
      } },
    xAxis: { type: "category", data: trend.map((t) => t.label), axisTick: { show: false },
      axisLabel: { color: "#414d55", fontSize: 11 } },
    yAxis: { type: "value", min: 0, max: 100, splitLine: { lineStyle: { color: "#eef1f5" } },
      axisLabel: { color: "#90a1ac", fontSize: 11, formatter: (v) => `${v}%` } },
    series: [
      { name: "Accuracy (projected items)", type: "line", smooth: true, symbol: "circle",
        symbolSize: 8, lineStyle: { width: 3, color: "#2f855a" }, itemStyle: { color: "#2f855a" },
        areaStyle: { color: gradV("#2f855a") },
        data: trend.map((t) => t.accuracy_proj) },
      { name: "Sales volume projected", type: "line", smooth: true, symbol: "circle",
        symbolSize: 6, lineStyle: { width: 2, type: "dashed", color: "#b7791f" },
        itemStyle: { color: "#b7791f" }, data: trend.map((t) => t.coverage_pct) },
    ],
  };
}

// How many items carried a projection each JC, against how many actually sold.
function jcItemsOption(trend) {
  return {
    ...ANIM, grid: { left: 8, right: 8, top: 34, bottom: 8, containLabel: true },
    legend: { top: 0, icon: "roundRect", itemWidth: 10, itemHeight: 10,
      textStyle: { color: "#414d55", fontSize: 11 } },
    tooltip: { ...TT, trigger: "axis", axisPointer: { type: "shadow" },
      formatter: (ps) => {
        const t = trend[ps[0].dataIndex] || {};
        if (!t.done) {
          return `<b>${t.label}</b><br/>Items with a projection: <b>${fmt.num(t.items_projected)}</b>` +
            `<br/><span style="color:#90a1ac">planning cycle — not dispatched yet</span>`;
        }
        const pct = t.items_sold ? Math.round((t.items_projected / t.items_sold) * 100) : null;
        return `<b>${t.label}</b><br/>Items with a projection: <b>${fmt.num(t.items_projected)}</b>` +
          `<br/>Items that sold: <b>${fmt.num(t.items_sold)}</b>` +
          (pct == null ? "" : `<br/><span style="color:#90a1ac">${pct}% of selling items projected</span>`);
      } },
    xAxis: { type: "category", data: trend.map((t) => t.label), axisTick: { show: false },
      axisLabel: { color: "#414d55", fontSize: 11 } },
    yAxis: { type: "value", splitLine: { lineStyle: { color: "#eef1f5" } },
      axisLabel: { color: "#90a1ac", fontSize: 11, formatter: abbr, hideOverlap: true } },
    series: [
      { name: "Items projected", type: "bar", barWidth: "48%",
        data: trend.map((t) => ({ value: t.items_projected,
          itemStyle: { borderRadius: [5, 5, 0, 0], color: t.done ? "#805ad5" : "#cdbdf0" } })) },
      { name: "Items sold", type: "line", smooth: true, symbol: "circle", symbolSize: 7,
        lineStyle: { width: 3, color: "#90a1ac" }, itemStyle: { color: "#90a1ac" },
        data: trend.map((t) => t.items_sold) },
    ],
  };
}

// Projected KG per item group. Single series on purpose: group sales volumes
// differ by orders of magnitude (a big trading group would flatten every other
// bar), so sales / accuracy / gaps ride in the tooltip and the table view.
function groupOption(rows) {
  const rev = [...rows].reverse();
  return {
    ...ANIM, grid: { left: 8, right: 26, top: 12, bottom: 8, containLabel: true },
    tooltip: { ...TT, trigger: "axis", axisPointer: { type: "shadow" },
      formatter: (ps) => {
        const g = rev[ps[0].dataIndex] || {};
        return `<b>${g.name}</b><br/>Projected: <b>${fmt.num(g.proj)}</b> KG` +
          `<br/>3-JC avg sales: <b>${fmt.num(g.avg3)}</b> KG` +
          `<br/>Accuracy (projected items): <b>${g.accuracy_proj == null ? "—" : g.accuracy_proj + "%"}</b>` +
          `<br/><span style="color:${g.missing ? "#c53030" : "#90a1ac"}">${g.missing} of ${g.items} items without a projection</span>`;
      } },
    xAxis: { type: "value", splitLine: { lineStyle: { color: "#eef1f5" } },
      axisLabel: { color: "#90a1ac", fontSize: 11, formatter: abbr, hideOverlap: true },
      axisLine: { show: false }, axisTick: { show: false } },
    yAxis: { type: "category", data: rev.map((g) => g.name),
      axisLabel: { color: "#414d55", fontSize: 11, width: 150, overflow: "truncate", hideOverlap: true },
      axisTick: { show: false }, axisLine: { show: false } },
    series: [{ type: "bar", barWidth: "58%",
      itemStyle: { borderRadius: [0, 6, 6, 0], color: grad("#7aa7ff", "#4880ff") },
      data: rev.map((g) => g.proj) }],
  };
}

// Unprojected selling items, biggest first — the action list.
function missingOption(rows, total) {
  const rev = [...rows].reverse();
  return {
    ...ANIM, grid: { left: 8, right: 26, top: 12, bottom: 8, containLabel: true },
    tooltip: { ...TT, trigger: "axis", axisPointer: { type: "shadow" },
      formatter: (ps) => {
        const m = rev[ps[0].dataIndex] || {};
        const share = total ? ((m.avg3 / total) * 100).toFixed(1) : null;
        return `<b>${m.name}</b>` + (m.code ? `<br/><span style="color:#90a1ac;font-size:11px">${m.code}</span>` : "") +
          `<br/><b>${fmt.num(m.avg3)}</b> KG avg / JC sold` +
          (share ? `<br/><span style="color:#c53030">${share}% of your unprojected volume</span>` : "") +
          `<br/><span style="color:#90a1ac">no projection submitted</span>`;
      } },
    xAxis: { type: "value", splitLine: { lineStyle: { color: "#eef1f5" } },
      axisLabel: { color: "#90a1ac", fontSize: 11, formatter: abbr, hideOverlap: true },
      axisLine: { show: false }, axisTick: { show: false } },
    yAxis: { type: "category", data: rev.map((m) => m.name),
      axisLabel: { color: "#414d55", fontSize: 11, width: 190, overflow: "truncate", hideOverlap: true },
      axisTick: { show: false }, axisLine: { show: false } },
    series: [{ type: "bar", barWidth: "58%",
      itemStyle: { borderRadius: [0, 6, 6, 0], color: grad("#f0a0a0", "#c53030") },
      data: rev.map((m) => m.avg3) }],
  };
}

export default function Dashboard({ session, isAdmin }) {
  const u = session?.user || {};

  // admin "View as" switcher — preview any persona / mapped user's dashboard
  const [viewAs, setViewAs] = useState({ persona: "", username: "" });
  const personas = useAsync(() => (isAdmin ? api.myDashboardPersonas() : Promise.resolve(null)), []);
  const plist = personas.data?.personas || [];
  const pickPersona = (e) => {
    const p = e.target.value;
    const first = plist.find((x) => x.persona === p)?.users?.[0]?.username || "";
    setViewAs({ persona: p, username: p ? first : "" });
  };
  const pickUser = (e) => setViewAs((v) => ({ ...v, username: e.target.value }));

  const { data, loading, error } = useAsync(
    () => api.myDashboard(viewAs.username
      ? { username: viewAs.username, persona: viewAs.persona }
      : { username: u.username || u.user_code || "", email: u.email || "", admin: isAdmin ? 1 : 0 }),
    [viewAs.username, viewAs.persona]
  );

  const [metric, setMetric] = useState("qty");           // qty (KG) | value (₹)
  const [shape, setShape] = useState({ coll: "bar", seg: "donut", proj: "donut" });
  const setSh = (k) => (v) => setShape((s) => ({ ...s, [k]: v }));
  const [sel, setSel] = useState({ collector: null, segment: null });   // cross-filter
  const toggle = (k) => (name) => setSel((s) => ({ ...s, [k]: s[k] === name ? null : name }));
  useEffect(() => { setSel({ collector: null, segment: null }); }, [viewAs.username, viewAs.persona]);

  const viewUsers = plist.find((x) => x.persona === viewAs.persona)?.users || [];
  const switcher = isAdmin && plist.length > 0 && (
    <div className="card" style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10,
      padding: "10px 16px", marginBottom: 14, background: viewAs.username ? "#FFF9EF" : undefined }}>
      <b style={{ fontSize: 13, display: "inline-flex", alignItems: "center", gap: 6 }}><Eye size={15} /> View as</b>
      <SelectBox className="searchbox" style={{ maxWidth: 250 }} value={viewAs.persona} onChange={pickPersona}>
        <option value="">Myself (Admin — all data)</option>
        {plist.map((p) => (
          <option key={p.persona} value={p.persona}>{p.persona} ({p.users.length} users)</option>
        ))}
      </SelectBox>
      {viewAs.persona && (
        <SelectBox className="searchbox" style={{ maxWidth: 280 }} value={viewAs.username} onChange={pickUser}>
          {viewUsers.map((us) => (
            <option key={us.username} value={us.username}>{us.user_name} — {us.username}</option>
          ))}
        </SelectBox>
      )}
      {viewAs.username && (
        <span style={{ fontSize: 12, color: "var(--muted)" }}>
          previewing this user’s dashboard — exactly what they see
        </span>
      )}
    </div>
  );

  const cube = data?.cube || [];
  const unit = metric === "qty" ? "KG" : "₹";

  // each dist chart respects only the OTHER cross-filter's selection
  // (Power BI behaviour: its own selection just highlights).
  const byColl = useMemo(() => {
    const m = {};
    cube.forEach((r) => {
      if (sel.segment && r.segment !== sel.segment) return;
      m[r.collector] = (m[r.collector] || 0) + r[metric];
    });
    return Object.entries(m).map(([name, v]) => ({ name, value: Math.round(v) }))
      .filter((d) => d.value > 0).sort((a, b) => b.value - a.value);
  }, [cube, sel.segment, metric]);

  const bySeg = useMemo(() => {
    const m = {};
    cube.forEach((r) => {
      if (sel.collector && r.collector !== sel.collector) return;
      m[r.segment] = (m[r.segment] || 0) + r[metric];
    });
    return Object.entries(m).map(([name, v]) => ({ name, value: Math.round(v) }))
      .filter((d) => d.value > 0).sort((a, b) => b.value - a.value);
  }, [cube, sel.collector, metric]);

  const collEvents = useMemo(() => ({ click: (p) => { if (p.name) toggle("collector")(p.name); } }), []);
  const segEvents = useMemo(() => ({ click: (p) => { if (p.name) toggle("segment")(p.name); } }), []);

  const collOpt = useMemo(() => distOption(byColl, { shape: shape.coll, unit, center: unit, selected: sel.collector }),
    [byColl, shape.coll, unit, sel.collector]);
  const segOpt = useMemo(() => distOption(bySeg, { shape: shape.seg, unit, center: unit, selected: sel.segment }),
    [bySeg, shape.seg, unit, sel.segment]);

  // ── projection accuracy (plan-table projection vs scoped 3-JC avg sales) ──
  const p = data?.projection;
  const pipeOpt = useMemo(() => pipeOption(p?.pipeline || []), [p]);

  // click-to-drill: which item's JC graph is open, and the identity the popup
  // fetch must use (respects the admin View-as impersonation)
  const [itemPop, setItemPop] = useState(null);

  // pipeline detail table (row per product) with a chart/table toggle + search
  const [pipeView, setPipeView] = useState("chart");
  const [pipeQ, setPipeQ] = useState("");
  useEffect(() => {
    setPipeView("chart"); setPipeQ(""); setItemPop(null);
    setGroupView("chart"); setMissView("chart"); setMissQ("");
    setProjMetric("accuracy"); setProjView("chart");
  }, [viewAs.username, viewAs.persona]);

  // Personal arrangements are per LOGGED-IN USER (and per browser, since they
  // live in localStorage) — two people sharing a machine keep their own.
  const layoutKey = `mydash_layout_v1:${u.user_code || u.username || "anon"}`;
  // both layers in one call: the app-level default + this user's own arrangement
  const me = (u.user_code || u.username || "").trim();
  const savedLayout = useAsync(() => api.dashboardLayout("mydash", me), [me]);

  const idParams = useMemo(() => (viewAs.username
    ? { username: viewAs.username, persona: viewAs.persona }
    : { username: u.username || u.user_code || "", email: u.email || "", admin: isAdmin ? 1 : 0 }),
  // eslint-disable-next-line react-hooks/exhaustive-deps
  [viewAs.username, viewAs.persona, isAdmin]);
  const pipeRows = useMemo(() => {
    const src = p?.pipeline_rows || [];
    const q = pipeQ.trim().toLowerCase();
    if (!q) return src;
    return src.filter((r) => (r.name || "").toLowerCase().includes(q) ||
      (r.code || "").toLowerCase().includes(q));
  }, [p, pipeQ]);
  const statusRows = useMemo(() => (p?.summary || []).map((s) => ({
    name: FLAGS[s.flag]?.label || s.flag, value: s.items, color: FLAGS[s.flag]?.color })), [p]);
  const statusOpt = useMemo(() => distOption(statusRows, { shape: shape.proj, unit: "items", center: "items" }),
    [statusRows, shape.proj]);
  const accGaugeOpt = useMemo(
    () => gaugeOption(p?.overall_accuracy_proj ?? null, "on projected items"), [p]);
  const volRatioOpt = useMemo(() => ratioOption([
    { name: "Projected", value: p?.covered_kg || 0, color: "#2a9d8f" },
    { name: "No projection", value: p?.uncovered_kg || 0, color: "#c53030" },
  ], { centerText: `${p?.coverage_pct ?? 0}%`, centerSub: "of sales volume", unit: "KG / JC" }), [p]);
  const itemRatioOpt = useMemo(() => {
    const projected = p?.items_projected || 0, missing = p?.missing_total || 0;
    const tot = projected + missing;
    return ratioOption([
      { name: "Has a projection", value: projected, color: "#4880ff" },
      { name: "No projection", value: missing, color: "#c53030" },
    ], { centerText: fmt.num(missing), centerSub: "items unprojected",
      unit: tot ? "items" : "items" });
  }, [p]);
  const [projMetric, setProjMetric] = useState("accuracy");
  const [projView, setProjView] = useState("chart");
  const lastDoneJc = useMemo(() => {
    const done = (p?.jc_trend || []).filter((t) => t.done);
    return done.length ? done[done.length - 1].jc : "—";
  }, [p]);
  const jcQtyOpt = useMemo(() => jcQtyOption(p?.jc_trend || []), [p]);
  const jcAccOpt = useMemo(() => jcAccOption(p?.jc_trend || []), [p]);
  const jcItemsOpt = useMemo(() => jcItemsOption(p?.jc_trend || []), [p]);

  // item-group roll-up (Segment 3 / Segment 2) with a chart/table toggle
  const [groupLevel, setGroupLevel] = useState("segment3");
  const [groupView, setGroupView] = useState("chart");
  const groupRows = useMemo(() => (p?.by_group?.[groupLevel] || []), [p, groupLevel]);
  const groupOpt = useMemo(() => groupOption(groupRows), [groupRows]);

  // missing projections: top-N chart, or the full searchable table
  const [missView, setMissView] = useState("chart");
  const [missQ, setMissQ] = useState("");
  const missChart = useMemo(() => (p?.missing_all || []).slice(0, 15), [p]);
  const missOpt = useMemo(() => missingOption(missChart, p?.missing_kg || 0), [missChart, p]);
  const missTableRows = useMemo(() => {
    const src = (p?.missing_all || []).map((m, i) => ({ ...m, rank: i + 1 }));
    const q = missQ.trim().toLowerCase();
    if (!q) return src;
    return src.filter((m) => (m.name || "").toLowerCase().includes(q) ||
      (m.code || "").toLowerCase().includes(q));
  }, [p, missQ]);

  // A card showing its TABLE takes the full row, sized to the rows it actually
  // has. One grid unit is 30px plus a 14px gutter, so h units == 44h - 14 px.
  // Long tables stop growing at MAX_ROWS and scroll inside the card instead.
  const MAX_ROWS = 12;
  const fitRows = (rows, toolbar = false) => {
    const px = 62                       // card header (title + sub + toggles)
      + (toolbar ? 46 : 0)              // search / segment bar, when present
      + 38                              // table header
      + Math.min(rows, MAX_ROWS) * 37   // body rows
      + 42;                             // card padding + breathing room
    return Math.max(5, Math.ceil((px + 14) / 44));
  };
  const expandedCards = useMemo(() => {
    const out = {};
    if (projView === "table") {
      out.projCanvas = projMetric === "accuracy"
        ? fitRows((p?.jc_trend?.length || 0) + 1)      // + the overall row
        : fitRows(groupRows.length, true);
    }
    if (groupView === "table") out.group = fitRows(groupRows.length);
    if (pipeView === "table") out.pipeline = fitRows(pipeRows.length, true);
    if (missView === "table") out.missing = fitRows(missTableRows.length, true);
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projView, projMetric, groupView, pipeView, missView,
      p, groupRows.length, pipeRows.length, missTableRows.length]);

  if (loading && !data) return <Loading what="your dashboard" />;
  if (error) return <>{switcher}<ErrorBox msg={error} /></>;

  const k = data.kpis;
  if (!data.persona || !k) {
    const who = viewAs.username || u.username || "";
    return (
      <>
        {switcher}
        <div className="banner warn">
          No data scope is mapped to {viewAs.username ? "this account" : "your account"}
          {who ? ` (${who})` : ""}. The CRM role-to-data mapping (market circle / collector /
          customer / segment) hasn’t been set up{viewAs.username ? "." : " — please contact your administrator."}
        </div>
      </>
    );
  }

  const syncedAt = data.last_sync?.finished_at ? String(data.last_sync.finished_at).slice(0, 16) : null;

  return (
    <>
      {switcher}
      <div style={{ opacity: loading ? 0.55 : 1, pointerEvents: loading ? "none" : "auto", transition: "opacity .2s" }}>
      <div className="card" style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10, padding: "12px 16px", marginBottom: 14 }}>
        <span className="chip" style={{ cursor: "default", background: "#EEF6FF", fontWeight: 600 }}>
          {data.persona}
        </span>
        {viewAs.username && data.user_name && (
          <span className="chip" style={{ cursor: "default", background: "#FFF3E8", fontWeight: 600 }}>
            {data.user_name.trim()}
          </span>
        )}
        <span style={{ fontSize: 13, color: "var(--muted)" }}>
          {(data.scope || []).join(" · ") || "—"}
        </span>
        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--muted)" }}>
          Last 13 JCs{syncedAt ? ` · data as of ${syncedAt}` : ""}
        </span>
        <SegTabs size="sm" value={metric} onChange={setMetric}
          tabs={[{ id: "qty", label: "KG" }, { id: "value", label: "₹ Value" }]} />
      </div>

      <div className="grid cols-4">
        <div className="card statcard"><div className="ic"><Truck size={22} /></div><Stat value={fmt.num(k.qty)} label="Dispatched (KG, 13 JCs)" /></div>
        <div className="card statcard amber"><div className="ic"><Wallet size={22} /></div><Stat value={`₹${abbr(k.value)}`} label="Dispatch value (13 JCs)" /></div>
        <div className="card statcard"><div className="ic"><Handshake size={22} /></div><Stat value={fmt.num(k.customers)} label="Customers served" /></div>
        <div className="card statcard"><div className="ic"><Package size={22} /></div><Stat value={fmt.num(k.items)} label="Items shipped" /></div>
      </div>

      {(sel.collector || sel.segment) && (
        <div className="pagebar" style={{ marginTop: 12, gap: 8 }}>
          <span style={{ fontSize: 12, color: "var(--muted)" }}>Filtered:</span>
          {sel.collector && (
            <button className="chip" onClick={() => setSel((s) => ({ ...s, collector: null }))}>
              {sel.collector} ✕
            </button>
          )}
          {sel.segment && (
            <button className="chip" onClick={() => setSel((s) => ({ ...s, segment: null }))}>
              {sel.segment} ✕
            </button>
          )}
        </div>
      )}

      <DashGrid storageKey={layoutKey} defaults={DASH_DEFAULTS}
        expanded={expandedCards}
        remoteLayouts={savedLayout.data?.layouts || null}
        userLayouts={savedLayout.data?.user_layouts || null}
        canSaveDefault={isAdmin}
        onSaveDefault={(l) => api.saveDashboardLayout("mydash", l)}
        onSaveUser={me ? (l) => api.saveDashboardLayout("mydash", l, me) : undefined}>
        {byColl.length > 1 && (
          <div key="byColl" className="card">
            <div className="supply-dash-cardhead">
              <div><h3>By collector{sel.segment ? ` · ${sel.segment}` : ""}</h3>
                <div className="sub">click a {shape.coll === "bar" ? "bar" : "slice"} to cross-filter</div></div>
              <SegTabs size="sm" value={shape.coll} onChange={setSh("coll")} tabs={SHAPE_DIST} />
            </div>
            <EChart className="echart-fill" option={collOpt} height="100%" onEvents={collEvents} />
          </div>
        )}

        {bySeg.length > 1 && (
          <div key="bySeg" className="card">
            <div className="supply-dash-cardhead">
              <div><h3>Product mix by segment{sel.collector ? ` · ${sel.collector}` : ""}</h3>
                <div className="sub">click a {shape.seg === "bar" ? "bar" : "slice"} to cross-filter</div></div>
              <SegTabs size="sm" value={shape.seg} onChange={setSh("seg")} tabs={SHAPE_DIST} />
            </div>
            <EChart className="echart-fill" option={segOpt} height="100%" onEvents={segEvents} />
          </div>
        )}

      {p && (
        <>
          <div key="projCanvas" className="card">
            <div className="supply-dash-cardhead">
              <div><h3 style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>{(() => { const M = PROJ_METRICS.find((m) => m.id === projMetric); const I = M?.icon; return <>{I && <I size={16} />} {M?.title}</>; })()}</h3>
                <div className="sub">
                  {projMetric === "accuracy" && <>100 − WMAPE over JC1–JC{lastDoneJc} · only the items that were projected</>}
                  {projMetric === "volume" && <>share of your 3-JC average sales that carries a JC{p.jc} projection</>}
                  {projMetric === "items" && <>selling items with vs without a JC{p.jc} projection</>}
                </div></div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <SegTabs size="sm" value={projMetric} onChange={setProjMetric}
                  tabs={PROJ_METRICS.map((m) => ({ id: m.id, label: m.label }))} />
                <SegTabs size="sm" value={projView} onChange={setProjView}
                  tabs={[{ id: "chart", label: "Chart" }, { id: "table", label: "Table" }]} />
              </div>
            </div>

            {projView === "chart" ? (
              <div className="echart-fill" style={{ width: "100%", maxWidth: 560, margin: "0 auto" }}>
                <EChart option={projMetric === "accuracy" ? accGaugeOpt
                  : projMetric === "volume" ? volRatioOpt : itemRatioOpt} height="100%" />
              </div>
            ) : projMetric === "accuracy" ? (
              <div className="tbl-wrap">
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <thead>
                    <tr>
                      <th style={{ ...HCELL, textAlign: "left" }}>Job cycle</th>
                      <th style={{ ...HCELL, textAlign: "left" }}>Period</th>
                      <th style={{ ...HCELL, textAlign: "right" }}>Projected (KG)</th>
                      <th style={{ ...HCELL, textAlign: "right" }}>Actual sales (KG)</th>
                      <th style={{ ...HCELL, textAlign: "right" }}>Items projected</th>
                      <th style={{ ...HCELL, textAlign: "right" }}>Accuracy (projected)</th>
                      <th style={{ ...HCELL, textAlign: "right" }}>Accuracy (all items)</th>
                      <th style={{ ...HCELL, textAlign: "right" }}>Volume projected</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(p.jc_trend || []).map((t, i) => (
                      <tr key={i}>
                        <td style={{ ...CELL, fontWeight: 600, color: "#1f3a5f" }}>
                          {t.label}{!t.done && <span style={{ fontSize: 10.5, color: "var(--muted)",
                            fontWeight: 400 }}> · planning</span>}
                        </td>
                        <td style={{ ...CELL, fontSize: 11.5, color: "var(--muted)", whiteSpace: "nowrap" }}>
                          {t.from ? `${t.from} → ${t.to}` : "—"}
                        </td>
                        <td style={{ ...CELL, textAlign: "right", fontWeight: 600 }}>{fmt.num(t.proj)}</td>
                        <td style={{ ...CELL, textAlign: "right" }}>
                          {t.actual == null ? <span style={{ color: "var(--muted)" }}>not dispatched yet</span>
                            : fmt.num(t.actual)}
                        </td>
                        <td style={{ ...CELL, textAlign: "right" }}>{fmt.num(t.items_projected)}</td>
                        <td style={{ ...CELL, textAlign: "right", fontWeight: 600,
                          color: t.accuracy_proj == null ? "var(--muted)" : accColor(t.accuracy_proj) }}>
                          {t.accuracy_proj == null ? "—" : `${t.accuracy_proj}%`}
                        </td>
                        <td style={{ ...CELL, textAlign: "right", color: "var(--muted)" }}>
                          {t.accuracy == null ? "—" : `${t.accuracy}%`}
                        </td>
                        <td style={{ ...CELL, textAlign: "right" }}>
                          {t.coverage_pct == null ? "—" : `${t.coverage_pct}%`}
                        </td>
                      </tr>
                    ))}
                    <tr>
                      <td style={{ ...CELL, background: "#f7fafc", fontWeight: 700 }} colSpan={5}>
                        Overall · completed cycles
                      </td>
                      <td style={{ ...CELL, background: "#f7fafc", textAlign: "right", fontWeight: 700,
                        color: accColor(p.overall_accuracy_proj) }}>
                        {p.overall_accuracy_proj == null ? "—" : `${p.overall_accuracy_proj}%`}
                      </td>
                      <td style={{ ...CELL, background: "#f7fafc", textAlign: "right", fontWeight: 700,
                        color: "var(--muted)" }}>
                        {p.overall_accuracy == null ? "—" : `${p.overall_accuracy}%`}
                      </td>
                      <td style={{ ...CELL, background: "#f7fafc", textAlign: "right", fontWeight: 700 }}>
                        {p.coverage_pct}%
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            ) : (
              <>
                <div className="pagebar" style={{ marginBottom: 10 }}>
                  <SegTabs size="sm" value={groupLevel} onChange={setGroupLevel}
                    tabs={[{ id: "segment3", label: "Segment 3" }, { id: "segment2", label: "Segment 2" }]} />
                  <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--muted)" }}>
                    broken down by item group
                  </span>
                </div>
                <div className="tbl-wrap">
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                    <thead>
                      {projMetric === "volume" ? (
                        <tr>
                          <th style={{ ...HCELL, textAlign: "left" }}>Item group</th>
                          <th style={{ ...HCELL, textAlign: "right" }}>3-JC avg sales (KG)</th>
                          <th style={{ ...HCELL, textAlign: "right" }}>Projected (KG)</th>
                          <th style={{ ...HCELL, textAlign: "right" }}>Sales with a projection</th>
                          <th style={{ ...HCELL, textAlign: "right" }}>Sales with none</th>
                          <th style={{ ...HCELL, textAlign: "right" }}>Volume projected</th>
                        </tr>
                      ) : (
                        <tr>
                          <th style={{ ...HCELL, textAlign: "left" }}>Item group</th>
                          <th style={{ ...HCELL, textAlign: "right" }}>Items</th>
                          <th style={{ ...HCELL, textAlign: "right" }}>With a projection</th>
                          <th style={{ ...HCELL, textAlign: "right" }}>No projection</th>
                          <th style={{ ...HCELL, textAlign: "right" }}>Unprojected sales (KG)</th>
                          <th style={{ ...HCELL, textAlign: "right" }}>% items missing</th>
                        </tr>
                      )}
                    </thead>
                    <tbody>
                      {groupRows.map((g, i) => {
                        const covPct = g.avg3 ? (g.covered_kg / g.avg3) * 100 : null;
                        const missPct = g.items ? (g.missing / g.items) * 100 : null;
                        return (
                          <tr key={i}>
                            <td style={{ ...CELL, fontWeight: 600, color: "#1f3a5f" }}>{g.name}</td>
                            {projMetric === "volume" ? (
                              <>
                                <td style={{ ...CELL, textAlign: "right" }}>{fmt.num(g.avg3)}</td>
                                <td style={{ ...CELL, textAlign: "right", fontWeight: 600 }}>{fmt.num(g.proj)}</td>
                                <td style={{ ...CELL, textAlign: "right", color: "#2a9d8f", fontWeight: 600 }}>
                                  {fmt.num(g.covered_kg)}
                                </td>
                                <td style={{ ...CELL, textAlign: "right",
                                  color: g.uncovered_kg ? "#c53030" : "var(--muted)", fontWeight: g.uncovered_kg ? 600 : 400 }}>
                                  {fmt.num(g.uncovered_kg)}
                                </td>
                                <td style={{ ...CELL, textAlign: "right", fontWeight: 600 }}>
                                  {covPct == null ? "—" : `${covPct.toFixed(1)}%`}
                                </td>
                              </>
                            ) : (
                              <>
                                <td style={{ ...CELL, textAlign: "right" }}>{fmt.num(g.items)}</td>
                                <td style={{ ...CELL, textAlign: "right", color: "#4880ff", fontWeight: 600 }}>
                                  {fmt.num(g.items - g.missing)}
                                </td>
                                <td style={{ ...CELL, textAlign: "right",
                                  color: g.missing ? "#c53030" : "var(--muted)", fontWeight: g.missing ? 600 : 400 }}>
                                  {fmt.num(g.missing)}
                                </td>
                                <td style={{ ...CELL, textAlign: "right" }}>{fmt.num(g.uncovered_kg)}</td>
                                <td style={{ ...CELL, textAlign: "right", fontWeight: 600,
                                  color: missPct && missPct > 50 ? "#c53030" : "inherit" }}>
                                  {missPct == null ? "—" : `${missPct.toFixed(0)}%`}
                                </td>
                              </>
                            )}
                          </tr>
                        );
                      })}
                      {groupRows.length === 0 && (
                        <tr><td colSpan={6} style={CELL}>No item groups in scope.</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
            {p.jc_trend.length > 0 && (
              <div key="jcQty" className="card">
                <div className="supply-dash-cardhead">
                  <div><h3 style={{ display: "inline-flex", alignItems: "center", gap: 7 }}><Package size={16} /> Total projection qty by JC</h3>
                    <div className="sub">projected KG per job cycle vs actual sales · {p.acc_year} · the pale bar is the planning JC{p.jc}</div></div>
                </div>
                <EChart className="echart-fill" option={jcQtyOpt} height="100%" />
              </div>
            )}

            {p.jc_trend.length > 0 && (
              <div key="jcAcc" className="card">
                <div className="supply-dash-cardhead">
                  <div><h3 style={{ display: "inline-flex", alignItems: "center", gap: 7 }}><Target size={16} /> Projection accuracy by JC</h3>
                    <div className="sub">100 − WMAPE per item · overall <b>{p.overall_accuracy_proj == null ? "—" : `${p.overall_accuracy_proj}%`}</b> on projected items</div></div>
                </div>
                <EChart className="echart-fill" option={jcAccOpt} height="100%" />
              </div>
            )}

            {p.jc_trend.length > 0 && (
              <div key="jcItems" className="card">
                <div className="supply-dash-cardhead">
                  <div><h3 style={{ display: "inline-flex", alignItems: "center", gap: 7 }}><ClipboardList size={16} /> Items projected · every JC</h3>
                    <div className="sub">items carrying a projection each cycle vs items that actually sold · the pale bar is the planning JC (not dispatched yet)</div></div>
                </div>
                <EChart className="echart-fill" option={jcItemsOpt} height="100%" />
              </div>
            )}

            <div key="status" className="card">
              <div className="supply-dash-cardhead">
                <div><h3>Projection status</h3>
                  <div className="sub">items by flag · same ±20% band as the RM plan</div></div>
                <SegTabs size="sm" value={shape.proj} onChange={setSh("proj")} tabs={SHAPE_DIST} />
              </div>
              <EChart className="echart-fill" option={statusOpt} height="100%" />
            </div>

            <div key="group" className="card">
              <div className="supply-dash-cardhead">
                <div><h3 style={{ display: "inline-flex", alignItems: "center", gap: 7 }}><Dna size={16} /> Projection by item group</h3>
                  <div className="sub">JC{p.jc} projected KG per {groupLevel === "segment2" ? "division (Segment 2)" : "product group (Segment 3)"} · sales, accuracy and gaps in the tooltip</div></div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <SegTabs size="sm" value={groupLevel} onChange={setGroupLevel}
                    tabs={[{ id: "segment3", label: "Segment 3" }, { id: "segment2", label: "Segment 2" }]} />
                  <SegTabs size="sm" value={groupView} onChange={setGroupView}
                    tabs={[{ id: "chart", label: "Chart" }, { id: "table", label: "Table" }]} />
                </div>
              </div>
              {groupView === "chart" ? (
                <EChart className="echart-fill" option={groupOpt} height="100%" />
              ) : (
                <div className="tbl-wrap">
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <thead>
                    <tr>
                      <th style={{ ...HCELL, textAlign: "left" }}>Item group</th>
                      <th style={{ ...HCELL, textAlign: "right" }}>Projected (KG)</th>
                      <th style={{ ...HCELL, textAlign: "right" }}>3-JC avg sales (KG)</th>
                      <th style={{ ...HCELL, textAlign: "right" }}>Items</th>
                      <th style={{ ...HCELL, textAlign: "right" }}>No projection</th>
                      <th style={{ ...HCELL, textAlign: "right" }}>Accuracy</th>
                    </tr>
                  </thead>
                  <tbody>
                    {groupRows.map((g, i) => (
                      <tr key={i}>
                        <td style={{ ...CELL, fontWeight: 600, color: "#1f3a5f" }}>{g.name}</td>
                        <td style={{ ...CELL, textAlign: "right", fontWeight: 600 }}>{fmt.num(g.proj)}</td>
                        <td style={{ ...CELL, textAlign: "right" }}>{fmt.num(g.avg3)}</td>
                        <td style={{ ...CELL, textAlign: "right", color: "var(--muted)" }}>{fmt.num(g.items)}</td>
                        <td style={{ ...CELL, textAlign: "right",
                          color: g.missing ? "#c53030" : "var(--muted)", fontWeight: g.missing ? 600 : 400 }}>
                          {fmt.num(g.missing)}
                        </td>
                        <td style={{ ...CELL, textAlign: "right", fontWeight: 600 }}>
                          {g.accuracy_proj == null ? "—" : `${g.accuracy_proj}%`}
                        </td>
                      </tr>
                    ))}
                    {groupRows.length === 0 && <tr><td colSpan={6} style={CELL}>No item groups in scope.</td></tr>}
                  </tbody>
                </table>
                </div>
              )}
            </div>

            <div key="compare" className="card">
              <div className="supply-dash-cardhead">
                <div><h3 style={{ display: "inline-flex", alignItems: "center", gap: 7 }}><Target size={16} /> Projection vs 3-JC avg sales</h3>
                  <div className="sub">
                    top items by sales · JC{p.jc} {p.acc_year} projection
                    ({p.basis === "collector" ? "your collectors" : "per item, company-wide"}) vs 3-JC average sales ·
                    ±20% band · <b>{p.coverage_pct}%</b> of your sales volume has a projection
                  </div></div>
              </div>
              <ProjCompareTable rows={p.compare || []}
                onItem={(r) => setItemPop({ name: r.name, code: r.code })} />
            </div>

            <div key="pipeline" className="card">
              <div className="supply-dash-cardhead">
                <div><h3 style={{ display: "inline-flex", alignItems: "center", gap: 7 }}><CalendarDays size={16} /> Projection pipeline</h3>
                  <div className="sub">projected KG for the current and the next two job cycles · your scope</div></div>
                <SegTabs size="sm" value={pipeView} onChange={setPipeView}
                  tabs={[{ id: "chart", label: "Chart" }, { id: "table", label: "Table" }]} />
              </div>
              {pipeView === "chart" ? (
                <EChart className="echart-fill" option={pipeOpt} height="100%" />
              ) : (
                <>
                  <div className="pagebar" style={{ marginBottom: 10 }}>
                    <SmoothInput className="searchbox" placeholder="Search item code / name…"
                      value={pipeQ} onChange={(e) => setPipeQ(e.target.value)} />
                    <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--muted)" }}>
                      {pipeRows.length} of {(p.pipeline_rows || []).length} products
                      {(p.pipeline_rows || []).length >= 200 ? " (top 200 by projected volume)" : ""}
                    </span>
                  </div>
                  <div className="tbl-wrap">
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                      <thead>
                        <tr>
                          <th style={{ ...HCELL, textAlign: "left" }}>Item Code</th>
                          <th style={{ ...HCELL, textAlign: "left" }}>Item Name</th>
                          <th style={{ ...HCELL, textAlign: "right" }}>3-JC avg sales</th>
                          <th style={{ ...HCELL, textAlign: "right" }}>Current · JC{p.jc}</th>
                          <th style={{ ...HCELL, textAlign: "right" }}>Next JC</th>
                          <th style={{ ...HCELL, textAlign: "right" }}>JC after next</th>
                          <th style={{ ...HCELL, textAlign: "center" }}>Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {pipeRows.map((r, i) => (
                          <tr key={i} onClick={() => setItemPop({ name: r.name, code: r.code })}
                            style={{ cursor: "pointer" }} title="Click to see this item's JC-wise graph">
                            <td style={{ ...CELL, fontSize: 12, whiteSpace: "nowrap" }}>{r.code || "—"}</td>
                            <td title={r.name} style={{ ...CELL, maxWidth: 340, overflow: "hidden",
                              textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.name}</td>
                            <td style={{ ...CELL, textAlign: "right" }}>{fmt.num(r.avg3)}</td>
                            <td style={{ ...CELL, textAlign: "right", fontWeight: 600 }}>{fmt.num(r.proj)}</td>
                            <td style={{ ...CELL, textAlign: "right" }}>{fmt.num(r.next1)}</td>
                            <td style={{ ...CELL, textAlign: "right" }}>{fmt.num(r.next2)}</td>
                            <td style={{ ...CELL, textAlign: "center", whiteSpace: "nowrap" }}>
                              <span style={{ color: (FLAGS[r.flag] || {}).color, fontSize: 12, fontWeight: 600 }}>
                                ● {(FLAGS[r.flag] || {}).label || r.flag}
                              </span>
                            </td>
                          </tr>
                        ))}
                        {pipeRows.length === 0 && (
                          <tr><td colSpan={7} style={CELL}>No products match the search.</td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </div>

            <div key="missing" className="card"
              style={p.missing_total > 0 ? { background: "#FFFBFA" } : undefined}>
              <div className="supply-dash-cardhead">
                <div><h3 style={{ display: "inline-flex", alignItems: "center", gap: 7 }}><TriangleAlert size={16} /> Missing projections</h3>
                  <div className="sub">
                    selling items with <b>no JC{p.jc} projection</b>
                    {p.missing_total > 0 && (
                      <span style={{ color: "#c53030", fontWeight: 600 }}>
                        {" "}· {p.missing_total} item{p.missing_total > 1 ? "s" : ""} · {abbr(p.missing_kg)} KG/JC unprojected
                      </span>
                    )}
                  </div></div>
                {p.missing_total > 0 && (
                  <SegTabs size="sm" value={missView} onChange={setMissView}
                    tabs={[{ id: "chart", label: "Chart" }, { id: "table", label: "Table" }]} />
                )}
              </div>
              {p.missing_total === 0 ? (
                <div style={{ padding: "48px 0", textAlign: "center", color: "#2f855a", fontSize: 13, display: "flex", alignItems: "center", justifyContent: "center", gap: 7 }}>
                  <CircleCheck size={16} /> Every selling item in your scope has a projection for JC{p.jc}.
                </div>
              ) : missView === "chart" ? (
                <>
                  <EChart className="echart-fill" option={missOpt} height="100%" />
                  {(p.missing_all || []).length > missChart.length && (
                    <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 6 }}>
                      Showing the {missChart.length} biggest — switch to <b>Table</b> for all {p.missing_total}.
                    </div>
                  )}
                </>
              ) : (
                <>
                  <div className="pagebar" style={{ marginBottom: 10 }}>
                    <SmoothInput className="searchbox" placeholder="Search item code / name…"
                      value={missQ} onChange={(e) => setMissQ(e.target.value)} />
                    <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--muted)" }}>
                      {missTableRows.length} of {(p.missing_all || []).length} items
                      {p.missing_total > (p.missing_all || []).length
                        ? ` (top ${(p.missing_all || []).length} of ${p.missing_total} by volume)` : ""}
                    </span>
                  </div>
                  <div className="tbl-wrap">
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                      <thead>
                        <tr>
                          <th style={{ ...HCELL, textAlign: "left", width: 60 }}>#</th>
                          <th style={{ ...HCELL, textAlign: "left" }}>Item Code</th>
                          <th style={{ ...HCELL, textAlign: "left" }}>Item Name</th>
                          <th style={{ ...HCELL, textAlign: "right" }}>3-JC avg sales (KG)</th>
                          <th style={{ ...HCELL, textAlign: "right" }}>Share of gap</th>
                        </tr>
                      </thead>
                      <tbody>
                        {missTableRows.map((m, i) => (
                          <tr key={i} onClick={() => setItemPop({ name: m.name, code: m.code })}
                            style={{ cursor: "pointer" }} title="Click to see this item's JC-wise graph">
                            <td style={{ ...CELL, color: "var(--muted)" }}>{m.rank}</td>
                            <td style={{ ...CELL, fontSize: 12, whiteSpace: "nowrap" }}>{m.code || "—"}</td>
                            <td title={m.name} style={{ ...CELL, maxWidth: 380, overflow: "hidden",
                              textOverflow: "ellipsis", whiteSpace: "nowrap", fontWeight: 600, color: "#1f3a5f" }}>
                              {m.name}
                            </td>
                            <td style={{ ...CELL, textAlign: "right", fontWeight: 600 }}>{fmt.num(m.avg3)}</td>
                            <td style={{ ...CELL, textAlign: "right", color: "#c53030", fontWeight: 600 }}>
                              {p.missing_kg ? `${((m.avg3 / p.missing_kg) * 100).toFixed(1)}%` : "—"}
                            </td>
                          </tr>
                        ))}
                        {missTableRows.length === 0 && (
                          <tr><td colSpan={5} style={CELL}>No items match the search.</td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </div>
        </>
      )}
      </DashGrid>
      </div>

      <ItemGraphModal target={itemPop} idParams={idParams} onClose={() => setItemPop(null)} />
    </>
  );
}

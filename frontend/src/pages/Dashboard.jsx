import React, { useMemo, useState, useEffect } from "react";
import EChart from "../components/EChart.jsx";
import SegTabs from "../components/SegTabs.jsx";
import SelectBox from "../components/SelectBox.jsx";
import { api, fmt } from "../api";
import { useAsync, Loading, ErrorBox, Stat } from "../components/ui.jsx";

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
const PAL = ["#2a9d8f", "#4880ff", "#b7791f", "#805ad5", "#2f855a", "#c53030", "#28b5e1", "#90a1ac",
  "#d69e2e", "#3182ce", "#38a169", "#e53e3e", "#718096"];
const SHAPE_DIST = [{ id: "donut", label: "Donut" }, { id: "pie", label: "Pie" }, { id: "bar", label: "Bar" }];
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

// bullet-style comparison: one bar per item = the scoped 3-JC avg sales,
// coloured by its accuracy flag; a navy ◆ marks the plan-table projection.
// One glance answers "how much do I sell, what did I project, am I close?"
function bulletOption(rows) {
  const rev = [...rows].reverse();
  const tip = (i) => {
    const r = rev[i];
    const f = FLAGS[r.flag] || FLAGS.ontrack;
    const acc = r.avg3 > 0 && r.proj > 0 ? ` (${Math.round((r.proj / r.avg3) * 100)}% of avg sales)` : "";
    return `<b>${r.name}</b><br/>3-JC avg sales: <b>${fmt.num(r.avg3)}</b> KG` +
      `<br/>Projection: <b>${fmt.num(r.proj)}</b> KG${acc}` +
      `<br/><span style="color:${f.color}">● ${f.label}</span>`;
  };
  return {
    ...ANIM, grid: { left: 8, right: 30, top: 34, bottom: 8, containLabel: true },
    legend: { top: 0, textStyle: { color: "#414d55", fontSize: 11 },
      data: [{ name: "3-JC avg sales", icon: "roundRect" }, { name: "Projection", icon: "diamond" }] },
    tooltip: { ...TT, trigger: "axis", axisPointer: { type: "shadow" },
      formatter: (ps) => tip(ps[0].dataIndex) },
    xAxis: { type: "value", splitLine: { lineStyle: { color: "#eef1f5" } },
      axisLabel: { color: "#90a1ac", fontSize: 11, formatter: abbr, hideOverlap: true },
      axisLine: { show: false }, axisTick: { show: false } },
    yAxis: { type: "category", data: rev.map((r) => r.name),
      axisLabel: { color: "#414d55", fontSize: 11, width: 170, overflow: "truncate", hideOverlap: true },
      axisTick: { show: false }, axisLine: { show: false } },
    series: [
      { name: "3-JC avg sales", type: "bar", barWidth: "52%", color: "#94a8bc", z: 1,
        data: rev.map((r) => ({ value: r.avg3,
          itemStyle: { color: (FLAGS[r.flag] || {}).color + "CC", borderRadius: [0, 6, 6, 0] } })) },
      { name: "Projection", type: "scatter", symbol: "diamond", symbolSize: 13, z: 3,
        itemStyle: { color: "#1f3a5f", borderColor: "#fff", borderWidth: 1.5 },
        data: rev.map((r) => r.proj) },
    ],
  };
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
      <b style={{ fontSize: 13 }}>👁 View as</b>
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
  const bulletOpt = useMemo(() => bulletOption(p?.compare || []), [p]);
  const pipeOpt = useMemo(() => pipeOption(p?.pipeline || []), [p]);
  const statusRows = useMemo(() => (p?.summary || []).map((s) => ({
    name: FLAGS[s.flag]?.label || s.flag, value: s.items, color: FLAGS[s.flag]?.color })), [p]);
  const statusOpt = useMemo(() => distOption(statusRows, { shape: shape.proj, unit: "items", center: "items" }),
    [statusRows, shape.proj]);
  const missRows = useMemo(() => (p?.missing || []).map((m) => ({
    name: m.name, value: m.avg3, color: "#c53030" })), [p]);
  const missOpt = useMemo(() => distOption(missRows, { shape: "bar", unit: "KG avg / JC" }), [missRows]);

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
        <div className="card statcard"><div className="ic">🚚</div><Stat value={fmt.num(k.qty)} label="Dispatched (KG, 13 JCs)" /></div>
        <div className="card statcard amber"><div className="ic">💰</div><Stat value={`₹${abbr(k.value)}`} label="Dispatch value (13 JCs)" /></div>
        <div className="card statcard"><div className="ic">🤝</div><Stat value={fmt.num(k.customers)} label="Customers served" /></div>
        <div className="card statcard"><div className="ic">📦</div><Stat value={fmt.num(k.items)} label="Items shipped" /></div>
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

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        {byColl.length > 1 && (
          <div className="card">
            <div className="supply-dash-cardhead">
              <div><h3>By collector{sel.segment ? ` · ${sel.segment}` : ""}</h3>
                <div className="sub">click a {shape.coll === "bar" ? "bar" : "slice"} to cross-filter</div></div>
              <SegTabs size="sm" value={shape.coll} onChange={setSh("coll")} tabs={SHAPE_DIST} />
            </div>
            <EChart option={collOpt} height={260} onEvents={collEvents} />
          </div>
        )}

        {bySeg.length > 1 && (
          <div className="card">
            <div className="supply-dash-cardhead">
              <div><h3>Product mix by segment{sel.collector ? ` · ${sel.collector}` : ""}</h3>
                <div className="sub">click a {shape.seg === "bar" ? "bar" : "slice"} to cross-filter</div></div>
              <SegTabs size="sm" value={shape.seg} onChange={setSh("seg")} tabs={SHAPE_DIST} />
            </div>
            <EChart option={segOpt} height={260} onEvents={segEvents} />
          </div>
        )}

      </div>

      {p && (
        <div className="grid cols-2" style={{ marginTop: 14 }}>
          <div className="card" style={{ gridColumn: "1 / -1" }}>
            <div className="supply-dash-cardhead">
              <div><h3>🎯 Projection vs 3-JC avg sales</h3>
                <div className="sub">
                  bar = your 3-JC average sales (coloured by status) · <b style={{ color: "#1f3a5f" }}>◆</b> = JC{p.jc} {p.acc_year} projection
                  ({p.basis === "collector" ? "your collectors" : "per item, company-wide"}) ·
                  ±20% band · <b>{p.coverage_pct}%</b> of your sales volume has a projection
                </div></div>
            </div>
            <EChart option={bulletOpt} height={330} />
          </div>

          <div className="card">
            <div className="supply-dash-cardhead">
              <div><h3>📅 Projection pipeline</h3>
                <div className="sub">projected KG for the current and the next two job cycles · your scope</div></div>
            </div>
            <EChart option={pipeOpt} height={260} />
          </div>

          <div className="card">
            <div className="supply-dash-cardhead">
              <div><h3>Projection status</h3>
                <div className="sub">items by flag · same ±20% band as the RM plan</div></div>
              <SegTabs size="sm" value={shape.proj} onChange={setSh("proj")} tabs={SHAPE_DIST} />
            </div>
            <EChart option={statusOpt} height={260} />
          </div>

          <div className="card" style={{ gridColumn: "1 / -1",
            ...(p.missing_total > 0 ? { borderColor: "#f0b9b9", background: "#FFFBFA" } : {}) }}>
            <div className="supply-dash-cardhead">
              <div><h3>⚠️ Missing projections</h3>
                <div className="sub">
                  high-selling items with <b>no JC{p.jc} projection</b>
                  {p.missing_total > 0 && (
                    <span style={{ color: "#c53030", fontWeight: 600 }}>
                      {" "}· {p.missing_total} item{p.missing_total > 1 ? "s" : ""} · {abbr(p.missing_kg)} KG/JC unprojected
                    </span>
                  )}
                </div></div>
            </div>
            {missRows.length > 0 ? (
              <EChart option={missOpt} height={240} />
            ) : (
              <div style={{ padding: "48px 0", textAlign: "center", color: "#2f855a", fontSize: 13 }}>
                ✅ Every high-selling item in your scope has a projection for JC{p.jc}.
              </div>
            )}
          </div>
        </div>
      )}
      </div>
    </>
  );
}

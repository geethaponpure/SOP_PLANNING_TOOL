import React, { useState } from "react";
import { api, fmt } from "../api";
import { useAsync, Loading, ErrorBox, Tag, Stat } from "../components/ui.jsx";

// A clean segmented-control tab strip (replaces the plain underlined link tabs).
function SegTabs({ tabs, value, onChange, size = "md" }) {
  return (
    <div style={{ display: "inline-flex", background: "#eef2f7", border: "1px solid var(--border)",
      borderRadius: 10, padding: 3, gap: 2 }}>
      {tabs.map((t) => {
        const active = value === t.id;
        return (
          <button key={t.id} onClick={() => onChange(t.id)} title={t.title || ""}
            style={{ border: "none", cursor: "pointer", borderRadius: 7, whiteSpace: "nowrap",
              padding: size === "sm" ? "5px 12px" : "7px 15px", fontSize: 13,
              fontWeight: active ? 700 : 500, background: active ? "#fff" : "transparent",
              color: active ? "var(--navy)" : "var(--muted)",
              boxShadow: active ? "0 1px 3px rgba(15,23,42,.14)" : "none", transition: "all .12s" }}>
            {t.icon ? t.icon + " " : ""}{t.label}
          </button>
        );
      })}
    </div>
  );
}

function TemplateBar() {
  const { data } = useAsync(api.templateSegments);
  const [seg2, setSeg2] = useState("");
  const [seg3, setSeg3] = useState("");
  const [busy, setBusy] = useState(false);
  const segments = data?.segments || [];
  const seg3opts = segments.find((x) => x.segment2 === seg2)?.segment3 || [];
  return (
    <div className="card supply-tool-card" style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", marginBottom: 14 }}>
      <span><b>📄 Plan-input template</b> <span style={{ fontSize: 12, color: "var(--muted)" }}>· Segment 1 = Performance Chemicals</span></span>
      <select className="searchbox" style={{ maxWidth: 210 }} value={seg2} onChange={(e) => { setSeg2(e.target.value); setSeg3(""); }}>
        <option value="">All Segment 2</option>
        {segments.map((x) => <option key={x.segment2} value={x.segment2}>{x.segment2}</option>)}
      </select>
      <select className="searchbox" style={{ maxWidth: 210 }} value={seg3} onChange={(e) => setSeg3(e.target.value)} disabled={!seg2}>
        <option value="">All Segment 3</option>
        {seg3opts.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
      <button className="btn" style={{ marginLeft: "auto" }} disabled={busy}
        title="Excel template (S.No, Item Description [dropdown], Qty, Current JC, Next JC1, Next JC2)"
        onClick={async () => { setBusy(true); try { await api.templateDownload(seg2, seg3); } catch (e) { alert(e.message); } finally { setBusy(false); } }}>
        {busy ? "Preparing…" : "⤓ Download Template"}
      </button>
    </div>
  );
}

function UploadBar({ onPlan, active, onClear }) {
  const [file, setFile] = useState(null);
  const [mode, setMode] = useState("consolidate");
  const [busy, setBusy] = useState(false);
  const go = async () => {
    if (mode !== "crm" && !file) { alert("Choose a filled template (.xlsx) first."); return; }
    if (mode === "excel_only" && !window.confirm("Plan using ONLY the uploaded Excel — ignoring CRM projection and pending SOC?\n\nProceed with Excel-only planning?")) return;
    setBusy(true);
    try {
      const r = await api.uploadPlan(mode === "crm" ? null : file, mode);
      onPlan(r);
      const src = mode === "crm" ? "Projection + Pending SOC" : `${r.plan_mode}, ${r.excel_items} Excel items`;
      alert(`✓ Plan #${r.plan_id ?? "—"} generated (${src})` + (r.mysql_ok ? " and saved to DB." : " — DB save failed: " + (r.mysql_error || "")));
    } catch (e) { alert(e.message); } finally { setBusy(false); }
  };
  const R = (val, label) => (
    <label style={{ display: "flex", alignItems: "center", gap: 5, margin: 0, fontSize: 13 }}>
      <input type="radio" name="pmode" style={{ width: "auto" }} checked={mode === val} onChange={() => setMode(val)} />
      {label}
    </label>
  );
  return (
    <div className="card supply-tool-card" style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", marginBottom: 14 }}>
      <span><b>⬆️ Generate plan</b></span>
      <input type="file" accept=".xlsx" disabled={mode === "crm"} onChange={(e) => setFile(e.target.files[0])}
        style={{ fontSize: 13, opacity: mode === "crm" ? 0.5 : 1 }} />
      {R("crm", "Projection + Pending SOC (no Excel)")}
      {R("consolidate", "Consolidate (Excel + Projection + Pending SOC)")}
      {R("excel_only", "Excel only")}
      <button className="btn" style={{ marginLeft: "auto" }} disabled={busy} onClick={go}>{busy ? "Planning…" : "▶ Generate Plan"}</button>
      {active && <button className="btn secondary" onClick={onClear}>↺ Back to CRM plan</button>}
    </div>
  );
}

export default function Supply() {
  return <RMPlanning />;
}

const NetCell = ({ v }) => <span className={v > 0 ? "num-pos" : "num-zero"}>{fmt.num(v)}</span>;

const SalesFlag = ({ f }) => {
  if (!f || f === "none") return <span style={{ color: "var(--muted)" }}>—</span>;
  const map = {
    over: { t: "Over", bg: "#FFE5E5", c: "#a11" },
    under: { t: "Under", bg: "#FFF4DA", c: "#8a6d00" },
    ontrack: { t: "On track", bg: "#E6F6EC", c: "#1a7" },
    new: { t: "New", bg: "#EEF0FF", c: "#55a" },
  };
  const m = map[f] || { t: f, bg: "#eee", c: "#666" };
  return <span className="chip" style={{ cursor: "default", background: m.bg, color: m.c, borderColor: m.bg }}>{m.t}</span>;
};

function RMPlanning() {
  const { data: fetched, loading, error } = useAsync(api.rmPlanning);
  const [uploaded, setUploaded] = useState(null);
  const data = uploaded ? uploaded.plan : fetched;
  const [open, setOpen] = useState(null);
  const [more, setMore] = useState(null);
  const [sel, setSel] = useState({});      // product index -> chosen BOM index
  const [q, setQ] = useState("");
  const [cls, setCls] = useState("");   // "" | manufacturing | repack_relabel | unclassified | none
  const [rmCls, setRmCls] = useState("manufacturing");  // consolidated-RM activity split
  const [seg2, setSeg2] = useState("");
  const [seg3, setSeg3] = useState("");
  const [exporting, setExporting] = useState(false);
  const [segExporting, setSegExporting] = useState(false);
  const [packExporting, setPackExporting] = useState(false);
  const [savingPlan, setSavingPlan] = useState(false);
  const [applying, setApplying] = useState(false);
  const [mode, setMode] = useState("product");
  if (loading) return <Loading what="RM planning — reading stock / BOM / projection files (first load can take ~30–60s)" />;
  if (error) return <ErrorBox msg={error} />;
  if (data.note) return <div className="banner info">{data.note}</div>;

  const s = data.summary;
  const pjc = data.planning_jc || 4;
  const pick = (i, k) => setSel((m) => ({ ...m, [i]: k }));
  const seg2opts = [...new Set(data.products.map((p) => p.segment2).filter(Boolean))].sort();
  // Segment 3 is nested under Segment 2: when a Segment 2 is chosen, only show the
  // Segment 3 values that fall under it.
  const seg3opts = [...new Set(data.products
    .filter((p) => !seg2 || p.segment2 === seg2)
    .map((p) => p.segment3).filter(Boolean))].sort();
  const rows = data.products
    .map((p, i) => ({ p, i }))
    .filter(({ p }) => (!cls || p.bom_class === cls) && (!q || p.name.toLowerCase().includes(q.toLowerCase()))
      && (!seg2 || p.segment2 === seg2) && (!seg3 || p.segment3 === seg3));

  // BOM overrides chosen in the UI: {product name -> "assembly|org|designator"}
  const bomOverrides = {};
  Object.entries(sel).forEach(([i, k]) => {
    const p = data.products[+i];
    const b = p && p.boms && p.boms[k];
    if (b && !b.preferred) bomOverrides[p.name] = `${b.assembly_item}|${b.org_code}|${b.designator}`;
  });
  const overrideCount = Object.keys(bomOverrides).length;
  const applyOverrides = async () => {
    if (!window.confirm(`Apply ${overrideCount} BOM override(s)?\n\nThe plan will rebuild with your chosen BOMs and save — this flows into the consolidated RM plan, Excel and Production Scheduling. (Rebuild can take ~2 minutes.)`)) return;
    setApplying(true);
    try {
      const r = await api.applyBomOverrides(bomOverrides);
      setUploaded(r); setSel({});
      alert(`✓ ${r.overrides_applied} BOM override(s) applied · plan #${r.plan_id ?? "—"} saved` + (r.mysql_ok ? "." : " — DB save failed: " + (r.mysql_error || "")));
    } catch (e) { alert(e.message); } finally { setApplying(false); }
  };

  return (
    <section className="supply-page">
      <div className="banner supply-context" style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "center", fontSize: 13 }}>
        <span>📅 <b>Planning JC{pjc}</b>{data.planning_jc_from ? ` · ${data.planning_jc_from} → ${data.planning_jc_to}` : ""}</span>
        {data.soc_window && <span>🧾 <b>Pending SOC:</b> {data.soc_window.from <= "1900-01-01" ? "As on date" : data.soc_window.from} → {data.soc_window.to}</span>}
        {data.po_window && <span>🚚 <b>Pending PO dates:</b> {data.po_window.from} → {data.po_window.to}</span>}
      </div>
      <div className="banner info page-intro supply-method">
        <b>Planning Filtration Technique.</b> 3-JC projection → BOM by preference
        (<b>PMO → BULK/HDLK → newest BOM → Primary</b>) with main RM + substitutes → netted against filtered RM stock and PO pending.
        Click a row to expand; use <b>More</b> to override the BOM (RM requirement re-computes instantly).
        Packing BOMs are shown separately under each product for packing-material planning.
        <div style={{ marginTop: 6, fontSize: 12.5 }}>
          <b>JC{pjc} Qty = (JC{pjc} WK1 + WK2) + MFG SOC Pending.</b> The current-JC quantity is the sum of the approved
          projection's JC{pjc}&nbsp;week-1 and week-2 qty
          {data.planning_jc_from ? ` (JC${pjc}: ${data.planning_jc_from} → ${data.planning_jc_to})` : ""}. Stock is read live from CRM (<code>SPBiStockDetails</code>);
          <b>MFG SOC Pending</b> (un-despatched sale orders for the manufacturing/trading dispatch orgs, by <code>soc_date</code>) is added to the planning JC —
          <b>Overall SOC</b> (all orgs) is shown for reference only. <b>Mfg Req (Current)</b> = (JC{pjc} Qty + <b>MSL</b>) − on-hand FG (Warehouse + Branch); <b>Mfg Req (3 JC)</b> = (Total + MSL) − on-hand (Warehouse + Branch). The <b>MSL</b> safety-stock buffer applies only to valid items (moved in &gt;10 of 13 JCs AND &gt;5 unique customers).
          {" "}<b>Producible (RM)</b> = FG makeable from currently-available RM (bottleneck: limited by the scarcest component, only if all RMs are on hand).
          {data.soc_window && <> SOC window <b>{data.soc_window.from <= "1900-01-01" ? "As on date" : data.soc_window.from} → {data.soc_window.to}</b> (all open pending — “as on date” — up to the close of the JC before JC{pjc})</>}.
          {typeof s?.min_plan_qty === "number" && (
            <div style={{ marginTop: 4 }}>
              🔎 <b>Negligible-quantity filter:</b> an item is planned only if a projection JC quantity (any of JC{pjc}–JC{pjc + 2})
              {" "}<b>or</b> its Pending SOC is <b>more than {fmt.num(s.min_plan_qty)} KG</b>. Example: projection is 0 but SOC is 40 → still planned;
              {" "}if all three JC quantities and the SOC are ≤ {fmt.num(s.min_plan_qty)}, the item is dropped as negligible.
            </div>)}
          {data.intransit && (
            <div style={{ marginTop: 4 }}>
              🚚 <b>In-transit</b> = {data.intransit.source === "crm"
                ? <>live open-PO balance from CRM (<code>ordered − received − cancelled</code>), for POs placed since <b>{data.intransit.from}</b> (last {data.intransit.po_months} months) and ordered ≤ <b>{fmt.num(data.intransit.blanket_po_qty)} KG</b> (excludes blanket/framework contracts)</>
                : <>the PO_receipts register file (date-windowed snapshot)</>}. It's added to RM <b>Available</b> and reduces <b>Net to buy</b>.
            </div>)}
        </div>
      </div>
      {data.projection_jc_note && (
        <div className="banner supply-notice">
          ⚠️ <b>Projection roll-forward:</b> {data.projection_jc_note}
        </div>
      )}

      <div className="grid cols-4 supply-metrics">
        <div className="card statcard"><div className="ic">📦</div><Stat value={fmt.num(s.projected_products)} label="Projected products (3-JC ≠ 0)" /></div>
        <div className="card statcard blue" style={{ cursor: "pointer" }} onClick={() => { setMode("product"); setCls(cls === "manufacturing" ? "" : "manufacturing"); }}>
          <div className="ic">🏭</div><Stat value={fmt.num(s.manufacturing)} label="Manufacturing (make)" /></div>
        <div className="card statcard" style={{ cursor: "pointer" }} onClick={() => { setMode("product"); setCls(cls === "repack_relabel" ? "" : "repack_relabel"); }}>
          <div className="ic">🏷️</div><Stat value={fmt.num(s.repack_relabel)} label="Repack / Relabel" /></div>
        <div className="card statcard amber"><div className="ic">🚚</div><Stat value={fmt.num(s.po_pending_items)} label="Items with PO pending" /></div>
      </div>

      <TemplateBar />
      <UploadBar onPlan={setUploaded} active={!!uploaded} onClear={() => setUploaded(null)} />
      {uploaded && (
        <div className="banner supply-notice">
          {uploaded.plan_mode === "bom_override"
            ? <>📋 Showing <b>plan #{uploaded.plan_id ?? "—"}</b> with <b>{uploaded.overrides_applied} BOM override(s)</b> applied · saved to DB — flows into consolidated RM, Excel &amp; Production Scheduling. </>
            : <>📋 Showing <b>uploaded plan #{uploaded.plan_id ?? "—"}</b> ({uploaded.plan_mode === "excel_only" ? "Excel only" : "Consolidated: Excel + Projection + Pending SOC"}) · {uploaded.excel_items} Excel items · saved to DB. </>}
          <button className="link" onClick={() => setUploaded(null)}>↺ back to CRM plan</button>
        </div>
      )}

      <div className="supply-workspace" style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 10, margin: "16px 0 8px" }}>
        <SegTabs value={mode}
          onChange={(m) => { setMode(m); setQ(""); if (m !== "product") { setSeg2(""); setSeg3(""); } }}
          tabs={[
            { id: "product", label: "By product" },
            { id: "consolidated", label: "Consolidated RM purchase" },
            { id: "realrm", label: "Real RM (exploded)", title: "Intermediates exploded to their purchased (leaf) raw materials — the true buy-list" },
          ]} />
        <div style={{ display: "flex", gap: 8, marginLeft: "auto", flexWrap: "wrap", alignItems: "center" }}>
          {overrideCount > 0 && (
            <button className="btn" disabled={applying}
              title="Rebuild & save the plan using your chosen BOMs (flows into consolidated RM, Excel & Production Scheduling)"
              onClick={applyOverrides}>
              {applying ? "Applying… (~2 min)" : `✓ Apply ${overrideCount} BOM override${overrideCount > 1 ? "s" : ""}`}
            </button>
          )}
          <button className="btn secondary" disabled={savingPlan}
            title="Save this JC's RM plan (freezes RM allocation for adhoc planning)"
            onClick={async () => {
              setSavingPlan(true);
              try { const r = await api.saveJcPlan(); alert(`✓ JC Plan saved — Plan ID ${r.plan_id}. Adhoc planning can now deduct this plan's RM allocation.`); }
              catch (e) { alert("Save failed: " + e.message); } finally { setSavingPlan(false); }
            }}>
            {savingPlan ? "Saving…" : "💾 Save JC Plan"}
          </button>
          <button className="btn" disabled={exporting}
            onClick={async () => { setExporting(true); try { await (uploaded?.plan_id ? api.planExport(uploaded.plan_id) : api.rmPlanningExport()); } catch (e) { alert(e.message); } finally { setExporting(false); } }}>
            {exporting ? "Exporting…" : (uploaded ? "⤓ Download uploaded plan (Excel)" : "⤓ Download report (Excel)")}
          </button>
          <button className="btn secondary" disabled={segExporting}
            title="A ZIP with a separate Excel file per Segment 2 (each split Manufacturing / Others) to share with the Business Units — each file includes a Reference sheet (understanding note + organization matrix)"
            onClick={async () => { setSegExporting(true); try { await api.rmSegmentExport(uploaded?.plan_id || null); } catch (e) { alert(e.message); } finally { setSegExporting(false); } }}>
            {segExporting ? "Zipping…" : "⤓ Projection Confirmation to Share BU"}
          </button>
          <button className="btn secondary" disabled={packExporting}
            title="Separate workbook: Packing Material (consolidated) + Packing BOMs (per-FG packing components) — split out of the RM plan"
            onClick={async () => { setPackExporting(true); try { await api.packingExport(uploaded?.plan_id || null); } catch (e) { alert(e.message); } finally { setPackExporting(false); } }}>
            {packExporting ? "Exporting…" : "⤓ Packing plan (Excel)"}
          </button>
        </div>
      </div>

      <div className="pagebar supply-filters">
        <input className="searchbox" placeholder={mode === "product" ? "Search product…" : "Search RM code / name…"} value={q} onChange={(e) => setQ(e.target.value)} />
        {mode === "product" && (
          <select className="searchbox" style={{ maxWidth: 190 }} value={seg2} onChange={(e) => { setSeg2(e.target.value); setSeg3(""); }}>
            <option value="">All Segment 2</option>
            {seg2opts.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        )}
        {mode === "product" && (
          <select className="searchbox" style={{ maxWidth: 190 }} value={seg3} onChange={(e) => setSeg3(e.target.value)}>
            <option value="">All Segment 3</option>
            {seg3opts.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        )}
        {mode === "product" && (
          <select className="searchbox" style={{ maxWidth: 210 }} value={cls} onChange={(e) => setCls(e.target.value)}>
            <option value="">All activities</option>
            <option value="manufacturing">Manufacturing (make)</option>
            <option value="repack_relabel">Repack / Relabel</option>
            <option value="trading">Trading / Distribution</option>
            <option value="unclassified">Unclassified</option>
            <option value="internal">Internal (conv/decode)</option>
          </select>
        )}
        {mode === "consolidated" && (
          <select className="searchbox" style={{ maxWidth: 240 }} value={rmCls} onChange={(e) => setRmCls(e.target.value)}>
            <option value="manufacturing">Manufacturing RM</option>
            <option value="repack">Repack / Relabel RM</option>
            <option value="packing">Packing Material</option>
            <option value="all">RM — All (combined)</option>
          </select>
        )}
        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--muted)" }}>
          {mode === "product" ? `${rows.length} products` : (() => {
            const cs = mode === "realrm" ? (data.real_rm_summary || {})
              : rmCls === "manufacturing" ? data.consolidated_summary_manufacturing
                : rmCls === "repack" ? data.consolidated_summary_repack
                  : rmCls === "packing" ? data.consolidated_summary_packing : data.consolidated_summary;
            return `${cs.distinct_rms} materials · ${cs.rms_to_buy} to buy`;
          })()}
        </span>
      </div>

      {mode === "consolidated" && <ConsolidatedRM data={data} q={q} rmCls={rmCls} pjc={pjc} />}
      {mode === "realrm" && <RealRM data={data} q={q} pjc={pjc} />}
      {mode === "product" && (
       <div className="tbl-wrap supply-table">
        <table>
          <thead>
            <tr>
              <th style={{ width: 24 }}></th><th>Product / selected BOM</th>
              <th className="grp" colSpan={11}>Projection (KG) — 3-JC · JC{pjc} Qty = (JC{pjc} WK1+WK2) + MFG SOC Pending · Mfg Req = (Qty + MSL) − On-hand (WH+Branch)</th>
              <th className="grp" colSpan={2}>Actual sales (3-JC avg)</th>
              <th className="grp" colSpan={2}>FG stock</th>
              <th>BOM</th><th></th>
            </tr>
            <tr>
              <th></th><th></th>
              <th className="num cg-proj">JC{pjc} Qty (WK1+2)</th><th className="num cg-proj">Overall SOC</th><th className="num cg-proj">MFG SOC Pending</th>
              <th className="num cg-proj">JC{pjc} Qty</th><th className="num cg-proj">JC{pjc + 1} Qty</th><th className="num cg-proj">JC{pjc + 2} Qty</th><th className="num cg-proj">Total</th>
              <th className="num cg-proj" title="MSL safety-stock buffer added to the Current-JC demand — only valid items (moved in >10 of 13 JCs AND served >5 unique customers)">+MSL buffer</th>
              <th className="num cg-proj" title="MSL-driven Current-JC quantity = (JC Qty + Pending SOC + MSL) − on-hand FG stock (Warehouse + Branch)">Mfg Req (Current)</th><th className="num cg-proj" title="(3-JC Total + MSL) − on-hand FG stock (Warehouse + Branch)">Mfg Req (3 JC)</th><th className="num cg-proj" title="FG producible from RAW MATERIAL available to this FG (chemical RMs only — packing excluded) after PTS (Plan-To-Stock) FGs are served first; shared RM allocated by priority">Producible (PTS-first)</th>
              <th className="num">Avg sales</th><th>vs proj</th>
              <th className="num">Warehouse</th><th className="num">Branch</th>
              <th></th><th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ p, i }) => {
              const bi = sel[i] ?? 0;
              const bom = p.boms && p.boms[bi];
              const overridden = bi !== 0 || p.overridden;
              const netSum = bom ? bom.components.reduce((a, c) => a + (c.net_total || 0), 0) : 0;
              const isOpen = open === i;
              return (
                <React.Fragment key={i}>
                  <tr className={`parent ${isOpen ? "isopen" : ""}`} style={{ cursor: p.has_bom ? "pointer" : "default" }} onClick={() => p.has_bom && setOpen(isOpen ? null : i)}>
                    <td style={{ color: "var(--muted)" }}>{p.has_bom ? (isOpen ? "▾" : "▸") : ""}</td>
                    <td><b>{p.name}</b> <ActivityChip cls={p.bom_class} />{p.msl_only && <span className="chip" style={{ marginLeft: 4, cursor: "default", fontSize: 10, fontWeight: 700, background: "#EAF4FF", borderColor: "#BBD9F5" }} title="No (or negligible) projection this JC — added to top up to MSL safety stock (on-hand is below MSL). Demand = (projection + pending + MSL) − on-hand.">MSL top-up</span>}{p.pts_pto && <span className="chip" style={{ marginLeft: 4, cursor: "default", fontSize: 10, fontWeight: 700, background: p.pts_pto === "PTS" ? "#E6F4EA" : "#EEF1F5", borderColor: p.pts_pto === "PTS" ? "#B7E1C4" : "#D9DEE5" }} title={p.pts_pto === "PTS" ? "Plan-To-Stock — served first in shared-RM allocation" : "Plan-To-Order — served after PTS"}>{p.pts_pto}</span>}{bom && <div style={{ fontSize: 11, color: "var(--muted)" }}>{bom.assembly_item} · {bom.org_code} · {bom.designator}{bom.created ? ` · ${bom.created}` : ""}{bom.bom_type ? ` · ${bom.bom_type}` : ""}</div>}
                      {p.packing_boms && p.packing_boms.length > 0 && <div style={{ marginTop: 3 }}><span className="chip" style={{ cursor: "default", background: "#EEF6FF", borderColor: "#CFE4FB" }}>📦 {p.packing_boms.length} packing BOM{p.packing_boms.length > 1 ? "s" : ""}</span></div>}</td>
                    <td className="num cg-proj">{fmt.num(p.projection.current_target)}</td>
                    <td className="num cg-proj">{p.projection.overall_soc > 0 ? fmt.num(p.projection.overall_soc) : "—"}</td>
                    <td className="num cg-proj" style={p.projection.mfg_soc > 0 ? { background: "var(--amber-bg, #FFF4DA)", fontWeight: 600 } : null}>{p.projection.mfg_soc > 0 ? `+${fmt.num(p.projection.mfg_soc)}` : "—"}</td>
                    <td className="num cg-proj"><b>{fmt.num(p.projection.current)}</b></td>
                    <td className="num cg-proj">{fmt.num(p.projection.next1)}</td>
                    <td className="num cg-proj">{fmt.num(p.projection.next2)}</td>
                    <td className="num cg-proj"><b>{fmt.num(p.projection.total)}</b></td>
                    <td className="num cg-proj" style={p.projection.msl > 0 ? { background: "#EAF4FF", fontWeight: 600 } : null}>{p.projection.msl > 0 ? `+${fmt.num(p.projection.msl)}` : "—"}</td>
                    <td className="num cg-proj"><b>{fmt.num(p.projection.mfg_required)}</b></td>
                    <td className="num cg-proj">{fmt.num(p.projection.mfg_required_3jc)}</td>
                    <td className="num cg-proj">{p.has_bom ? fmt.num(p.producible_qty) : "—"}
                      {p.has_bom && p.projection.producible_cover && <div style={{ fontSize: 10, fontWeight: 700, color: /3 JC/.test(p.projection.producible_cover) ? "#1a7d4f" : /Current/.test(p.projection.producible_cover) ? "#2b6cb0" : /None/.test(p.projection.producible_cover) ? "#b23b3b" : "#8a6d00" }} title={`Producible ${fmt.num(p.producible_qty)} vs Mfg-Required — Current JC: ${p.projection.producible_pct_current ?? "—"}% · 3-JC: ${p.projection.producible_pct_3jc ?? "—"}%`}>{p.projection.producible_cover}</div>}</td>
                    <td className="num">{fmt.num(p.avg_3jc_sales)}</td>
                    <td><SalesFlag f={p.proj_flag} /></td>
                    <td className="num">{bom ? fmt.num(bom.fg_stock.warehouse) : "—"}</td>
                    <td className="num">{bom ? fmt.num(bom.fg_stock.branch) : "—"}</td>
                    <td>{p.has_bom
                      ? (<>{overridden ? <Tag kind="soft">overridden</Tag> : <Tag kind="none">preferred</Tag>}
                          <div style={{ marginTop: 4 }}>{netSum > 0
                            ? <span className="pill-buy">buy {fmt.num(netSum)}</span>
                            : <span className="pill-ok">covered</span>}</div></>)
                      : <Tag kind="light">traded</Tag>}</td>
                    <td>{p.alternatives > 0 && (
                      <button className="link" onClick={(e) => { e.stopPropagation(); setMore(more === i ? null : i); }}>
                        More ({p.alternatives})
                      </button>)}</td>
                  </tr>
                  {more === i && p.boms.length > 1 && (
                    <tr className="expander"><td></td><td colSpan={17}>
                      <div style={{ fontSize: 12, marginBottom: 4, color: "var(--muted)" }}>Select BOM (★ preferred):</div>
                      {p.boms.map((a, k) => (
                        <span key={k} className={`chip ${k === bi ? "active" : ""} ${a.preferred ? "preferred" : ""}`}
                          onClick={() => { pick(i, k); setOpen(i); setMore(null); }}>
                          {a.assembly_item} · {a.org_code} · {a.designator}{a.created ? ` · ${a.created}` : ""}{a.preferred ? " ★" : ""}
                        </span>
                      ))}
                    </td></tr>
                  )}
                  {open === i && bom && bom.components.length > 0 && (
                    <tr className="expander"><td></td><td colSpan={17}>
                      <table className="subtable">
                        <thead>
                          <tr><th>Seq</th><th>RM (main) + substitutes</th><th className="num">Qty/unit</th>
                            <th className="grp" colSpan={4}>Gross requirement (KG)</th>
                            <th className="num">Main stk</th><th className="num">Sub stk</th>
                            <th className="num">Received</th><th className="num">In-transit</th>
                            <th className="grp" colSpan={4}>Net to buy (KG)</th></tr>
                          <tr><th></th><th></th><th></th>
                            <th className="num cg-proj">Curr</th><th className="num cg-proj">N1</th><th className="num cg-proj">N2</th><th className="num cg-proj">Total</th>
                            <th></th><th></th><th></th><th></th>
                            <th className="num cg-net">Curr</th><th className="num cg-net">N1</th><th className="num cg-net">N2</th><th className="num cg-net">Total</th></tr>
                        </thead>
                        <tbody>
                          {bom.components.map((c, k) => (
                            <tr key={k}>
                              <td>{c.seq}</td>
                              <td><b>{data.decode_names ? c.rm_desc : c.rm_code}</b>
                                <div style={{ fontSize: 11, color: "var(--muted)" }}>{data.decode_names ? c.rm_code : c.rm_desc}
                                  {c.substitutes.length > 0 && <> · subs: {c.substitutes.map((su) => `${su.desc || su.code} [${su.code}] (${fmt.num(su.stock)})`).join(", ")}</>}</div></td>
                              <td className="num">{c.qty_per_unit}</td>
                              <td className="num cg-proj">{fmt.num(c.gross.current)}</td>
                              <td className="num cg-proj">{fmt.num(c.gross.next1)}</td>
                              <td className="num cg-proj">{fmt.num(c.gross.next2)}</td>
                              <td className="num cg-proj"><b>{fmt.num(c.gross_total)}</b></td>
                              <td className="num">{fmt.num(c.main_stock)}</td>
                              <td className="num">{fmt.num(c.substitute_stock)}</td>
                              <td className="num">{fmt.num(c.received)}</td>
                              <td className="num">{fmt.num(c.in_transit)}</td>
                              <td className="num cg-net"><NetCell v={c.net_to_buy.current} /></td>
                              <td className="num cg-net"><NetCell v={c.net_to_buy.next1} /></td>
                              <td className="num cg-net"><NetCell v={c.net_to_buy.next2} /></td>
                              <td className="num cg-net"><b><NetCell v={c.net_total} /></b></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {p.packing_boms && p.packing_boms.length > 0 && (
                        <div style={{ marginTop: 14 }}>
                          <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 6 }}>📦 Packing BOMs — plan packing material ({p.packing_boms.length})</div>
                          {p.packing_boms.map((pb, pk) => (
                            <div key={pk} style={{ marginBottom: 8 }}>
                              <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 2 }}>
                                <b>{pb.assembly_item}</b> · {pb.org_code} · {pb.designator}{pb.created ? ` · ${pb.created}` : ""} · FG stock W {fmt.num(pb.fg_stock.warehouse)} / B {fmt.num(pb.fg_stock.branch)}
                              </div>
                              <table className="subtable">
                                <thead>
                                  <tr><th>Seq</th><th>Packing / component</th><th className="num">Qty/unit</th>
                                    <th className="num">Gross total</th><th className="num">Stock</th>
                                    <th className="num">In-transit</th><th className="num cg-net">Net to buy</th></tr>
                                </thead>
                                <tbody>
                                  {pb.components.map((c, ck) => (
                                    <tr key={ck}>
                                      <td>{c.seq}</td>
                                      <td><b>{data.decode_names ? c.rm_desc : c.rm_code}</b>
                                        <div style={{ fontSize: 11, color: "var(--muted)" }}>{data.decode_names ? c.rm_code : c.rm_desc}</div></td>
                                      <td className="num">{c.qty_per_unit}</td>
                                      <td className="num"><b>{fmt.num(c.gross_total)}</b></td>
                                      <td className="num">{fmt.num(c.main_stock)}</td>
                                      <td className="num">{fmt.num(c.in_transit)}</td>
                                      <td className="num cg-net"><b><NetCell v={c.net_total} /></b></td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          ))}
                          <div className="sub" style={{ marginTop: 2 }}>Packing BOMs are shown separately for packing-material planning and are not added to the RM consolidation (FG demand is not split across pack sizes).</div>
                        </div>
                      )}
                    </td></tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      )}
      {mode === "product" && (
        <div className="sub supply-summary" style={{ marginTop: 8 }}>{s.with_bom} of {s.shown} shown products matched a BOM; {s.without_bom} traded (no recipe).
          Net-to-buy: <span className="num-pos">red = buy</span> · <span className="num-zero">green = covered</span>. FG stock = Warehouse (MFG orgs) vs Branch.
          {" "}MFG SOC of <b>{fmt.num(s.mfg_soc_in_plan ?? s.pending_soc_in_plan)}</b> KG added across <b>{s.pending_soc_items}</b> planned items (Overall SOC {fmt.num(s.overall_soc_total)} KG){s.stock_source ? <> · stock source: <b>{s.stock_source}</b></> : null}.
          {s.msl_total > 0 && <> {" "}<b>MSL buffer</b> of <b>{fmt.num(s.msl_total)}</b> KG added across <b>{s.msl_items}</b> valid items; on-hand (WH+Branch) netted <b>{fmt.num(s.onhand_total)}</b> KG. Mfg Req = {s.mfg_required_formula}.{s.msl_only_items > 0 && <> Of these, <b>{s.msl_only_items}</b> had no projection this JC (<b>MSL top-up</b> — a BOM item below its MSL; demand = MSL − on-hand).</>}</>}
          {s.plan_divisions?.length > 0 && <> {" "}Scope: <b>Division = {s.plan_divisions.join(", ")}</b> only{s.out_of_scope_items > 0 && <> ({fmt.num(s.out_of_scope_items)} projected items in other divisions excluded)</>}.</>}
          {s.with_packing_bom > 0 && <> {s.packing_bom_count} packing BOM(s) across {s.with_packing_bom} products shown separately.</>}</div>
      )}
    </section>
  );
}

const ACT = {
  manufacturing: { t: "MFG", bg: "#E3F3E8", c: "#1a7d4f" },
  repack_relabel: { t: "REPACK/RELABEL", bg: "#EAF1FF", c: "#1768c4" },
  trading: { t: "TRADING", bg: "#FDECEF", c: "#b03052" },
  internal: { t: "INTERNAL", bg: "#F0EEF6", c: "#6b5b95" },
  unclassified: { t: "UNCLASSIFIED", bg: "#F3F0E8", c: "#8a6d00" },
  none: { t: "NO BOM", bg: "#F4F4F5", c: "#888" },
};
function ActivityChip({ cls }) {
  const a = ACT[cls] || ACT.none;
  return <span className="chip" style={{ cursor: "default", background: a.bg, color: a.c, borderColor: "transparent", fontSize: 10.5, fontWeight: 700 }}>{a.t}</span>;
}

function RmPlanChip({ r, pjc }) {
  // Available (RM on hand for the lead-time horizon → no purchase) vs Buy-in-JCs.
  const lbl = (k) => k === "current" ? `JC${pjc}` : k === "next1" ? `JC${pjc + 1}` : `JC${pjc + 2}`;
  if (!r.to_buy) {
    return <span className="chip" title="Raw material available — no purchase needed this cycle" style={{ marginLeft: 6, cursor: "default", fontSize: 10, fontWeight: 700, background: "#E6F4EA", color: "#1a7d4f", borderColor: "transparent" }}>✓ Available</span>;
  }
  const jcs = (r.buy_jcs && r.buy_jcs.length ? r.buy_jcs : r.planned_jcs || []).map(lbl).join(", ");
  return <span className="chip" title="Not available — plan the shortfall by lead time (≤30d: current · 31–60d: +next · >60d: all 3)" style={{ marginLeft: 6, cursor: "default", fontSize: 10, fontWeight: 700, background: "#FDECEC", color: "#b23b3b", borderColor: "transparent" }}>🛒 Buy {jcs}</span>;
}

function RmActivityChip({ a }) {
  if (!a) return null;
  const has = (k) => a.includes(k);
  const bg = has("Manufacturing") && has("Repack") ? "#EFE9FB"
    : has("Manufacturing") ? "#E6F4EA" : has("Repack") ? "#FDECEC"
      : a === "Packing" ? "#EEF6FF" : "#F3F0E8";
  const c = has("Manufacturing") && has("Repack") ? "#5b3fa0"
    : has("Manufacturing") ? "#1a7d4f" : has("Repack") ? "#b23b3b"
      : a === "Packing" ? "#2b6cb0" : "#8a6d00";
  return <span className="chip" title={`Used in ${a} finished goods`} style={{ marginLeft: 6, cursor: "default", background: bg, color: c, borderColor: "transparent", fontSize: 10, fontWeight: 700 }}>{a}</span>;
}

function UnmatchedIntransit({ items }) {
  const [open, setOpen] = useState(false);
  if (!items || !items.length) return null;
  const total = items.reduce((a, x) => a + (x.in_transit || 0), 0);
  return (
    <div className="banner" style={{ marginBottom: 14, background: "#FFF7E6", border: "1px solid #F0D8A0" }}>
      <div style={{ cursor: "pointer" }} onClick={() => setOpen(!open)}>
        ⚠️ <b>{items.length} item(s) with open-PO in-transit ({fmt.num(total)} KG) are NOT matched to any planned BOM RM.</b>{" "}
        These are bought/in-transit but absent from every recipe in scope, or a code/description mismatch (also in the report's <b>“In-transit Unmatched”</b> sheet). <button className="link">{open ? "hide" : "show"}</button>
      </div>
      {open && (
        <div className="tbl-wrap" style={{ marginTop: 8 }}>
          <table>
            <thead><tr><th>Item Description</th><th>Item Code</th><th className="num">In-transit (KG)</th><th className="num">#PO lines</th><th>Latest PO</th><th>Vendors</th></tr></thead>
            <tbody>
              {items.map((u, i) => (
                <tr key={i}>
                  <td><b>{u.item_desc}</b></td><td>{u.item_code}</td>
                  <td className="num">{fmt.num(u.in_transit)}</td><td className="num">{u.po_count}</td>
                  <td>{u.latest_po}</td><td style={{ fontSize: 11, color: "var(--muted)" }}>{(u.vendors || []).join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>)}
    </div>
  );
}

function ConsolidatedRM({ data, q, rmCls = "manufacturing", pjc = 4 }) {
  const [open, setOpen] = useState(null);
  const pick = rmCls === "manufacturing"
    ? { list: data.consolidated_rm_manufacturing, cs: data.consolidated_summary_manufacturing, label: "Manufacturing" }
    : rmCls === "repack"
      ? { list: data.consolidated_rm_repack, cs: data.consolidated_summary_repack, label: "Repack / Relabel" }
      : rmCls === "packing"
        ? { list: data.consolidated_rm_packing, cs: data.consolidated_summary_packing, label: "Packing Material" }
        : { list: data.consolidated_rm, cs: data.consolidated_summary, label: "All activities" };
  const cs = pick.cs;
  const rows = (pick.list || []).filter((r) =>
    !q || r.rm_code.toLowerCase().includes(q.toLowerCase()) || (r.rm_desc || "").toLowerCase().includes(q.toLowerCase()));
  return (
    <>
      <div className="banner info page-intro" style={{ marginTop: 0 }}>
        <b>Consolidated purchase — {pick.label}.</b> Consolidated by <b>item description</b> (one row per material, all its item codes rolled up),
        summed across the {pick.label.toLowerCase()} finished goods, then netted against its stock (+ substitutes) and in-transit.
        Manufacturing, Repack/Relabel and <b>Packing material</b> are planned <b>separately</b>; packing (item codes starting “P”) is excluded from the material plans.
        <div style={{ marginTop: 4 }}>🛒 <b>Plan rule:</b> if the RM is <b>available</b> (stock + substitutes + in-transit covers the requirement) it shows <b>✓ Available — no purchase</b>. If short, only the shortfall is planned, <b>by lead time</b> — ⏱ ≤30d → buy JC{pjc} only · 31–60d → +JC{pjc + 1} · &gt;60d → all three.</div>
      </div>
      <div className="grid cols-3" style={{ marginBottom: 14 }}>
        <div className="card statcard"><div className="ic">⚗️</div><Stat value={fmt.num(cs.distinct_rms)} label="Distinct RMs required" /></div>
        <div className="card statcard red"><div className="ic">🛒</div><Stat value={fmt.num(cs.rms_to_buy)} label="RMs to purchase (net &gt; 0)" /></div>
        <div className="card statcard amber"><div className="ic">⚖️</div><Stat value={fmt.num(cs.total_buy_qty)} label="Total buy quantity (KG)" /></div>
      </div>
      <UnmatchedIntransit items={data.intransit_unmatched} />
      <div className="tbl-wrap">
        <table>
          <thead>
            <tr>
              <th style={{ width: 24 }}></th><th>Raw material</th><th className="num">Used in #FG</th>
              <th className="grp" colSpan={4}>Total gross (KG)</th>
              <th className="num">Stock</th><th className="num">Sub stk</th><th className="num">In-transit</th>
              <th className="grp" colSpan={4}>Net to buy (KG)</th>
            </tr>
            <tr>
              <th></th><th></th><th></th>
              <th className="num cg-proj">JC{pjc}</th><th className="num cg-proj">JC{pjc + 1}</th><th className="num cg-proj">JC{pjc + 2}</th><th className="num cg-proj">Total</th>
              <th></th><th></th><th></th>
              <th className="num cg-net">JC{pjc}</th><th className="num cg-net">JC{pjc + 1}</th><th className="num cg-net">JC{pjc + 2}</th><th className="num cg-net">Total</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const isOpen = open === i;
              return (
                <React.Fragment key={i}>
                  <tr className={`parent ${isOpen ? "isopen" : ""}`} style={{ cursor: "pointer" }} onClick={() => setOpen(isOpen ? null : i)}>
                    <td style={{ color: "var(--muted)" }}>{isOpen ? "▾" : "▸"}</td>
                    <td><b>{r.rm_desc}</b>
                      <RmActivityChip a={r.activity} />
                      <RmPlanChip r={r} pjc={pjc} />
                      {r.business && <span className="chip" style={{ marginLeft: 6, cursor: "default", fontSize: 10, background: /raw material/i.test(r.business) ? "#EEF6FF" : "#FFF4DA", borderColor: /raw material/i.test(r.business) ? "#CFE4FB" : "#F0D8A0" }} title={/raw material/i.test(r.business) ? "Raw Material — stock counted across all RM-source orgs" : "Non-RM business — stock counted at MFG plants only"}>{r.business}</span>}
                      <div style={{ fontSize: 11, color: "var(--muted)" }}>{r.code_count > 1 ? `${r.code_count} item codes · ${(r.rm_codes || []).slice(0, 4).join(", ")}${r.code_count > 4 ? "…" : ""}` : r.rm_code}</div>
                      {(r.avg_lead_time_days != null || r.trade) && (
                        <div style={{ fontSize: 11, marginTop: 2 }}>
                          {r.avg_lead_time_days != null && <span title={`avg over latest ${r.lead_basis_pos || 5} purchases · +7d preprocessing = ${fmt.num(r.lead_total_days)}d total`}>⏱ lead {fmt.num(r.avg_lead_time_days)}d (+7 = {fmt.num(r.lead_total_days)}d)</span>}
                          {r.trade && <span style={{ marginLeft: 6, color: r.trade === "Import" ? "var(--red)" : "var(--muted)" }}>{r.trade === "Import" ? "🌐 Import" : "Domestic"}</span>}
                          {r.currencies && r.currencies.length > 0 && <span style={{ marginLeft: 6, color: "var(--muted)" }}>{r.currencies.join(", ")}</span>}
                        </div>)}</td>
                    <td className="num">{r.fg_count}</td>
                    <td className="num cg-proj">{fmt.num(r.gross.current)}</td>
                    <td className="num cg-proj">{fmt.num(r.gross.next1)}</td>
                    <td className="num cg-proj">{fmt.num(r.gross.next2)}</td>
                    <td className="num cg-proj"><b>{fmt.num(r.gross_total)}</b></td>
                    <td className="num">{fmt.num(r.main_stock)}</td>
                    <td className="num">{fmt.num(r.substitute_stock)}</td>
                    <td className="num">{fmt.num(r.in_transit)}</td>
                    <td className="num cg-net"><NetCell v={r.net_to_buy.current} /></td>
                    <td className="num cg-net"><NetCell v={r.net_to_buy.next1} /></td>
                    <td className="num cg-net"><NetCell v={r.net_to_buy.next2} /></td>
                    <td className="num cg-net"><b><NetCell v={r.net_total} /></b></td>
                  </tr>
                  {isOpen && (
                    <tr className="expander"><td></td><td colSpan={13}>
                      <div style={{ fontSize: 12 }}>
                        <b>Used in {r.fg_count} finished good(s):</b> {r.fgs.join(", ")}{r.fg_count > r.fgs.length ? " …" : ""}
                        {r.substitutes.length > 0 && <div style={{ marginTop: 4 }}><b>Substitutes:</b> {r.substitutes.map((su) => `${su.desc || su.code} [${su.code}] (${fmt.num(su.stock)})`).join(", ")}</div>}
                        {r.suppliers && r.suppliers.length > 0 && (
                          <div style={{ marginTop: 4 }}><b>Suppliers ({r.supplier_count}):</b> {r.suppliers.join(", ")}
                            {r.locations && r.locations.length > 0 && <span style={{ color: "var(--muted)" }}> · {r.locations.join(", ")}</span>}
                            {r.avg_lead_time_days != null && <span style={{ color: "var(--muted)" }}> · avg lead {fmt.num(r.avg_lead_time_days)} days · {r.trade}</span>}</div>)}
                        <div style={{ marginTop: 4, color: "var(--muted)" }}>Available = stock {fmt.num(r.main_stock)} + substitutes {fmt.num(r.substitute_stock)} + in-transit {fmt.num(r.in_transit)} = {fmt.num(r.available)} · already received {fmt.num(r.received)}</div>
                      </div>
                    </td></tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="sub" style={{ marginTop: 8 }}>{rows.length} of {cs.distinct_rms} RMs shown. Net-to-buy: <span className="num-pos">red = buy</span> · <span className="num-zero">green = covered</span>. One RM used by multiple FGs is summed into a single purchase quantity.</div>
    </>
  );
}

function RealRM({ data, q, pjc }) {
  const [open, setOpen] = useState(null);
  const cs = data.real_rm_summary || {};
  const list = data.real_rm_requirement || [];
  const rows = list.filter((r) =>
    !q || (r.rm_code || "").toLowerCase().includes(q.toLowerCase()) || (r.rm_desc || "").toLowerCase().includes(q.toLowerCase()));
  return (
    <>
      <div className="banner info page-intro" style={{ marginTop: 0 }}>
        <b>Real RM requirement.</b> Every encoded <b>intermediate</b> (an item that has its own BOM, e.g. <code>RDNBP101</code>) is
        recursively <b>exploded to its purchased leaf raw materials</b> (the real manufacturing recipe is used), quantities rolled up and <b>names decoded</b>.
        This is the true buy-list. Rows tagged <b>“via …”</b> were reached through one or more intermediates.
      </div>
      {cs.unresolved_intermediates > 0 && (
        <div className="banner" style={{ marginBottom: 14, background: "#FFF7E6", border: "1px solid #F0D8A0" }}>
          ⚠️ <b>{fmt.num(cs.unresolved_intermediates)} item(s) ({fmt.num(cs.unresolved_qty)} KG) could not be fully exploded</b> — their encoded BOMs are
          <b> circular / self-referential</b> (an intermediate whose recipe loops back to itself via another code). They stay listed as the intermediate
          (tagged <b>⚠ Unresolved</b> below and “Unresolved intermediate” in the report). Fix the BOM master to resolve them to raw materials.
        </div>)}
      <div className="grid cols-4" style={{ marginBottom: 14 }}>
        <div className="card statcard"><div className="ic">🧪</div><Stat value={fmt.num(cs.distinct_rms)} label="Distinct leaf RMs" /></div>
        <div className="card statcard red"><div className="ic">🛒</div><Stat value={fmt.num(cs.rms_to_buy)} label="RMs to purchase (net &gt; 0)" /></div>
        <div className="card statcard amber"><div className="ic">⚖️</div><Stat value={fmt.num(cs.total_buy_qty)} label="Total buy quantity (KG)" /></div>
        <div className="card statcard"><div className="ic">⚠️</div><Stat value={fmt.num(cs.unresolved_intermediates)} label="Unresolved (circular BOM)" /></div>
      </div>
      <div className="tbl-wrap">
        <table>
          <thead>
            <tr>
              <th style={{ width: 24 }}></th><th>Raw material (decoded)</th><th>Business</th><th className="num">#FG</th>
              <th className="grp" colSpan={4}>Gross requirement (KG)</th>
              <th className="num">Stock</th><th className="num">In-transit</th>
              <th className="grp" colSpan={4}>Net to buy (KG)</th>
            </tr>
            <tr>
              <th></th><th></th><th></th><th></th>
              <th className="num cg-proj">JC{pjc}</th><th className="num cg-proj">JC{pjc + 1}</th><th className="num cg-proj">JC{pjc + 2}</th><th className="num cg-proj">Total</th>
              <th></th><th></th>
              <th className="num cg-net">JC{pjc}</th><th className="num cg-net">JC{pjc + 1}</th><th className="num cg-net">JC{pjc + 2}</th><th className="num cg-net">Total</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const isOpen = open === i;
              return (
                <React.Fragment key={i}>
                  <tr className={`parent ${isOpen ? "isopen" : ""}`} style={{ cursor: "pointer" }} onClick={() => setOpen(isOpen ? null : i)}>
                    <td style={{ color: "var(--muted)" }}>{isOpen ? "▾" : "▸"}</td>
                    <td><b>{r.rm_desc}</b>
                      <RmActivityChip a={r.activity} />
                      <RmPlanChip r={r} pjc={pjc} />
                      {r.unresolved && <span className="chip" style={{ marginLeft: 6, cursor: "default", fontSize: 10, fontWeight: 700, background: "#FDE9CF", color: "#a15c00", borderColor: "#F0D8A0" }} title="This is still an encoded intermediate — its BOM is circular/self-referential so it could not be exploded to raw materials. Fix the BOM master.">⚠ Unresolved</span>}
                      {r.via_intermediate && <span className="chip" style={{ marginLeft: 6, cursor: "default", fontSize: 10, background: "#FFF4DA", borderColor: "#F0D8A0" }} title={`Exploded from intermediate(s): ${(r.from_intermediates || []).join(", ")}`}>via {(r.from_intermediates || [])[0]}{(r.from_intermediates || []).length > 1 ? ` +${r.from_intermediates.length - 1}` : ""}</span>}
                      {r.has_encoded_stock && <span className="chip" style={{ marginLeft: 6, cursor: "default", fontSize: 10, fontWeight: 700, background: "#EDE7F6", color: "#5b3fa0", borderColor: "#D6C8F0" }} title={`${fmt.num(r.encoded_stock)} KG is held in CRM under encoded name ${r.encoded_names || "?"} and merged into this row's Stock.`}>⚑ encoded stock</span>}
                      <div style={{ fontSize: 11, color: "var(--muted)" }}>{r.code_count > 1 ? `${r.code_count} item codes · ${(r.rm_codes || []).slice(0, 4).join(", ")}` : r.rm_code}{r.lead_total_days != null ? ` · ⏱ ${fmt.num(r.lead_total_days)}d` : ""}</div></td>
                    <td>{r.business && <span className="chip" style={{ cursor: "default", fontSize: 10, background: /raw material/i.test(r.business) ? "#EEF6FF" : "#FFF4DA", borderColor: /raw material/i.test(r.business) ? "#CFE4FB" : "#F0D8A0" }}>{r.business}</span>}</td>
                    <td className="num">{r.fg_count}</td>
                    <td className="num cg-proj">{fmt.num(r.gross.current)}</td>
                    <td className="num cg-proj">{fmt.num(r.gross.next1)}</td>
                    <td className="num cg-proj">{fmt.num(r.gross.next2)}</td>
                    <td className="num cg-proj"><b>{fmt.num(r.gross_total)}</b></td>
                    <td className="num" title={r.stock_orgs || undefined}>{fmt.num(r.main_stock)}</td>
                    <td className="num">{fmt.num(r.in_transit)}</td>
                    <td className="num cg-net"><NetCell v={r.net_to_buy.current} /></td>
                    <td className="num cg-net"><NetCell v={r.net_to_buy.next1} /></td>
                    <td className="num cg-net"><NetCell v={r.net_to_buy.next2} /></td>
                    <td className="num cg-net"><b><NetCell v={r.net_total} /></b></td>
                  </tr>
                  {isOpen && (
                    <tr className="expander"><td></td><td colSpan={13}>
                      <div style={{ fontSize: 12 }}>
                        {r.via_intermediate && <div style={{ marginBottom: 4 }}><b>Exploded from intermediate(s):</b> {(r.from_intermediates || []).join(", ")}</div>}
                        <b>Used in {r.fg_count} finished good(s):</b> {(r.fgs || []).join(", ")}{r.fg_count > (r.fgs || []).length ? " …" : ""}
                        {r.stock_orgs && <div style={{ marginTop: 4 }}><b>Stock by org:</b> {r.stock_orgs}</div>}
                        {r.has_encoded_stock && <div style={{ marginTop: 2, color: "#5b3fa0", fontSize: 11 }}>Includes <b>{fmt.num(r.encoded_stock)} KG</b> held under encoded name {r.encoded_names ? <b>{r.encoded_names}</b> : "(unknown)"} in CRM (merged into Stock so the on-hand is complete).</div>}
                        <div style={{ marginTop: 4, color: "var(--muted)" }}>Available = stock {fmt.num(r.main_stock)} + in-transit {fmt.num(r.in_transit)} = {fmt.num(r.available)}. Substitutes are not modelled on the exploded buy-list.</div>
                      </div>
                    </td></tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="sub" style={{ marginTop: 8 }}>{rows.length} of {cs.distinct_rms} leaf RMs shown. Net-to-buy: <span className="num-pos">red = buy</span> · <span className="num-zero">green = covered</span>.</div>
    </>
  );
}

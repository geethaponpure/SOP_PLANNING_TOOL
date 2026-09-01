import React, { useState } from "react";
import SegTabs from "../components/SegTabs.jsx";
import SmoothInput from "../components/SmoothInput.jsx";
import { api, fmt } from "../api";
import { useAsync, Loading, ErrorBox, Tag, Stat } from "../components/ui.jsx";

const NetCell = ({ v }) => <span className={v > 0 ? "num-pos" : "num-zero"}>{fmt.num(v)}</span>;

const LEAD_AMBER = 30, LEAD_RED = 60;   // days
const leadColor = (d) => (d == null ? "var(--muted)" : d >= LEAD_RED ? "#a11" : d >= LEAD_AMBER ? "#8a6d00" : "#1a7d4f");
const LeadCell = ({ v }) => (
  <span style={{ color: leadColor(v), fontWeight: v != null && v >= LEAD_AMBER ? 600 : 400 }}>
    {v == null ? "—" : `${fmt.num(v, 1)}d`}
  </span>
);

function applySort(rows, { key, dir }) {
  if (!key) return rows;
  const mul = dir === "desc" ? -1 : 1;
  return [...rows].sort((a, b) => {
    const va = a[key], vb = b[key];
    if (typeof va === "number" && typeof vb === "number") return (va - vb) * mul;
    return String(va ?? "").localeCompare(String(vb ?? "")) * mul;
  });
}

function SortTh({ label, k, sort, setSort, className }) {
  const active = sort.key === k;
  return (
    <th className={className} style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }} title="Click to sort"
      onClick={() => setSort((s) => (s.key === k ? { key: k, dir: s.dir === "asc" ? "desc" : "asc" } : { key: k, dir: "desc" }))}>
      {label}<span style={{ color: active ? "inherit" : "var(--border)", fontSize: 10 }}>{active ? (sort.dir === "asc" ? " ▲" : " ▼") : " ⇅"}</span>
    </th>
  );
}

export default function Vooki() {
  const { data, loading, error } = useAsync(api.vookiPlanning);
  const [mode, setMode] = useState("product");
  const [q, setQ] = useState("");
  const [qty, setQty] = useState({});        // product name -> planned quantity
  const [bomIdx, setBomIdx] = useState({});  // product name -> selected BOM index
  const [open, setOpen] = useState(null);    // expanded product name
  const [exporting, setExporting] = useState(false);
  const [rowBusy, setRowBusy] = useState(null);   // product name being exported
  const [sortP, setSortP] = useState({ key: "name", dir: "asc" });
  const [sortC, setSortC] = useState({ key: "net_to_buy", dir: "desc" });
  if (loading) return <Loading what="Vooki Planning" />;
  if (error) return <ErrorBox msg={error} />;
  if (data.note) return <div className="banner info">{data.note}</div>;

  const s = data.summary;
  const decode = data.decode_names;
  const ql = q.toLowerCase();
  const bomOf = (p) => p.boms[Math.min(bomIdx[p.name] || 0, p.boms.length - 1)];
  const grossOf = (p, c) => (qty[p.name] || 0) * c.qty_per_unit;
  const netOf = (p, c) => Math.max(0, grossOf(p, c) - c.available);
  const rmToBuy = (p) => (p.has_bom ? bomOf(p).components.reduce((a, c) => a + netOf(p, c), 0) : 0);
  const plannedCount = data.products.filter((p) => (qty[p.name] || 0) > 0).length;
  // FG qty producible from current RM: min = main stock only; max = main + subs + in-transit
  const minMax = (p) => {
    let mn = Infinity, mx = Infinity;
    if (p.has_bom) for (const c of bomOf(p).components) if (c.qty_per_unit > 0) {
      mn = Math.min(mn, c.main_stock / c.qty_per_unit);
      mx = Math.min(mx, c.available / c.qty_per_unit);
    }
    return { min: isFinite(mn) ? mn : 0, max: isFinite(mx) ? mx : 0 };
  };
  const downloadOne = async (name) => {
    setRowBusy(name);
    try { await api.vookiPlanningExport(qty, name); } catch (e) { alert(e.message); } finally { setRowBusy(null); }
  };

  const products = applySort(
    data.products
      .filter((p) => !q || p.name.toLowerCase().includes(ql))
      .map((p) => { const mm = minMax(p); return { ...p, _rmbuy: rmToBuy(p), _min: mm.min, _max: mm.max }; }),
    sortP);

  // consolidated RM across all products with a planned quantity
  const consMap = {};
  for (const p of data.products) {
    if (!p.has_bom || (qty[p.name] || 0) <= 0) continue;
    for (const c of bomOf(p).components) {
      const key = (c.rm_desc || c.rm_code).toUpperCase();   // consolidate by item description
      const a = consMap[key] || (consMap[key] = {
        rm_code: c.rm_code, rm_desc: c.rm_desc, gross: 0, available: c.available, fgs: new Set() });
      a.gross += grossOf(p, c);
      a.available = Math.max(a.available, c.available);      // desc-consolidated availability
      a.fgs.add(p.name);
    }
  }
  const cons = applySort(Object.values(consMap)
    .map((a) => ({ ...a, fg_count: a.fgs.size, net_to_buy: Math.max(0, a.gross - a.available) }))
    .filter((a) => !q || a.rm_code.toLowerCase().includes(ql) || (a.rm_desc || "").toLowerCase().includes(ql)),
    sortC);

  return (
    <>
      <div className="banner info page-intro">
        <b>Vooki Planning.</b> Enter a <b>quantity to plan</b> for each Vooki finished good — the RM requirement
        explodes instantly through the selected BOM (preference <b>PMO → BULK/HDLK → newest → Primary</b>; use
        <b> More</b> to override), nets main RM + substitutes against live CRM stock (<code>SPBiStockDetails</code>,
        Business = <b>{data.rules?.fg_business}</b> for FG · <b>{data.rules?.rm_business}</b> + intermediates for RM),
        and against PO received / in-transit. FG stock is unpacked from packaged SKUs (via the item master) into
        units & KG/Lit. Packing BOMs plan separately for packing material.
      </div>

      <div className="grid cols-4">
        <div className="card statcard"><div className="ic">🧴</div><Stat value={fmt.num(s.products)} label="Vooki products (with BOM)" /></div>
        <div className="card statcard blue"><div className="ic">📝</div><Stat value={fmt.num(plannedCount)} label="Products planned (qty entered)" /></div>
        <div className="card statcard"><div className="ic">🏭</div><Stat value={fmt.num(s.rm_items_in_stock)} label="RM items in stock (Business = RM)" /></div>
        <div className="card statcard amber"><div className="ic">📦</div><Stat value={`${fmt.num(s.fg_stock_units)} u · ${fmt.num(s.fg_stock_volume_l)} L`} label="FG stock (units · KG/Lit)" /></div>
      </div>

      <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 10, margin: "16px 0 8px" }}>
        <SegTabs value={mode} onChange={(m) => { setMode(m); setQ(""); }}
          tabs={[{ id: "product", label: "By product" }, { id: "consolidated", label: "Consolidated RM purchase" }]} />
        <button className="btn" style={{ marginLeft: "auto" }} disabled={exporting}
          onClick={async () => { setExporting(true); try { await api.vookiPlanningExport(qty); } catch (e) { alert(e.message); } finally { setExporting(false); } }}>
          {exporting ? "Exporting…" : "⤓ Download report (Excel)"}
        </button>
      </div>

      <div className="pagebar">
        <SmoothInput className="searchbox" placeholder={mode === "product" ? "Search Vooki product…" : "Search RM…"} value={q} onChange={(e) => setQ(e.target.value)} />
        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--muted)" }}>
          {mode === "product" ? `${products.length} products` : `${cons.length} RMs to plan`}
        </span>
      </div>

      {mode === "consolidated" ? (
        <div className="tbl-wrap">
          <table>
            <thead><tr>
              <SortTh label="Raw material" k="rm_desc" sort={sortC} setSort={setSortC} />
              <SortTh label="#FG" k="fg_count" sort={sortC} setSort={setSortC} className="num" />
              <SortTh label="Gross" k="gross" sort={sortC} setSort={setSortC} className="num" />
              <SortTh label="Available" k="available" sort={sortC} setSort={setSortC} className="num" />
              <SortTh label="Net to buy" k="net_to_buy" sort={sortC} setSort={setSortC} className="num" />
            </tr></thead>
            <tbody>
              {cons.map((r, i) => (
                <tr key={i}>
                  <td><b>{decode ? r.rm_desc : r.rm_code}</b><div style={{ fontSize: 11, color: "var(--muted)" }}>{decode ? r.rm_code : r.rm_desc}</div></td>
                  <td className="num">{r.fg_count}</td>
                  <td className="num">{fmt.num(r.gross)}</td>
                  <td className="num">{fmt.num(r.available)}</td>
                  <td className="num"><b><NetCell v={r.net_to_buy} /></b></td>
                </tr>
              ))}
              {cons.length === 0 && <tr><td colSpan={5} style={{ color: "var(--muted)" }}>Enter a plan quantity against a product to build the consolidated RM list.</td></tr>}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="tbl-wrap">
          <table>
            <thead><tr>
              <th style={{ width: 24 }}></th>
              <SortTh label="Vooki product" k="name" sort={sortP} setSort={setSortP} />
              <th className="num">Plan Qty</th>
              <SortTh label="FG stock (u)" k="fg_units" sort={sortP} setSort={setSortP} className="num" />
              <SortTh label="FG (KG/L)" k="fg_volume_l" sort={sortP} setSort={setSortP} className="num" />
              <SortTh label="Min qty" k="_min" sort={sortP} setSort={setSortP} className="num" />
              <SortTh label="Max qty" k="_max" sort={sortP} setSort={setSortP} className="num" />
              <SortTh label="Producible (RM)" k="producible_now" sort={sortP} setSort={setSortP} className="num" />
              <th>BOM</th>
              <SortTh label="RM to buy" k="_rmbuy" sort={sortP} setSort={setSortP} className="num" />
              <th style={{ width: 40 }}></th>
            </tr></thead>
            <tbody>
              {products.map((p, i) => {
                const isOpen = open === p.name;
                const b = bomOf(p);
                return (
                  <React.Fragment key={p.name + i}>
                    <tr className={`parent ${isOpen ? "isopen" : ""}`}>
                      <td style={{ color: "var(--muted)", cursor: p.has_bom ? "pointer" : "default" }}
                        onClick={() => p.has_bom && setOpen(isOpen ? null : p.name)}>{p.has_bom ? (isOpen ? "▾" : "▸") : ""}</td>
                      <td><b>{p.name}</b>{p.alternatives > 0 && <span style={{ fontSize: 11, color: "var(--muted)" }}> · {p.alternatives} alt BOM</span>}</td>
                      <td className="num">
                        <input type="number" min="0" className="searchbox" style={{ width: 96, textAlign: "right", padding: "4px 6px" }}
                          value={qty[p.name] ?? ""} placeholder="0"
                          onChange={(e) => setQty({ ...qty, [p.name]: parseFloat(e.target.value) || 0 })} />
                      </td>
                      <td className="num">{fmt.num(p.fg_units)}</td>
                      <td className="num">{fmt.num(p.fg_volume_l)}</td>
                      <td className="num" style={{ color: "#1a7d4f" }} title="Producible from main RM stock only (direct BOM)">{fmt.num(p._min)}</td>
                      <td className="num" style={{ color: "#1768c4" }} title="Producible from main + substitutes + in-transit (direct BOM)">{fmt.num(p._max)}</td>
                      <td className="num" style={{ color: "#7b2d8e", fontWeight: 600 }}
                        title={`Producible from current on-hand MFG RM stock, intermediates exploded to raw materials.${p.limiting_rm ? " Limited by: " + p.limiting_rm + " (avail " + fmt.num(p.limiting_rm_available) + ")" : ""}`}>
                        {fmt.num(p.producible_now)}</td>
                      <td>{p.has_bom ? <Tag kind="none">{b.designator || "BOM"}</Tag> : <Tag kind="light">no BOM</Tag>}</td>
                      <td className="num"><b><NetCell v={p._rmbuy} /></b></td>
                      <td><button className="link" title="Download this FG report (Excel)" disabled={rowBusy === p.name}
                        onClick={() => downloadOne(p.name)}>{rowBusy === p.name ? "…" : "⤓"}</button></td>
                    </tr>
                    {isOpen && p.has_bom && (
                      <tr className="expander"><td></td><td colSpan={10}>
                        {p.boms.length > 1 && (
                          <div style={{ marginBottom: 6, fontSize: 12 }}>
                            <b>BOM:</b>{" "}
                            {p.boms.map((bb, k) => (
                              <button key={k} className="link" onClick={() => setBomIdx({ ...bomIdx, [p.name]: k })}
                                style={{ fontWeight: (bomIdx[p.name] || 0) === k ? 700 : 400, marginRight: 8 }}>
                                {bb.assembly_item}·{bb.org_code}·{bb.designator}{bb.preferred ? " (preferred)" : ""}
                              </button>
                            ))}
                          </div>
                        )}
                        <table className="subtable">
                          <thead><tr><th>RM (main) + substitutes</th><th className="num">Qty/unit</th><th className="num">Gross</th>
                            <th className="num">Main stk</th><th className="num">Sub stk</th><th className="num">In-transit</th>
                            <th className="num">Available</th><th className="num" title="FG units this RM can produce from available stock">Producible</th>
                            <th className="num">Net to buy</th><th className="num" title="Avg lead time — latest 5 PO receipts">Lead time</th></tr></thead>
                          <tbody>
                            {b.components.map((c, k) => (
                              <tr key={k}>
                                <td><b>{decode ? c.rm_desc : c.rm_code}</b>
                                  <div style={{ fontSize: 11, color: "var(--muted)" }}>{decode ? c.rm_code : c.rm_desc}
                                    {c.substitutes.length > 0 && <> · subs: {c.substitutes.map((su) => `${su.desc || su.code} (${fmt.num(su.stock)})`).join(", ")}</>}</div></td>
                                <td className="num">{c.qty_per_unit}</td>
                                <td className="num">{fmt.num(grossOf(p, c))}</td>
                                <td className="num">{fmt.num(c.main_stock)}</td>
                                <td className="num">{fmt.num(c.substitute_stock)}</td>
                                <td className="num">{fmt.num(c.in_transit)}</td>
                                <td className="num">{fmt.num(c.available)}</td>
                                <td className="num">{fmt.num(c.producible)}</td>
                                <td className="num"><b><NetCell v={netOf(p, c)} /></b></td>
                                <td className="num"><LeadCell v={c.lead_time} /></td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        {p.packing_boms.length > 0 && (
                          <div style={{ marginTop: 8 }}>
                            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>Packing BOMs (packing material):</div>
                            {p.packing_boms.map((pb, k) => (
                              <table className="subtable" key={k} style={{ marginBottom: 6 }}>
                                <thead><tr><th>{pb.assembly_item} · {pb.org_code} · {pb.designator}</th>
                                  <th className="num">Qty/unit</th><th className="num">Gross</th><th className="num">Stock</th><th className="num">Net to buy</th></tr></thead>
                                <tbody>
                                  {pb.components.map((c, j) => (
                                    <tr key={j}>
                                      <td>{decode ? c.rm_desc : c.rm_code}</td>
                                      <td className="num">{c.qty_per_unit}</td>
                                      <td className="num">{fmt.num(grossOf(p, c))}</td>
                                      <td className="num">{fmt.num(c.main_stock)}</td>
                                      <td className="num"><b><NetCell v={netOf(p, c)} /></b></td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            ))}
                          </div>
                        )}
                      </td></tr>
                    )}
                  </React.Fragment>
                );
              })}
              {products.length === 0 && <tr><td colSpan={11} style={{ color: "var(--muted)" }}>No products.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

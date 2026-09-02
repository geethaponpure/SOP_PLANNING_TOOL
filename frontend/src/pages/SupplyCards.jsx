import React, { useState } from "react";
import Pagination, { usePagination } from "../components/Pagination.jsx";
import SegTabs from "../components/SegTabs.jsx";
import SelectBox from "../components/SelectBox.jsx";
import SmoothInput from "../components/SmoothInput.jsx";
import { api, fmt } from "../api";
import { useAsync, Loading, ErrorBox, Tag } from "../components/ui.jsx";

// ── local presentation helpers (kept self-contained so this beta page does not
// depend on Supply.jsx internals; will be merged in later) ───────────────────
const ACT = {
  manufacturing: { t: "MFG", bg: "#E3F3E8", c: "#1a7d4f" },
  repack_relabel: { t: "REPACK/RELABEL", bg: "#EAF1FF", c: "#1768c4" },
  trading: { t: "TRADING", bg: "#FDECEF", c: "#b03052" },
  internal: { t: "INTERNAL", bg: "#F0EEF6", c: "#6b5b95" },
  unclassified: { t: "UNCLASSIFIED", bg: "#F3F0E8", c: "#8a6d00" },
  none: { t: "NO BOM", bg: "#F4F4F5", c: "#888" },
};
const ActivityChip = ({ cls }) => {
  const a = ACT[cls] || ACT.none;
  return <span className="chip" style={{ cursor: "default", background: a.bg, color: a.c, borderColor: "transparent", fontSize: 10.5, fontWeight: 700 }}>{a.t}</span>;
};

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
const NetCell = ({ v }) => <span className={v > 0 ? "num-pos" : "num-zero"}>{fmt.num(v)}</span>;

// One row of the demand build-up table: OP · COMPONENT · QTY
export function BRow({ op, label, value, sub, hint }) {
  const tone = op === "+" ? "pos" : op === "−" ? "neg" : op === "=" ? "eq" : "";
  return (
    <div className={"sc-brow" + (sub ? " sub" : "")} title={hint || ""}>
      <span className={"sc-bop " + tone}>{op || ""}</span>
      <span className="sc-blabel">{label}</span>
      <span className={"sc-bval " + tone}>{value}</span>
    </div>
  );
}

export function ProductCard({ p, data, pjc }) {
  const [open, setOpen] = useState(false);
  const [bi, setBi] = useState(0);
  const [showAlts, setShowAlts] = useState(false);
  const bom = p.boms && p.boms[bi];
  const pr = p.projection || {};
  const netSum = bom ? bom.components.reduce((a, c) => a + (c.net_total || 0), 0) : 0;
  const wh = bom ? bom.fg_stock.warehouse : 0;
  const br = bom ? bom.fg_stock.branch : 0;
  const overridden = bi !== 0 || p.overridden;

  // derived values for the metric bars
  const fgTotal = wh + br;
  const whPct = fgTotal > 0 ? Math.min(100, Math.round((wh / fgTotal) * 100)) : 0;
  const prodPct = pr.mfg_required_3jc > 0
    ? Math.max(0, Math.min(100, Math.round((p.producible_qty / pr.mfg_required_3jc) * 100)))
    : (p.producible_qty > 0 ? 100 : 0);
  const coverText = pr.producible_cover || (prodPct >= 100 ? "Full" : prodPct > 0 ? "Partial" : "None");
  const coverTone = /3 JC|Full/.test(coverText) ? "ok" : /Current/.test(coverText) ? "info" : /None/.test(coverText) ? "bad" : "warn";

  const metadata = bom
    ? [bom.assembly_item, bom.org_code, bom.designator, bom.created, bom.bom_type].filter(Boolean)
    : ["No recipe — traded item"];

  const toggle = () => p.has_bom && setOpen((o) => !o);

  return (
    <div className={"sc-card" + (open ? " open" : "")}>
      {/* ── header (always visible) ─────────────────────────────── */}
      <div className="sc-head" role="button" tabIndex={0} onClick={toggle}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } }}
        style={{ cursor: p.has_bom ? "pointer" : "default" }}>
        <div className="sc-head-main">
          <div className="sc-title">
            {p.has_bom && <span className="sc-caret">{open ? "▾" : "▸"}</span>}
            <span className="sc-name">{p.name}</span>
          </div>
          <div className="sc-chips">
            <ActivityChip cls={p.bom_class} />
            {p.msl_only && <span className="chip" style={{ cursor: "default", fontSize: 10, fontWeight: 700, background: "#EAF4FF", borderColor: "#BBD9F5" }} title="No projection this JC — topped up to MSL safety stock.">MSL top-up</span>}
            {p.pts_pto && <span className="chip" style={{ cursor: "default", fontSize: 10, fontWeight: 700, background: p.pts_pto === "PTS" ? "#E6F4EA" : "#EEF1F5", borderColor: p.pts_pto === "PTS" ? "#B7E1C4" : "#D9DEE5" }} title={p.pts_pto === "PTS" ? "Plan-To-Stock — served first in shared-RM allocation" : "Plan-To-Order — served after PTS"}>{p.pts_pto}</span>}
          </div>
          <div className="sc-meta">
            {metadata.map((m, k) => <React.Fragment key={k}>{k > 0 && <span className="sc-dot" />}<span>{m}</span></React.Fragment>)}
          </div>
        </div>
        <div className="sc-head-make">
          <div className="sc-make">
            <div className="sc-make-label">To make (JC{pjc})</div>
            <div className="sc-make-val">{fmt.num(pr.mfg_required)}<span className="sc-unit"> KG</span></div>
          </div>
          {!p.has_bom
            ? <span className="sc-status traded">traded</span>
            : netSum > 0
              ? <span className="sc-status buy">🛒 buy {fmt.num(netSum)}</span>
              : <span className="sc-status ok">✓ covered</span>}
        </div>
      </div>

      {/* ── metric strip (always visible) ───────────────────────── */}
      <div className="sc-metrics">
        <div className="sc-metric">
          <div className="l">3-JC req</div>
          <div className="v">{fmt.num(pr.mfg_required_3jc)}</div>
        </div>
        <div className="sc-metric">
          <div className="l">Producible</div>
          <div className="v">{p.has_bom ? fmt.num(p.producible_qty) : "—"}{p.has_bom && <span className={"sc-badge " + coverTone}>{coverText}</span>}</div>
          {p.has_bom && <div className="sc-bar"><span className="sc-track"><i className={"sc-fill " + coverTone} style={{ width: prodPct + "%" }} /></span><span className="sc-pct">{prodPct}%</span></div>}
        </div>
        <div className="sc-metric">
          <div className="l">FG stock (W/B)</div>
          <div className="v">{fmt.num(wh)} <span className="sc-muted">/ {fmt.num(br)}</span></div>
          {fgTotal > 0 && <div className="sc-bar"><span className="sc-track"><i className="sc-fill info" style={{ width: whPct + "%" }} /></span></div>}
        </div>
        <div className="sc-metric">
          <div className="l">Avg sales</div>
          <div className="v">{fmt.num(p.avg_3jc_sales)}</div>
          <div style={{ marginTop: 4 }}><SalesFlag f={p.proj_flag} /></div>
        </div>
      </div>

      {/* ── expanded detail ─────────────────────────────────────── */}
      {open && p.has_bom && bom && (
        <div className="sc-body">
          <div className="sc-body-grid">
            {/* demand build-up */}
            <div className="sc-build">
              <div className="sc-sec-title">Demand build-up</div>
              <div className="sc-build-tbl">
                <div className="sc-brow head"><span>OP</span><span>Component</span><span>Qty (KG)</span></div>
                <BRow label={`JC${pjc} Qty (WK1+2)`} value={fmt.num(pr.current_target)} />
                <BRow op="+" label="MFG SOC pending" value={fmt.num(pr.mfg_soc)} hint="Pending sales-order commitments still to manufacture" />
                <BRow op="=" label={`JC${pjc} Qty`} value={fmt.num(pr.current)} sub />
                <BRow op="+" label={`JC${pjc + 1} Qty`} value={fmt.num(pr.next1)} />
                <BRow op="+" label={`JC${pjc + 2} Qty`} value={fmt.num(pr.next2)} />
                <BRow op="=" label="3-JC total" value={fmt.num(pr.total)} sub />
                <BRow op="+" label="MSL safety buffer" value={fmt.num(pr.msl)} hint="Min-stock-level buffer for valid items" />
                <BRow op="−" label="On-hand FG (WH + Branch)" value={fmt.num(wh + br)} hint="Netted off — already in stock" />
              </div>
              <div className="sc-req">
                <div className="sc-req-box"><span>Mfg required — JC{pjc}</span><b>{fmt.num(pr.mfg_required)}</b></div>
                <div className="sc-req-box"><span>Mfg required — 3-JC</span><b>{fmt.num(pr.mfg_required_3jc)}</b></div>
              </div>
              {pr.overall_soc > 0 && <div className="sc-build-note">Overall SOC (context): {fmt.num(pr.overall_soc)} KG</div>}
            </div>

            {/* bom + coverage summary */}
            <div className="sc-side">
              <div className="sc-sec-title">This BOM</div>
              <div className="sc-kv"><span>Recipe</span><b>{bom.assembly_item} · {bom.org_code} · {bom.designator}</b></div>
              <div className="sc-kv"><span>Selection</span>{overridden ? <Tag kind="soft">overridden</Tag> : <Tag kind="none">preferred</Tag>}</div>
              <div className="sc-kv"><span>FG stock</span><b>WH {fmt.num(wh)} · Branch {fmt.num(br)}</b></div>
              <div className="sc-kv"><span>RM status</span>{netSum > 0 ? <span className="pill-buy">buy {fmt.num(netSum)} KG</span> : <span className="pill-ok">covered</span>}</div>
              <div className="sc-kv"><span>Producible</span><b>{fmt.num(p.producible_qty)} KG</b></div>
              {p.alternatives > 0 && (
                <div style={{ marginTop: 8 }}>
                  <button className="link" onClick={() => setShowAlts((s) => !s)}>{showAlts ? "Hide" : `Switch BOM (${p.alternatives} alt)`}</button>
                  {showAlts && (
                    <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 6 }}>
                      {p.boms.map((a, k) => (
                        <span key={k} className={`chip ${k === bi ? "active" : ""} ${a.preferred ? "preferred" : ""}`}
                          onClick={() => { setBi(k); setShowAlts(false); }}>
                          {a.assembly_item} · {a.org_code} · {a.designator}{a.preferred ? " ★" : ""}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* RM components */}
          {bom.components.length > 0 && (
            <div className="sc-rm">
              <div className="sc-sec-title">Raw materials — net to buy</div>
              <div className="tbl-wrap">
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
              </div>
            </div>
          )}

          {/* packing BOMs */}
          {p.packing_boms && p.packing_boms.length > 0 && (
            <div className="sc-rm">
              <div className="sc-sec-title">📦 Packing BOMs ({p.packing_boms.length})</div>
              {p.packing_boms.map((pb, pk) => (
                <div key={pk} style={{ marginBottom: 8 }}>
                  <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 2 }}>
                    <b>{pb.assembly_item}</b> · {pb.org_code} · {pb.designator} · FG W {fmt.num(pb.fg_stock.warehouse)} / B {fmt.num(pb.fg_stock.branch)}
                  </div>
                  <div className="tbl-wrap">
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
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function SupplyCards() {
  const { data, loading, error } = useAsync(api.rmPlanning);
  const [q, setQ] = useState("");
  const [cls, setCls] = useState("");
  const [seg2, setSeg2] = useState("");
  const [seg3, setSeg3] = useState("");

  const rows = (data && !data.note) ? data.products
    .map((p, i) => ({ p, i }))
    .filter(({ p }) => (!cls || p.bom_class === cls) && (!q || p.name.toLowerCase().includes(q.toLowerCase()))
      && (!seg2 || p.segment2 === seg2) && (!seg3 || p.segment3 === seg3)) : [];
  const pg = usePagination(rows, [q, cls, seg2, seg3]);

  if (loading) return <Loading what="Supply cards" />;
  if (error) return <ErrorBox msg={error} />;
  if (data.note) return <div className="banner info">{data.note}</div>;

  const pjc = data.planning_jc || 4;
  const seg2opts = [...new Set(data.products.map((p) => p.segment2).filter(Boolean))].sort();
  const seg3opts = [...new Set(data.products.filter((p) => !seg2 || p.segment2 === seg2).map((p) => p.segment3).filter(Boolean))].sort();

  return (
    <section className="supply-page">
      <div className="banner supply-context" style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", fontSize: 13 }}>
        <span style={{ fontWeight: 600 }}>🗂 Supply & RM Plan — Cards <span className="chip" style={{ background: "#EEF4FF", borderColor: "#CFE0FB", color: "#3060c0" }}>beta</span></span>
        <span style={{ color: "var(--muted)" }}>Each product is a card — tap to expand its demand build-up and raw-material plan.</span>
        <span style={{ marginLeft: "auto" }}>📅 <b>Planning JC{pjc}</b></span>
      </div>

      <div className="card supply-tool-card" style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", marginBottom: 14 }}>
        <SmoothInput className="searchbox" placeholder="Search product…" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 240 }} />
        <SegTabs value={cls} onChange={setCls} tabs={[
          { id: "", label: "All" },
          { id: "manufacturing", label: "MFG" },
          { id: "repack_relabel", label: "Repack" },
          { id: "trading", label: "Trading" },
        ]} />
        <SelectBox className="searchbox" style={{ maxWidth: 200 }} value={seg2} onChange={(e) => { setSeg2(e.target.value); setSeg3(""); }}>
          <option value="">All Segment 2</option>
          {seg2opts.map((o) => <option key={o} value={o}>{o}</option>)}
        </SelectBox>
        <SelectBox className="searchbox" style={{ maxWidth: 200 }} value={seg3} onChange={(e) => setSeg3(e.target.value)} disabled={!seg2}>
          <option value="">All Segment 3</option>
          {seg3opts.map((o) => <option key={o} value={o}>{o}</option>)}
        </SelectBox>
        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--muted)" }}>{rows.length} products</span>
      </div>

      <div className="sc-grid">
        {pg.pageRows.map(({ p, i }) => <ProductCard key={i} p={p} data={data} pjc={pjc} />)}
      </div>
      <Pagination {...pg} />
    </section>
  );
}

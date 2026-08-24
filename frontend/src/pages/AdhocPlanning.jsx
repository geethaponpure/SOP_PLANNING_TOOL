import React, { useState } from "react";
import { api, fmt } from "../api";
import { useAsync, Loading, ErrorBox, Tag, Stat } from "../components/ui.jsx";

const NetCell = ({ v }) => <span className={v > 0 ? "num-pos" : "num-zero"}>{fmt.num(v)}</span>;

// A clean segmented-control tab strip (replaces the plain underlined link tabs).
function SegTabs({ tabs, value, onChange }) {
  return (
    <div style={{ display: "inline-flex", background: "#eef2f7", border: "1px solid var(--border)",
      borderRadius: 10, padding: 3, gap: 2 }}>
      {tabs.map((t) => {
        const active = value === t.id;
        return (
          <button key={t.id} onClick={() => onChange(t.id)} title={t.title || ""}
            style={{ border: "none", cursor: "pointer", borderRadius: 7, whiteSpace: "nowrap",
              padding: "7px 15px", fontSize: 13, fontWeight: active ? 700 : 500,
              background: active ? "#fff" : "transparent", color: active ? "var(--navy)" : "var(--muted)",
              boxShadow: active ? "0 1px 3px rgba(15,23,42,.14)" : "none", transition: "all .12s" }}>
            {t.label}
          </button>
        );
      })}
    </div>
  );
}

const STATUS = {
  exceeds: { label: "Exceeds", bg: "#FFE5E5", color: "#a11" },
  new: { label: "New", bg: "#EAF3FF", color: "#1768c4" },
  covered: { label: "Covered", bg: "#E6F6EC", color: "#1a7d4f" },
};
const StatusTag = ({ s }) => {
  const m = STATUS[s] || STATUS.covered;
  return <span className="chip" style={{ cursor: "default", fontWeight: 600, background: m.bg, color: m.color, borderColor: "transparent" }}>{m.label}</span>;
};

function applySort(rows, { key, dir }) {
  if (!key) return rows;
  const out = [...rows].sort((a, b) => {
    const va = a[key], vb = b[key];
    if (typeof va === "number" && typeof vb === "number") return va - vb;
    if (typeof va === "boolean" && typeof vb === "boolean") return (va ? 1 : 0) - (vb ? 1 : 0);
    return String(va ?? "").localeCompare(String(vb ?? ""));
  });
  return dir === "desc" ? out.reverse() : out;
}

function SortTh({ label, k, sort, setSort, className }) {
  const active = sort.key === k;
  return (
    <th className={className} style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}
      title="Click to sort"
      onClick={() => setSort((s) => (s.key === k ? { key: k, dir: s.dir === "asc" ? "desc" : "asc" } : { key: k, dir: "desc" }))}>
      {label}<span style={{ color: active ? "inherit" : "var(--border)", fontSize: 10 }}>{active ? (sort.dir === "asc" ? " ▲" : " ▼") : " ⇅"}</span>
    </th>
  );
}

export default function AdhocPlanning() {
  const [planId, setPlanId] = useState("");
  const { data, loading, error } = useAsync(() => api.adhocPlanning(planId || null), [planId]);
  const [mode, setMode] = useState("product");
  const [q, setQ] = useState("");
  const [onlyAdhoc, setOnlyAdhoc] = useState(true);
  const [seg2, setSeg2] = useState("");
  const [seg3, setSeg3] = useState("");
  const [open, setOpen] = useState(null);
  const [sortP, setSortP] = useState({ key: "adhoc_qty", dir: "desc" });
  const [sortC, setSortC] = useState({ key: "net_to_buy", dir: "desc" });
  const [exporting, setExporting] = useState(false);
  const [running, setRunning] = useState(false);
  if (loading) return <Loading what="adhoc planning — reading post-freeze SOC + projection + pending SOC (first load ~40s)" />;
  if (error) return <ErrorBox msg={error} />;
  if (data.note) return <div className="banner info">{data.note}</div>;

  const s = data.summary;
  const fz = data.freeze || {};
  const plans = data.jc_plans || [];
  const ql = q.toLowerCase();
  const seg2opts = [...new Set(data.products.map((p) => p.segment2).filter(Boolean))].sort();
  const seg3opts = [...new Set(data.products
    .filter((p) => !seg2 || p.segment2 === seg2)
    .map((p) => p.segment3).filter(Boolean))].sort();
  const products = applySort(data.products.filter((p) =>
    (!onlyAdhoc || p.is_adhoc) && (!q || p.name.toLowerCase().includes(ql))
    && (!seg2 || p.segment2 === seg2) && (!seg3 || p.segment3 === seg3)), sortP);
  const cons = applySort(data.consolidated_rm.filter((r) =>
    !q || r.rm_code.toLowerCase().includes(ql) || (r.rm_desc || "").toLowerCase().includes(ql)), sortC);

  return (
    <>
      <div className="banner info page-intro">
        <b>Adhoc Planning.</b> Open SOC orders received <b>after the freeze date</b> (2nd day of the 3rd JC week) are
        validated against <b>Projected Qty</b> + <b>Pending SOC</b>: within projection → <b>Covered</b>; beyond projection + pending SOC →
        the excess is an <b>Exceeds</b> adhoc order; a new line item not in projection → <b>New</b>. Adhoc production is planned from the
        RM remaining <b>after deducting a saved JC Plan's allocation</b> (no duplicate RM allocation).
      </div>

      <div className="card" style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", padding: "12px 14px" }}>
        {[
          ["📅 Current JC", `JC${fz.jc} · ${fz.jc_from} → ${fz.jc_to} · FY ${fz.fy}`],
          ["🧾 Pending-SOC window", `${fz.pending_from} → ${fz.pending_to}`],
        ].map(([k, v], i) => (
          <span key={i} style={{ display: "inline-flex", gap: 6, alignItems: "baseline", background: "#F0F6FF",
            border: "1px solid #CFE0FB", borderRadius: 8, padding: "5px 11px", fontSize: 12.5 }}>
            <span style={{ color: "var(--muted)", fontWeight: 600 }}>{k}</span>
            <b style={{ color: "var(--navy)" }}>{v}</b>
          </span>
        ))}
        <label style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6, margin: 0, fontSize: 13 }}>
          Deduct RM from JC Plan:
          <select className="searchbox" style={{ maxWidth: 260 }} value={planId} onChange={(e) => setPlanId(e.target.value)} disabled={!data.mysql_ready}>
            <option value="">None (full stock)</option>
            {plans.map((p) => <option key={p.plan_id} value={p.plan_id}>#{p.plan_id} · JC{p.jc_number} · {p.plan_datetime} · RM {fmt.num(p.planned_rm_qty)}</option>)}
          </select>
        </label>
        <button className="btn" disabled={running || !data.mysql_ready}
          title="Run adhoc evaluation and log each item to ADHOC_EVALUATION"
          onClick={async () => { setRunning(true); try { const r = await api.adhocPlanningRun(planId || null); alert(`✓ Adhoc evaluation logged: ${r.logged?.written ?? 0} items to ADHOC_EVALUATION.`); } catch (e) { alert(e.message); } finally { setRunning(false); } }}>
          {running ? "Running…" : "▶ Run & Log"}
        </button>
      </div>
      {!data.mysql_ready && <div className="banner warn">MySQL store not ready — JC-plan deduction & logging need <code>backend/db/migrate_adhoc.sql</code> (run as root). Adhoc still evaluates on full stock.</div>}

      <div className="grid cols-4">
        <div className="card statcard"><div className="ic">🧾</div><Stat value={fmt.num(s.soc_items)} label="Post-freeze SOC items" /></div>
        <div className="card statcard red"><div className="ic">⬆️</div><Stat value={fmt.num(s.exceeds)} label="Exceeds (excess adhoc)" /></div>
        <div className="card statcard blue"><div className="ic">🆕</div><Stat value={fmt.num(s.new)} label="New line items" /></div>
        <div className="card statcard amber"><div className="ic">🛒</div><Stat value={`${fmt.num(s.rms_to_buy)} · ${fmt.num(s.total_buy_qty)}`} label="Adhoc RMs to buy · KG" /></div>
      </div>

      <div style={{ margin: "16px 0 8px" }}>
        <SegTabs value={mode}
          onChange={(m) => { setMode(m); setQ(""); if (m !== "product") { setSeg2(""); setSeg3(""); } }}
          tabs={[{ id: "product", label: "By SOC item" }, { id: "consolidated", label: "Consolidated RM (adhoc)" }]} />
      </div>

      <div className="pagebar">
        <input className="searchbox" placeholder={mode === "product" ? "Search item…" : "Search RM…"} value={q} onChange={(e) => setQ(e.target.value)} />
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
          <label style={{ display: "flex", alignItems: "center", gap: 6, margin: 0, fontSize: 13 }}>
            <input type="checkbox" style={{ width: "auto" }} checked={onlyAdhoc} onChange={(e) => setOnlyAdhoc(e.target.checked)} />
            Adhoc only (exclude covered)
          </label>
        )}
        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--muted)" }}>
          {mode === "product" ? `${products.length} items` : `${cons.length} RMs`}
        </span>
        <button className="btn" disabled={exporting}
          onClick={async () => { setExporting(true); try { await api.adhocPlanningExport(planId || null); } catch (e) { alert(e.message); } finally { setExporting(false); } }}>
          {exporting ? "Exporting…" : "⤓ Download (Excel)"}
        </button>
      </div>

      {mode === "consolidated" ? (
        <div className="tbl-wrap">
          <table>
            <thead><tr>
              <SortTh label="Raw material" k="rm_desc" sort={sortC} setSort={setSortC} />
              <SortTh label="#Items" k="item_count" sort={sortC} setSort={setSortC} className="num" />
              <SortTh label="Gross (KG)" k="gross" sort={sortC} setSort={setSortC} className="num" />
              <SortTh label="Stock" k="main_stock" sort={sortC} setSort={setSortC} className="num" />
              <SortTh label="Sub stk" k="substitute_stock" sort={sortC} setSort={setSortC} className="num" />
              <SortTh label="Available" k="available" sort={sortC} setSort={setSortC} className="num" />
              <SortTh label="Net to buy" k="net_to_buy" sort={sortC} setSort={setSortC} className="num" />
            </tr></thead>
            <tbody>
              {cons.map((r, i) => (
                <tr key={i}>
                  <td><b>{data.decode_names ? r.rm_desc : r.rm_code}</b><div style={{ fontSize: 11, color: "var(--muted)" }}>{data.decode_names ? r.rm_code : r.rm_desc}</div></td>
                  <td className="num">{r.item_count}</td>
                  <td className="num">{fmt.num(r.gross)}</td>
                  <td className="num">{fmt.num(r.main_stock)}</td>
                  <td className="num">{fmt.num(r.substitute_stock)}</td>
                  <td className="num">{fmt.num(r.available)}</td>
                  <td className="num"><b><NetCell v={r.net_to_buy} /></b></td>
                </tr>
              ))}
              {cons.length === 0 && <tr><td colSpan={7}>No adhoc RM to buy.</td></tr>}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="tbl-wrap">
          <table>
            <thead><tr>
              <th style={{ width: 24 }}></th>
              <SortTh label="SOC item" k="name" sort={sortP} setSort={setSortP} />
              <SortTh label="Status" k="status" sort={sortP} setSort={setSortP} />
              <SortTh label="Order qty" k="soc_qty" sort={sortP} setSort={setSortP} className="num" />
              <SortTh label="Projected" k="projected_qty" sort={sortP} setSort={setSortP} className="num" />
              <SortTh label="Pending SOC" k="pending_soc_qty" sort={sortP} setSort={setSortP} className="num" />
              <SortTh label="Adhoc qty" k="adhoc_qty" sort={sortP} setSort={setSortP} className="num" />
              <th>BOM</th>
              <SortTh label="RM to buy" k="net_total" sort={sortP} setSort={setSortP} className="num" />
            </tr></thead>
            <tbody>
              {products.map((p, i) => {
                const isOpen = open === p.name;
                const accent = p.status === "exceeds" ? "#e05353" : p.status === "new" ? "#1768c4" : "transparent";
                return (
                  <React.Fragment key={p.name + i}>
                    <tr className={`parent ${isOpen ? "isopen" : ""}`} style={{ cursor: p.has_bom && p.is_adhoc ? "pointer" : "default" }} onClick={() => p.has_bom && p.is_adhoc && setOpen(isOpen ? null : p.name)}>
                      <td style={{ color: "var(--muted)", borderLeft: `3px solid ${accent}` }}>{p.has_bom && p.is_adhoc ? (isOpen ? "▾" : "▸") : ""}</td>
                      <td><b>{p.name}</b></td>
                      <td><StatusTag s={p.status} /></td>
                      <td className="num">{fmt.num(p.soc_qty)}</td>
                      <td className="num">{fmt.num(p.projected_qty)}</td>
                      <td className="num">{fmt.num(p.pending_soc_qty)}</td>
                      <td className="num" style={{ fontWeight: 600 }}>{fmt.num(p.adhoc_qty)}</td>
                      <td>{p.has_bom ? <Tag kind="none">Yes</Tag> : <Tag kind="light">traded</Tag>}</td>
                      <td className="num"><b><NetCell v={p.net_total} /></b></td>
                    </tr>
                    {isOpen && p.components.length > 0 && (
                      <tr className="expander"><td></td><td colSpan={8}>
                        <table className="subtable">
                          <thead><tr><th>RM (main) + substitutes</th><th className="num">Qty/unit</th><th className="num">Gross</th>
                            <th className="num">Main stk</th><th className="num">Sub stk</th><th className="num">Available</th><th className="num">Net to buy</th></tr></thead>
                          <tbody>
                            {p.components.map((c, k) => (
                              <tr key={k}>
                                <td><b>{data.decode_names ? c.rm_desc : c.rm_code}</b>
                                  <div style={{ fontSize: 11, color: "var(--muted)" }}>{data.decode_names ? c.rm_code : c.rm_desc}
                                    {c.substitutes.length > 0 && <> · subs: {c.substitutes.map((su) => `${su.code}(${fmt.num(su.stock)})`).join(", ")}</>}</div></td>
                                <td className="num">{c.qty_per_unit}</td>
                                <td className="num">{fmt.num(c.gross)}</td>
                                <td className="num">{fmt.num(c.main_stock)}</td>
                                <td className="num">{fmt.num(c.substitute_stock)}</td>
                                <td className="num">{fmt.num(c.available)}</td>
                                <td className="num"><b><NetCell v={c.net_to_buy} /></b></td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </td></tr>
                    )}
                  </React.Fragment>
                );
              })}
              {products.length === 0 && <tr><td colSpan={9}>No items.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      <div className="sub" style={{ marginTop: 8 }}>{s.adhoc_items} adhoc items ({fmt.num(s.adhoc_soc_qty)} KG SOC); consolidated to {s.consolidated_rms} RMs, {s.rms_to_buy} to buy.</div>
    </>
  );
}

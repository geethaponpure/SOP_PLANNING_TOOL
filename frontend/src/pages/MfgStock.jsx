import React, { useState } from "react";
import { api, fmt } from "../api";
import { useAsync, Loading, ErrorBox, Stat } from "../components/ui.jsx";

function applySort(rows, { key, dir }) {
  if (!key) return rows;
  const out = [...rows].sort((a, b) => {
    const va = a[key], vb = b[key];
    if (typeof va === "number" && typeof vb === "number") return va - vb;
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

export default function MfgStock() {
  const { data, loading, error } = useAsync(api.mfgStock);
  const [q, setQ] = useState("");
  const [org, setOrg] = useState("");
  const [seg, setSeg] = useState("");
  const [div, setDiv] = useState("");
  const [sort, setSort] = useState({ key: "qty", dir: "desc" });
  if (loading) return <Loading what="MFG-org stock — reading CRM stock (first load ~30–60s)" />;
  if (error) return <ErrorBox msg={error} />;

  const s = data.summary || {};
  const ql = q.toLowerCase();
  const rows = applySort((data.rows || []).filter((r) =>
    (!org || r.org === org) && (!seg || r.segment2 === seg) && (!div || r.division === div) &&
    (!q || (r.item_code || "").toLowerCase().includes(ql) || (r.item_desc || "").toLowerCase().includes(ql))
  ), sort);
  const shownQty = rows.reduce((a, r) => a + (r.qty || 0), 0);

  return (
    <>
      <div className="banner info page-intro">
        <b>MFG-Org Stock.</b> On-hand stock at the <b>manufacturing organizations</b> (org names containing “MFG/Mfg”), read live from
        CRM (<code>SPBiStockDetails</code>), aggregated per <b>item × org</b> and tagged with <b>Division</b> &amp; <b>Segment</b>.
        Restricted to the <b>Performance Chemicals</b> and <b>NPD</b> divisions. Excluded sub-inventories and <b>DM-water</b> codes are
        removed. Filter by Division / Org / Segment, search, and click any column to sort.
      </div>

      <div className="grid cols-4">
        <div className="card statcard"><div className="ic">📦</div><Stat value={fmt.num(s.items)} label="Distinct items" /></div>
        <div className="card statcard"><div className="ic">🏭</div><Stat value={fmt.num(s.orgs)} label="MFG organizations" /></div>
        <div className="card statcard amber"><div className="ic">⚖️</div><Stat value={fmt.num(s.total_qty)} label="Total on-hand (KG)" /></div>
        <div className="card statcard"><div className="ic">🔎</div><Stat value={fmt.num(shownQty)} label="Shown qty (filtered, KG)" /></div>
      </div>

      <div className="pagebar" style={{ marginTop: 14 }}>
        <input className="searchbox" placeholder="Search item code / description…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="searchbox" style={{ maxWidth: 220 }} value={div} onChange={(e) => setDiv(e.target.value)}>
          <option value="">All divisions</option>
          {(data.divisions || []).map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        <select className="searchbox" style={{ maxWidth: 220 }} value={org} onChange={(e) => setOrg(e.target.value)}>
          <option value="">All MFG orgs</option>
          {(data.orgs || []).map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        <select className="searchbox" style={{ maxWidth: 200 }} value={seg} onChange={(e) => setSeg(e.target.value)}>
          <option value="">All segments</option>
          {(data.segments || []).map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--muted)" }}>{rows.length} rows</span>
      </div>

      <div className="tbl-wrap">
        <table>
          <thead>
            <tr>
              <SortTh label="Item Code" k="item_code" sort={sort} setSort={setSort} />
              <SortTh label="Item Description" k="item_desc" sort={sort} setSort={setSort} />
              <SortTh label="Division" k="division" sort={sort} setSort={setSort} />
              <SortTh label="Segment" k="segment2" sort={sort} setSort={setSort} />
              <SortTh label="Sub-segment" k="segment3" sort={sort} setSort={setSort} />
              <SortTh label="Organisation" k="org" sort={sort} setSort={setSort} />
              <SortTh label="Qty (KG)" k="qty" sort={sort} setSort={setSort} className="num" />
              <SortTh label="Age (d)" k="age_days" sort={sort} setSort={setSort} className="num" />
              <SortTh label="#Lots" k="lots" sort={sort} setSort={setSort} className="num" />
            </tr>
          </thead>
          <tbody>
            {rows.map((r, k) => (
              <tr key={k}>
                <td style={{ fontSize: 12 }}>{r.item_code}</td>
                <td><b>{r.item_desc}</b></td>
                <td>{r.division && <span className="chip" style={{ cursor: "default", fontSize: 10, background: /npd/i.test(r.division) ? "#FFF3E8" : "#EEF6FF" }}>{r.division}</span>}</td>
                <td>{r.segment2 && <span className="chip" style={{ cursor: "default", fontSize: 10, background: /raw material/i.test(r.segment2) ? "#EEF6FF" : "#F3F0E8" }}>{r.segment2}</span>}</td>
                <td style={{ fontSize: 12, color: "var(--muted)" }}>{r.segment3 || "—"}</td>
                <td style={{ fontSize: 12 }}>{r.org}</td>
                <td className="num"><b>{fmt.num(r.qty)}</b></td>
                <td className="num" style={{ color: r.age_days >= 180 ? "var(--red)" : r.age_days >= 90 ? "#8a6d00" : "var(--muted)" }}>{fmt.num(r.age_days)}</td>
                <td className="num">{fmt.num(r.lots)}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={9}>No stock matches the filters.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="sub" style={{ marginTop: 8 }}>
        Qty is summed across sub-inventories &amp; lots per item × org. Age = oldest lot age (days).
        Stock source: <b>{s.stock_source}</b>.
      </div>
    </>
  );
}

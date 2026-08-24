import React, { useState } from "react";
import { api, fmt } from "../api";
import { useAsync, Loading, ErrorBox, Stat } from "../components/ui.jsx";

function SegTabs({ tabs, value, onChange }) {
  return (
    <div style={{ display: "inline-flex", background: "#eef2f7", border: "1px solid var(--border)",
      borderRadius: 10, padding: 3, gap: 2, flexWrap: "wrap" }}>
      {tabs.map((t) => {
        const active = value === t.id;
        return (
          <button key={t.id} onClick={() => onChange(t.id)}
            style={{ border: "none", cursor: "pointer", borderRadius: 7, whiteSpace: "nowrap",
              padding: "7px 14px", fontSize: 13, fontWeight: active ? 700 : 500,
              background: active ? "#fff" : "transparent", color: active ? "var(--navy)" : "var(--muted)",
              boxShadow: active ? "0 1px 3px rgba(15,23,42,.14)" : "none", transition: "all .12s" }}>
            {t.label}{t.n != null ? ` (${fmt.num(t.n)})` : ""}
          </button>
        );
      })}
    </div>
  );
}

function SortTh({ label, k, sort, setSort, className, title }) {
  const active = sort.key === k;
  return (
    <th className={className} title={title || "Click to sort"} style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}
      onClick={() => setSort((s) => (s.key === k ? { key: k, dir: s.dir === "asc" ? "desc" : "asc" } : { key: k, dir: "desc" }))}>
      {label}<span style={{ color: active ? "inherit" : "var(--border)", fontSize: 10 }}>{active ? (sort.dir === "asc" ? " ▲" : " ▼") : " ⇅"}</span>
    </th>
  );
}

const ACT_BG = {
  Manufacturing: { bg: "#E3F3E8", c: "#1a7d4f" }, "Repack/Relabel": { bg: "#EAF1FF", c: "#1768c4" },
  Trading: { bg: "#FDECEF", c: "#b03052" }, Other: { bg: "#F3F0E8", c: "#8a6d00" },
};

export default function MSL() {
  const [ver, setVer] = useState(0);
  const [ref, setRef] = useState("");            // "" = live current window
  const { data, loading, error } = useAsync(() => api.msl(ref || undefined), [ver, ref]);
  const snaps = useAsync(() => api.mslSnapshots(), [ver]);
  const [tab, setTab] = useState("all");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState({ key: "avg_qty_per_jc", dir: "desc" });
  const [busy, setBusy] = useState("");

  if (error) return <ErrorBox msg={error} />;
  const meta = data?.meta || {};
  const all = data?.rows || [];
  const st = data?.storage || {};
  const byAct = meta.summary?.by_activity || {};

  const ql = q.trim().toLowerCase();
  const rows = [...all]
    .filter((r) => (tab === "all" || r.activity === tab)
      && (!ql || (r.item_name || "").toLowerCase().includes(ql) || (r.item_code || "").toLowerCase().includes(ql)))
    .sort((a, b) => {
      const va = a[sort.key], vb = b[sort.key];
      const c = typeof va === "number" ? va - vb : String(va ?? "").localeCompare(String(vb ?? ""));
      return sort.dir === "desc" ? -c : c;
    });

  const save = async () => {
    setBusy("save");
    try { const r = await api.mslSave(); alert(`✓ MSL snapshot saved — ${r.reference} (${fmt.num(r.n_items)} items).`); setVer((v) => v + 1); }
    catch (e) { alert(e.message); } finally { setBusy(""); }
  };
  const dl = async () => {
    setBusy("dl");
    try { await api.mslExport(ref || undefined); } catch (e) { alert(e.message); } finally { setBusy(""); }
  };

  return (
    <>
      <div className="banner info page-intro">
        <b>MSL — Minimum Stock Level (finished products).</b> Computed from the latest <b>13 JCs</b> (one year) of
        dispatch (CRM <code>FnDespatchDetails</code>); the window slides forward as each JC completes. Per item:
        the <b>average one-JC sales</b>, the <b>movement frequency</b> (how many of the 13 JCs had dispatch), and the
        <b> customer coverage</b> (unique customers served). <b>MSL = 50% of the average one-JC sales.</b> Only items served by
        <b> more than {meta.summary?.min_customers ?? 5} customers</b> and that <b>moved in more than {meta.summary?.min_freq ?? 10} of the 13 JCs</b> qualify.
        Current <b>on-hand stock</b> (Warehouse / Branch) is shown alongside. Split by activity — <b>Manufacturing / Trading / Repack-Relabel</b>.
        Save a snapshot per JC (e.g. <code>{meta.reference}</code>).
      </div>

      <div className="banner" style={{ background: "#EAF4FF", border: "1px solid #BBD9F5", display: "flex", flexWrap: "wrap", gap: 16, alignItems: "center", fontSize: 13 }}>
        <span>📅 <b>{meta.jc_label || "—"}</b> · {meta.n_jcs || 13}-JC window <b>{meta.jc_from} → {meta.jc_to}</b> · FY {meta.fy}</span>
        <span>🏷 <b>Reference:</b> <code>{meta.reference}</code></span>
        {ref && <span style={{ color: "#8a6d00" }}>viewing saved snapshot</span>}
        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--muted)" }}>
          Storage: <b>{st.backend === "mysql" ? "MySQL" : "JSON fallback"}</b>
          {!st.db_ready && <> — run <code>{st.migration}</code> for DB</>}
        </span>
      </div>

      <div className="grid cols-4">
        <div className="card statcard"><div className="ic">📦</div><Stat value={fmt.num(meta.summary?.items)} label="Finished products (moved)" /></div>
        <div className="card statcard blue"><div className="ic">🏭</div><Stat value={fmt.num(byAct.Manufacturing || 0)} label="Manufacturing items" /></div>
        <div className="card statcard"><div className="ic">🔁</div><Stat value={`${fmt.num(byAct.Trading || 0)} / ${fmt.num(byAct["Repack/Relabel"] || 0)}`} label="Trading / Repack-Relabel" /></div>
        <div className="card statcard amber"><div className="ic">📉</div><Stat value={fmt.num(meta.summary?.total_msl)} label="Total MSL (KG)" /></div>
      </div>

      <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 10, margin: "16px 0 8px" }}>
        <SegTabs value={tab} onChange={setTab} tabs={[
          { id: "all", label: "All finished", n: meta.summary?.items },
          { id: "Manufacturing", label: "Manufacturing", n: byAct.Manufacturing || 0 },
          { id: "Trading", label: "Trading", n: byAct.Trading || 0 },
          { id: "Repack/Relabel", label: "Repack/Relabel", n: byAct["Repack/Relabel"] || 0 },
        ]} />
        <div style={{ display: "flex", gap: 8, marginLeft: "auto", flexWrap: "wrap", alignItems: "center" }}>
          {snaps.data?.snapshots?.length > 0 && (
            <select className="searchbox" style={{ maxWidth: 240, fontSize: 12 }} value={ref} onChange={(e) => setRef(e.target.value)}>
              <option value="">● Live (current window)</option>
              {snaps.data.snapshots.map((s) => (
                <option key={s.reference} value={s.reference}>{s.reference} · {s.jc_to} · {fmt.num(s.n_items)} items</option>
              ))}
            </select>
          )}
          {!ref && <button className="btn secondary" disabled={busy === "save"} onClick={save} title="Store this MSL snapshot in the database">{busy === "save" ? "Saving…" : "💾 Save snapshot"}</button>}
          <button className="btn" disabled={busy === "dl"} onClick={dl}>{busy === "dl" ? "Exporting…" : "⤓ Download (Excel)"}</button>
        </div>
      </div>

      <div className="pagebar">
        <input className="searchbox" placeholder="Search item name / code…" value={q} onChange={(e) => setQ(e.target.value)} />
        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--muted)" }}>{fmt.num(rows.length)} items · sorted by {sort.key}</span>
      </div>

      {loading && <Loading what="MSL (reading 13-JC dispatch — first load ~2 min)" />}
      {data && (
        <div className="tbl-wrap">
          <table style={{ width: "100%", tableLayout: "fixed", fontSize: 12 }}>
            <colgroup>
              <col />
              <col style={{ width: "8.5%" }} />
              <col style={{ width: "10%" }} />
              <col style={{ width: "8%" }} />
              <col style={{ width: "5.5%" }} />
              <col style={{ width: "8%" }} />
              <col style={{ width: "8%" }} />
              <col style={{ width: "8%" }} />
              <col style={{ width: "8%" }} />
              <col style={{ width: "8%" }} />
              <col style={{ width: "8%" }} />
            </colgroup>
            <thead><tr>
              <SortTh label="Item Name" k="item_name" sort={sort} setSort={setSort} />
              <SortTh label="Activity" k="activity" sort={sort} setSort={setSort} />
              <SortTh label="Business" k="business" sort={sort} setSort={setSort} />
              <SortTh label="Avg Qty / JC" k="avg_qty_per_jc" sort={sort} setSort={setSort} className="num" title="Average one-JC sales over 13 JCs" />
              <SortTh label="Freq" k="freq_jcs" sort={sort} setSort={setSort} className="num" title="How many of the 13 JCs had dispatch" />
              <SortTh label="Customers" k="customer_coverage" sort={sort} setSort={setSort} className="num" title="Unique customers served over 13 JCs" />
              <SortTh label="Total Qty" k="total_qty" sort={sort} setSort={setSort} className="num" title="Total dispatch over the 13 JCs" />
              <SortTh label="MSL" k="msl" sort={sort} setSort={setSort} className="num" title="MSL = 50% of average one-JC sales" />
              <SortTh label="WH Stock" k="warehouse_stock" sort={sort} setSort={setSort} className="num" title="Current on-hand at warehouse orgs" />
              <SortTh label="Branch" k="branch_stock" sort={sort} setSort={setSort} className="num" title="Current on-hand at branches" />
              <SortTh label="On-hand" k="onhand_stock" sort={sort} setSort={setSort} className="num" title="Total on-hand (warehouse + branch)" />
            </tr></thead>
            <tbody>
              {rows.slice(0, 3000).map((r, i) => {
                const a = ACT_BG[r.activity] || ACT_BG.Other;
                return (
                  <tr key={(r.item_name || "") + i}>
                    <td style={{ whiteSpace: "normal", wordBreak: "break-word" }} title={r.item_codes?.join(", ")}>
                      <b>{r.item_name}</b>
                      {r.code_count > 1 && <span style={{ color: "var(--muted)", fontSize: 10, fontWeight: 500 }}> · {r.code_count} codes</span>}
                    </td>
                    <td><span className="chip" style={{ cursor: "default", fontSize: 10, fontWeight: 700, background: a.bg, color: a.c, borderColor: "transparent" }}>{r.activity}</span></td>
                    <td style={{ fontSize: 11, color: "var(--muted)", whiteSpace: "normal", wordBreak: "break-word" }}>{r.business || "—"}</td>
                    <td className="num"><b>{fmt.num(r.avg_qty_per_jc)}</b></td>
                    <td className="num" style={{ color: r.freq_jcs >= 10 ? "#1a7d4f" : r.freq_jcs >= 5 ? "#8a6d00" : "var(--muted)" }}>{r.freq_jcs}/{meta.n_jcs || 13}</td>
                    <td className="num">{fmt.num(r.customer_coverage)}</td>
                    <td className="num">{fmt.num(r.total_qty)}</td>
                    <td className="num" style={{ fontWeight: 700, color: "#b23b3b" }}>{fmt.num(r.msl)}</td>
                    <td className="num">{fmt.num(r.warehouse_stock)}</td>
                    <td className="num">{fmt.num(r.branch_stock)}</td>
                    <td className="num" style={{ fontWeight: 600, color: (r.onhand_stock || 0) < (r.msl || 0) ? "#b23b3b" : "#1a7d4f" }}>{fmt.num(r.onhand_stock)}</td>
                  </tr>
                );
              })}
              {rows.length === 0 && <tr><td colSpan={11} style={{ color: "var(--muted)" }}>No items match.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      {rows.length > 3000 && <div className="sub" style={{ marginTop: 8 }}>Showing first 3,000 of {fmt.num(rows.length)} — use search or download the full Excel.</div>}
    </>
  );
}

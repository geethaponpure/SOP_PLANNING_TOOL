import React, { useEffect, useMemo, useRef, useState } from "react";
import Gantt from "frappe-gantt";
// the package exports map has no CSS subpath, so reach the built stylesheet directly
import "../../node_modules/frappe-gantt/dist/frappe-gantt.css";
import { fmt } from "../api";

// Production calendar rendered with frappe-gantt (MIT). One row per scheduled job,
// grouped by equipment through the sort order; the bar colour encodes the priority
// and a dashed outline means the RM only arrives via lead time.
const fmtD = (s) => new Date(s + "T00:00:00").toLocaleDateString("en-GB", { day: "2-digit", month: "short" });

export default function ProductionGantt({ jobs, view, today }) {
  const elRef = useRef(null);
  const [err, setErr] = useState(null);

  const tasks = useMemo(() => {
    const sorted = [...jobs].sort((a, b) =>
      a.equipment === b.equipment ? (a.start < b.start ? -1 : 1) : String(a.equipment).localeCompare(String(b.equipment)));
    return sorted.map((j, i) => ({
      id: "j" + i,
      name: `${j.equipment} · ${j.item}`,
      start: j.start,
      end: j.end,
      progress: 0,
      // one token only — frappe does classList.add(custom_class)
      custom_class: "p" + j.priority + (j.rm_available ? "" : "-lead"),
      _job: j,
    }));
  }, [jobs]);

  useEffect(() => {
    const el = elRef.current;
    if (!el || !tasks.length) return;
    el.innerHTML = "";                      // frappe appends; clear before re-drawing
    try {
      // eslint-disable-next-line no-new
      new Gantt(el, tasks, {
        view_mode: view === "week" ? "Day" : "Week",
        date_format: "YYYY-MM-DD",
        bar_height: 20,
        padding: 12,
        readonly: true,
        today_button: false,
        view_mode_select: false,
        popup: (ctx) => {
          const j = ctx.task._job;
          if (!j) return;
          ctx.set_title(j.item);
          ctx.set_subtitle(`${j.equipment} · ${j.scenario} · Priority ${j.priority}`);
          ctx.set_details(
          `<b>${fmt.num(j.qty)} kg</b> · ${j.batches} batch(es) × ${fmt.num(j.batch_size)} kg · ${j.cycle_hrs}h/batch<br/>`
          + `${fmtD(j.start)} → ${fmtD(j.end)}<br/>`
          + (j.rm_available
            ? '<span style="color:#7ee2b8">RM in stock</span>'
            : `<span style="color:#ffb4ac">RM via lead time ${j.lead_days}d</span>`)
        );
        },
      });
    } catch (e) {
      // never let a third-party chart blank the page
      console.error("frappe-gantt failed to render:", e);
      el.innerHTML = "";
      setErr(e && (e.message || String(e)));
    }
    return () => { el.innerHTML = ""; };
  }, [tasks, view, today]);

  if (!tasks.length) return <div className="banner info">No scheduled jobs to plot.</div>;
  return (
    <>
      {err && <div className="banner warn">Calendar could not be drawn: {err}</div>}
      <div className="prod-gantt"><div ref={elRef} /></div>
    </>
  );
}

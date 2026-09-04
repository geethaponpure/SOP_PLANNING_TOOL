import React, { useCallback, useMemo, useState } from "react";
import { Responsive, WidthProvider } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";

// Draggable + resizable card grid for dashboard pages.
//
// Each child must carry a stable `key` — that key is the layout id, so a card
// keeps its place across reloads (and across cards being added or removed).
// The saved arrangement lives in localStorage per `storageKey`, per breakpoint.
//
// Dragging is by the card header; anything interactive (chart canvas, table,
// button, input) is excluded via draggableCancel so cross-filter clicks, table
// scrolling and the Chart/Table toggles keep working normally.

const RGL = WidthProvider(Responsive);
const COLS = { lg: 12, md: 8, sm: 4, xs: 2 };
const BREAKPOINTS = { lg: 1280, md: 960, sm: 680, xs: 0 };
const CANCEL = "button, a, input, select, textarea, canvas, table, .tbl-wrap, .searchbox";

const keyOf = (child) => String(child.key ?? "").replace(/^\.\$/, "");

// Cards are often grouped inside fragments (e.g. everything behind one `p &&`
// guard). Flatten those so each CARD — not the fragment — becomes a grid item.
function flatten(nodes) {
  const out = [];
  React.Children.forEach(nodes, (c) => {
    if (!c || typeof c !== "object") return;
    if (c.type === React.Fragment) out.push(...flatten(c.props.children));
    else out.push(c);
  });
  return out;
}

function buildLayout(ids, defaults, cols) {
  // place anything without a default underneath, full width
  let y = 0;
  return ids.map((id) => {
    const d = defaults[id] || {};
    const w = Math.min(d.w ?? cols, cols);
    const h = d.h ?? 8;
    const item = { i: id, x: d.x ?? 0, y: d.y ?? y, w, h, minW: 3, minH: 4 };
    y = Math.max(y, item.y + h);
    return item;
  });
}

export default function DashGrid({ storageKey, defaults = {}, children }) {
  const items = useMemo(() => flatten(children).filter((c) => c.key != null), [children]);
  const ids = useMemo(() => items.map(keyOf), [items]);

  const [layouts, setLayouts] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey));
      if (saved && typeof saved === "object") return saved;
    } catch { /* first visit, or storage blocked */ }
    return { lg: buildLayout(ids, defaults, COLS.lg) };
  });

  // keep the saved arrangement in step with the cards actually rendered:
  // drop layout entries for cards that disappeared, append newly added ones.
  const shown = useMemo(() => {
    const out = {};
    for (const [bp, arr] of Object.entries(layouts || {})) {
      const cols = COLS[bp] || COLS.lg;
      const known = new Map((arr || []).map((l) => [l.i, l]));
      const kept = ids.filter((id) => known.has(id)).map((id) => known.get(id));
      const missing = ids.filter((id) => !known.has(id));
      const nextY = kept.reduce((m, l) => Math.max(m, l.y + l.h), 0);
      out[bp] = [
        ...kept,
        ...buildLayout(missing, defaults, cols).map((l) => ({ ...l, y: l.y + nextY })),
      ];
    }
    return out;
  }, [layouts, ids, defaults]);

  const save = useCallback((_cur, all) => {
    setLayouts(all);
    try { localStorage.setItem(storageKey, JSON.stringify(all)); } catch { /* ignore */ }
  }, [storageKey]);

  const reset = useCallback(() => {
    const fresh = { lg: buildLayout(ids, defaults, COLS.lg) };
    setLayouts(fresh);
    try { localStorage.removeItem(storageKey); } catch { /* ignore */ }
  }, [ids, defaults, storageKey]);

  return (
    <>
      <div className="dash-grid-bar">
        <span>Drag a card by its header to move it · drag its bottom-right corner to resize</span>
        <button type="button" className="btn secondary" onClick={reset}>Reset layout</button>
      </div>
      <RGL
        className="dash-grid"
        layouts={shown}
        breakpoints={BREAKPOINTS}
        cols={COLS}
        rowHeight={30}
        margin={[14, 14]}
        containerPadding={[0, 0]}
        draggableCancel={CANCEL}
        onLayoutChange={save}
        measureBeforeMount
        useCSSTransforms
      >
        {items.map((child) => (
          <div key={keyOf(child)} className="dash-item">{child}</div>
        ))}
      </RGL>
    </>
  );
}

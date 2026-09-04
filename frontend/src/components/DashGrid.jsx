import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ResponsiveGridLayout, useContainerWidth } from "react-grid-layout";
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

export default function DashGrid({
  storageKey, defaults = {}, children,
  expanded = [],          // ids to blow up to the full row (e.g. a table view)
  remoteLayouts = null,   // the admin-saved default, once it arrives
  canSaveDefault = false,
  onSaveDefault,
}) {
  const items = useMemo(() => flatten(children).filter((c) => c.key != null), [children]);
  // `children` is a fresh array on every render, so key the id list by VALUE —
  // otherwise the memoised layout below would be rebuilt each render and the
  // grid could fight itself over positions.
  const idKey = items.map(keyOf).join("|");
  const ids = useMemo(() => idKey.split("|").filter(Boolean), [idKey]);

  // null = this user has not arranged the page, so fall back to the saved
  // default (or the built-in one) and keep following it.
  const [layouts, setLayouts] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey));
      if (saved && typeof saved === "object") return saved;
    } catch { /* first visit, or storage blocked */ }
    return null;
  });
  const [savedDefault, setSavedDefault] = useState("idle");
  // memoised so the fallback keeps a stable identity — a fresh object every
  // render would rebuild the layout and make the grid fight itself.
  const fallback = useMemo(() => ({ lg: buildLayout(ids, defaults, COLS.lg) }), [ids, defaults]);
  const base = layouts || remoteLayouts || fallback;

  const expKey = expanded.join("|");
  const expandedSet = useMemo(
    () => new Set(expKey ? expKey.split("|") : []), [expKey]);
  useEffect(() => { setSavedDefault("idle"); }, [expKey]);

  // keep the saved arrangement in step with the cards actually rendered:
  // drop layout entries for cards that disappeared, append newly added ones.
  const shown = useMemo(() => {
    const out = {};
    for (const [bp, arr] of Object.entries(base || {})) {
      const cols = COLS[bp] || COLS.lg;
      const known = new Map((arr || []).map((l) => [l.i, l]));
      const kept = ids.filter((id) => known.has(id)).map((id) => known.get(id));
      const missing = ids.filter((id) => !known.has(id));
      const nextY = kept.reduce((m, l) => Math.max(m, l.y + l.h), 0);
      out[bp] = [
        ...kept,
        ...buildLayout(missing, defaults, cols).map((l) => ({ ...l, y: l.y + nextY })),
      ].map((l) => (expandedSet.has(l.i)
        // a card switched to its table view takes the whole row, so the columns
        // are readable — the user's own size is restored when they switch back
        ? { ...l, x: 0, w: cols, h: Math.max(l.h, 13) }
        : l));
    }
    return out;
  }, [base, ids, defaults, expandedSet]);

  const save = useCallback((_cur, all) => {
    // never bake a temporary full-row expansion into the saved arrangement
    if (expandedSet.size) return;
    setLayouts(all);
    try { localStorage.setItem(storageKey, JSON.stringify(all)); } catch { /* ignore */ }
  }, [storageKey, expandedSet]);

  const saveDefault = useCallback(async () => {
    if (!onSaveDefault) return;
    setSavedDefault("saving");
    try {
      await onSaveDefault(base);
      setSavedDefault("saved");
    } catch {
      setSavedDefault("failed");
    }
  }, [onSaveDefault, base]);

  const reset = useCallback(() => {
    setLayouts(null);          // back to the saved default, else the built-in one
    try { localStorage.removeItem(storageKey); } catch { /* ignore */ }
  }, [storageKey]);

  // v2 measures its own container instead of the old WidthProvider HOC
  const { width, containerRef, mounted } = useContainerWidth({ measureBeforeMount: true });

  return (
    <>
      <div className="dash-grid-bar">
        <span>Drag a card by its header to move it · drag its bottom-right corner to resize</span>
        {canSaveDefault && (
          <button type="button" className="btn secondary" onClick={saveDefault}
            style={{ marginLeft: "auto" }}
            disabled={savedDefault === "saving" || expandedSet.size > 0}
            title={expandedSet.size
              ? "Switch the expanded card back to Chart first"
              : "Make this arrangement the default everyone starts from"}>
            {savedDefault === "saving" ? "Saving…"
              : savedDefault === "saved" ? "✓ Saved as default"
                : savedDefault === "failed" ? "Save failed — retry" : "Save as default"}
          </button>
        )}
        <button type="button" className="btn secondary" onClick={reset}
          style={canSaveDefault ? undefined : { marginLeft: "auto" }}>Reset layout</button>
      </div>
      <div ref={containerRef}>
      {mounted && (
      <ResponsiveGridLayout
        className="dash-grid"
        width={width}
        layouts={shown}
        breakpoints={BREAKPOINTS}
        cols={COLS}
        rowHeight={30}
        margin={[14, 14]}
        containerPadding={[0, 0]}
        draggableCancel={CANCEL}
        onLayoutChange={save}
        useCSSTransforms
      >
        {items.map((child) => (
          <div key={keyOf(child)} className="dash-item">{child}</div>
        ))}
      </ResponsiveGridLayout>
      )}
      </div>
    </>
  );
}

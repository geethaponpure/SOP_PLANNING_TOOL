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
  // Cards to blow up to the full row (e.g. a table view). Either a list of ids,
  // or {id: rowUnits} to also give the card a height that fits its content.
  expanded = [],
  // {newId: [oldId, ...]} — a card that replaced others takes over their slot
  // so a layout saved before the merge keeps its shape.
  renames = {},
  remoteLayouts = null,   // the app-level default, once it arrives
  userLayouts = null,     // this user's own saved arrangement, once it arrives
  canSaveDefault = false,
  onSaveDefault,          // (layouts) => save the app-level default  [admin]
  onSaveUser,             // (layouts) => save THIS user's own arrangement
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
  // The prop only carries what was on the server at PAGE LOAD. Keep it in state
  // so that saving a new default takes effect for "Reset layout" right away
  // instead of falling through to the built-in arrangement.
  const [serverDefault, setServerDefault] = useState(remoteLayouts);
  useEffect(() => { if (remoteLayouts) setServerDefault(remoteLayouts); }, [remoteLayouts]);
  // this user's own arrangement, adopted once it arrives from the server (their
  // local copy, if any, already won — it is the same thing, only faster)
  const [mine, setMine] = useState(userLayouts);
  useEffect(() => { if (userLayouts) setMine(userLayouts); }, [userLayouts]);
  // memoised so the fallback keeps a stable identity — a fresh object every
  // render would rebuild the layout and make the grid fight itself.
  const fallback = useMemo(() => ({ lg: buildLayout(ids, defaults, COLS.lg) }), [ids, defaults]);
  const base = layouts || mine || serverDefault || fallback;

  const expKey = Array.isArray(expanded) ? expanded.join("|") : JSON.stringify(expanded);
  const expandedH = useMemo(() => (Array.isArray(expanded)
    ? Object.fromEntries(expanded.map((id) => [id, 0]))
    : (expanded || {})), [expKey]);              // eslint-disable-line react-hooks/exhaustive-deps
  const expandedSet = useMemo(() => new Set(Object.keys(expandedH)), [expandedH]);
  useEffect(() => { setSavedDefault("idle"); }, [expKey]);

  // keep the saved arrangement in step with the cards actually rendered:
  // drop layout entries for cards that disappeared, append newly added ones.
  const shown = useMemo(() => {
    const out = {};
    for (const [bp, arr] of Object.entries(base || {})) {
      const cols = COLS[bp] || COLS.lg;
      const known = new Map((arr || []).map((l) => [l.i, l]));
      for (const [now, before] of Object.entries(renames)) {
        if (known.has(now)) continue;
        const from = (Array.isArray(before) ? before : [before]).find((b) => known.has(b));
        if (from) known.set(now, { ...known.get(from), i: now });
      }
      const kept = ids.filter((id) => known.has(id)).map((id) => known.get(id));
      const missing = ids.filter((id) => !known.has(id));
      const nextY = kept.reduce((m, l) => Math.max(m, l.y + l.h), 0);
      out[bp] = [
        ...kept,
        ...buildLayout(missing, defaults, cols).map((l) => ({ ...l, y: l.y + nextY })),
      ].map((l) => (expandedSet.has(l.i)
        // A card switched to its table view takes the whole row so the columns
        // are readable, and only as much HEIGHT as its rows need — forcing a
        // fixed height left a short table stranded in a mostly empty card.
        // The user's own size comes back when they switch to the chart.
        ? { ...l, x: 0, w: cols, h: expandedH[l.i] || Math.max(l.h, 13) }
        : l));
    }
    return out;
  }, [base, ids, defaults, expandedSet, renames]);

  const pending = React.useRef(null);
  const save = useCallback((_cur, all) => {
    // never bake a temporary full-row expansion into the saved arrangement
    if (expandedSet.size) return;
    setLayouts(all);
    setMine(all);
    try { localStorage.setItem(storageKey, JSON.stringify(all)); } catch { /* ignore */ }
    // persist to this user's own server slot so their layout follows them to
    // another browser or machine; debounced so a drag is one write, not thirty
    if (onSaveUser) {
      clearTimeout(pending.current);
      pending.current = setTimeout(() => { onSaveUser(all).catch(() => {}); }, 1200);
    }
  }, [storageKey, expandedSet, onSaveUser]);
  useEffect(() => () => clearTimeout(pending.current), []);

  const saveDefault = useCallback(async () => {
    if (!onSaveDefault) return;
    setSavedDefault("saving");
    try {
      await onSaveDefault(base);
      setServerDefault(base);      // Reset now returns to what was just saved
      setSavedDefault("saved");
    } catch {
      setSavedDefault("failed");
    }
  }, [onSaveDefault, base]);

  const reset = useCallback(() => {
    // drop only this user's layer — the app-level default shows through again
    clearTimeout(pending.current);
    setLayouts(null);
    setMine(null);
    try { localStorage.removeItem(storageKey); } catch { /* ignore */ }
    if (onSaveUser) onSaveUser({}).catch(() => {});
  }, [storageKey, onSaveUser]);

  // v2 measures its own container instead of the old WidthProvider HOC
  const { width, containerRef, mounted } = useContainerWidth({ measureBeforeMount: true });

  return (
    <>
      <div className="dash-grid-bar">
        <span>
          Drag a card by its header to move it · drag its bottom-right corner to resize ·
          your arrangement saves automatically
        </span>
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
          title="Discard your own arrangement and go back to the app default"
          style={canSaveDefault ? undefined : { marginLeft: "auto" }}>Reset to app default</button>
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

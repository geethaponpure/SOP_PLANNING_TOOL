import React, { useEffect, useLayoutEffect, useRef, useState } from "react";

// Shared segmented tab toggle with a sliding "glider" that animates under the
// active tab. The glider is positioned by MEASURING the active button (offsetLeft
// / offsetWidth) rather than assuming equal widths — so unequal-length labels
// never overlap. Replaces the per-page inline SegTabs copies.
//
//   <SegTabs value={mode} onChange={setMode}
//     tabs={[{ id, label, title?, icon? }, ...]} size="md" | "sm" />
const cx = (...p) => p.filter(Boolean).join(" ");

export default function SegTabs({ tabs, value, onChange, size = "md", className }) {
  const wrapRef = useRef(null);
  const btnRefs = useRef({});
  const [g, setG] = useState({ left: 0, width: 0, ready: false });

  const measure = () => {
    const el = btnRefs.current[value];
    if (el) setG({ left: el.offsetLeft, width: el.offsetWidth, ready: true });
  };

  // reposition when the selection or the tab set changes
  useLayoutEffect(measure, [value, tabs]);

  // ...and when the container resizes or web fonts finish loading (widths shift)
  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const ro = new ResizeObserver(measure);
    ro.observe(wrap);
    document.fonts?.ready?.then(measure).catch(() => {});
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, tabs]);

  return (
    <div ref={wrapRef} className={cx("segtabs", `segtabs-${size}`, className)} role="tablist">
      {g.ready && (
        <span className="segtabs-glider" aria-hidden style={{ left: g.left, width: g.width }} />
      )}
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          aria-selected={value === t.id}
          ref={(el) => { btnRefs.current[t.id] = el; }}
          className={cx("segtabs-tab", value === t.id && "active")}
          title={t.title || ""}
          onClick={() => onChange(t.id)}
        >
          {t.icon ? `${t.icon} ` : ""}{t.label}
        </button>
      ))}
    </div>
  );
}

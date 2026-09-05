// Render SupplyCompetition in every filter combination against a real payload,
// plus the item drill-down modal and the empty states.
import { build } from "esbuild";
import { createRequire } from "module";
import fs from "fs";
import path from "path";

const FE = "d:/Ritesh/sop-planning-tool/frontend";
const require = createRequire(path.join(FE, "package.json"));
const PAGE = path.join(FE, "src/pages/PromiseDates.jsx");

globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
globalThis.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k),
};

const payload = JSON.parse(fs.readFileSync(process.env.PAYLOAD_FILE, "utf8"));
const itemPayload = JSON.parse(fs.readFileSync(process.env.ITEM_FILE, "utf8"));
const layout = { layouts: null, user_layouts: null };

const SUBS = [
  ['const [statMetric, setStatMetric] = useState("status");', "sm"],
  ['const [riskMetric, setRiskMetric] = useState("risk");', "hm"],
  ['const [statView, setStatView] = useState("chart");', "sv"],
  ['const [riskView, setRiskView] = useState("chart");', "hv"],
];

function plugin(state) {
  return {
    name: "stub",
    setup(b) {
      b.onResolve({ filter: /components\/ui\.jsx$/ }, () => ({ path: "stub-ui", namespace: "stub" }));
      // react-dom's createPortal needs a real DOM; render the modal inline instead
      b.onResolve({ filter: /^react-grid-layout/ }, () => ({ path: "stub-rgl", namespace: "stub" }));
      b.onLoad({ filter: /.*/, namespace: "stub" }, (a) => ({
        loader: "jsx",
        contents: a.path === "stub-rgl"
          ? `import React from "react";
             export const Responsive = ({ children }) => <div>{children}</div>;
             export const ResponsiveGridLayout = ({ children }) => <div>{children}</div>;
             export const WidthProvider = (C) => C;
             export const useContainerWidth = () => ({ width: 1200, containerRef: { current: null }, mounted: true });
             export default Responsive;`
          : `import React from "react";
             const R = globalThis.__ASYNC__;
             let i = 0;
             export function useAsync() {
               const r = R[Math.min(i, R.length - 1)]; i += 1;
               return { data: r, loading: false, error: null, reload: () => {} };
             }
             export const Loading = () => <div>loading</div>;
             export const ErrorBox = ({ error }) => <div>err {String(error)}</div>;`,
      }));
      b.onLoad({ filter: /PromiseDates\.jsx$/ }, async () => {
        let src = await fs.promises.readFile(PAGE, "utf8");
        // createPortal needs a live DOM; render the modal inline for SSR
        src = src.replace('import { createPortal } from "react-dom";',
          "const createPortal = (children) => children;")
          .replace("</div>, document.body);", "</div>);");
        for (const [decl, kk] of SUBS) {
          if (!src.includes(decl)) throw new Error(`state declaration moved: ${decl}`);
          src = src.replace(decl, decl.replace(/useState\("[a-z]+"\)/, `useState("${state[kk]}")`));
        }
        if (state.pick) {
          // open the modal on the first exposed row
          src = src.replace("const [pick, setPick] = useState(null);",
            "const [pick, setPick] = useState((globalThis.__PICK__) || null);");
        }
        return { loader: "jsx", contents: src, resolveDir: path.dirname(PAGE) };
      });
    },
  };
}

const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
let fails = 0;

async function render(state, data = payload, extraAsync = []) {
  const tag = `${state.sm}_${state.sv}_${state.hm}_${state.hv}${state.pick ? "_pick" : ""}`;
  const out = path.join(FE, `node_modules/.cache/pd_${tag}.cjs`);
  await build({
    entryPoints: [PAGE], bundle: true, format: "cjs", platform: "node", outfile: out,
    loader: { ".js": "jsx", ".jsx": "jsx", ".css": "empty" },
    jsx: "automatic", logLevel: "error",
    external: ["react", "react-dom", "echarts"], plugins: [plugin(state)],
  });
  globalThis.__ASYNC__ = [{ personas: [] }, data, layout, ...extraAsync];
  delete require.cache[require.resolve(out)];
  const Page = require(out).default;
  try {
    return renderToStaticMarkup(React.createElement(Page, {
      session: { user: { username: "MSUDARSHAN" } }, isAdmin: true,
    }));
  } catch (e) {
    console.log(`RENDER FAIL ${tag}:`, e.message);
    console.log(e.stack.split("\n").slice(0, 4).join("\n"));
    process.exit(1);
  }
}

const SM_TITLE = { status: "Can we promise a date?", slip: "How far the promise slips",
  nodate: "Items we cannot date at all" };
const HM_TITLE = { risk: "When the stock runs out", sources: "What the dates rest on",
  all: "Every item and its dates" };

console.log("every filter combination:");
for (const sm of ["status", "slip", "nodate"]) {
  for (const sv of ["chart", "table"]) {
    for (const hm of ["risk", "sources", "all"]) {
      for (const hv of ["chart", "table"]) {
        const html = await render({ sm, sv, hm, hv });
        const need = [SM_TITLE[sm], HM_TITLE[hm]];
        const bad = need.filter((n) => !html.includes(n));
        const dirty = ["NaN", ">undefined<"].filter((x) => html.includes(x));
        const ok = !bad.length && !dirty.length;
        console.log(`  ${ok ? "PASS" : "FAIL"}  ${sm}/${sv} + ${hm}/${hv}` +
          (bad.length ? `  missing ${bad.join("|")}` : "") +
          (dirty.length ? `  dirty ${dirty.join("|")}` : ""));
        if (!ok) fails += 1;
      }
    }
  }
}

// card inventory
const src = fs.readFileSync(PAGE, "utf8");
const defaults = [...src.matchAll(/^\s{2}(\w+):\s*\{\s*x:/gm)].map((m) => m[1]);
const keys = [...src.matchAll(/<div key="(\w+)" className="card">/g)].map((m) => m[1]);
console.log(`\nDASH_DEFAULTS: ${defaults.join(", ")} | rendered: ${keys.join(", ")}`);
if (defaults.join() !== keys.join()) { console.log("INVENTORY FAIL"); fails += 1; }
else console.log("card inventory: OK");

// the item drill-down modal
globalThis.__PICK__ = { key: itemPayload.key, item: itemPayload.item };
const mhtml = await render({ sm: "status", sv: "table", hm: "risk", hv: "chart", pick: true },
  payload, [itemPayload]);
console.log("\nitem drill-down modal:");
for (const want of ["Where the supply comes from", "Running balance", "Can promise from",
  "Stock runs out", "Days to risk", "Supply in", "Orders out"]) {
  const ok = mhtml.includes(want);
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${want}`);
  if (!ok) fails += 1;
}

// empty scope
const empty = {
  ...payload, rows: [], total_rows: 0,
  kpis: { ...payload.kpis, items: 0, promised: 0, late: 0, no_date: 0, running_out: 0,
    buckets: payload.kpis.buckets.map((b) => ({ ...b, items: 0, qty: 0 })) },
};
console.log("\nempty scope:");
const e1 = await render({ sm: "status", sv: "chart", hm: "risk", hv: "chart" }, empty);
for (const [what, needle] of [["nothing to promise", "Nothing to promise for"],
  ["nothing in scope", "Nothing in scope"]]) {
  const ok = e1.includes(needle);
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${what}`);
  if (!ok) fails += 1;
}
const noLate = { ...payload,
  rows: payload.rows.map((r) => ({ ...r, class: r.class === "late" ? "dated" : r.class })),
  kpis: { ...payload.kpis, late: 0 } };
const e2 = await render({ sm: "slip", sv: "chart", hm: "risk", hv: "chart" }, noLate);
const ok2 = e2.includes("Nothing slips");
console.log(`  ${ok2 ? "PASS" : "FAIL"}  nothing exposed`);
if (!ok2) fails += 1;

console.log(fails ? `\n${fails} FAILURES` : "\nALL PROMISE-DATES RENDER CHECKS PASSED");
process.exit(fails ? 1 : 0);

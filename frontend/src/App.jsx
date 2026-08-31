import React, { useState, useEffect } from "react";
import { NAV, HIDDEN } from "./nav";
import { api } from "./api";
import { useAsync } from "./components/ui.jsx";
import DataFreshness from "./components/DataFreshness.jsx";
import Login from "./pages/Login.jsx";
import ChangePassword from "./pages/ChangePassword.jsx";
import Overview from "./pages/Overview.jsx";
import DataQuality from "./pages/DataQuality.jsx";
import Validation from "./pages/Validation.jsx";
import Forecasting from "./pages/Forecasting.jsx";
import Segmentation from "./pages/Segmentation.jsx";
import Supply from "./pages/Supply.jsx";
import MfgStock from "./pages/MfgStock.jsx";
import Vooki from "./pages/Vooki.jsx";
import AgedRM from "./pages/AgedRM.jsx";
import MSL from "./pages/MSL.jsx";
import ProjectionSales from "./pages/ProjectionSales.jsx";
import ProjectionAccuracy from "./pages/ProjectionAccuracy.jsx";
import SRDMS from "./pages/SRDMS.jsx";
import SupplierScorecard from "./pages/SupplierScorecard.jsx";
import AdhocPlanning from "./pages/AdhocPlanning.jsx";
import PPV from "./pages/PPV.jsx";
import ProductionSchedule from "./pages/ProductionSchedule.jsx";
import ItemReceiptSchedule from "./pages/ItemReceiptSchedule.jsx";
import JCPlan from "./pages/JCPlan.jsx";
import Analytics from "./pages/Analytics.jsx";
import KPIs from "./pages/KPIs.jsx";
import Governance from "./pages/Governance.jsx";
import PlanningSetting from "./pages/PlanningSetting.jsx";
import RoleMaster from "./pages/RoleMaster.jsx";
import UserMaster from "./pages/UserMaster.jsx";
import Audit from "./pages/Audit.jsx";

export default function App() {
  const [page, setPage] = useState("supply");
  // shared cycle status drives the badge + header; bumped on any mutation
  const [version, setVersion] = useState(0);
  const bump = () => setVersion((v) => v + 1);
  const status = useAsync(api.overview, [version]);
  const cycle = status.data?.cycle;
  const openEx = cycle?.exceptions_open ?? 0;

  // ── access control ───────────────────────────────────────────────────────
  // The login gate only activates once passwords are enabled AND at least one user is
  // approved — before that the app stays open (bootstrap, so no one is locked out).
  const [session, setSession] = useState(() => {
    try { return JSON.parse(localStorage.getItem("app_session")); } catch { return null; }
  });
  const auth = useAsync(() => api.userMaster.status(), []);
  const gate = !!(auth.data && auth.data.password_enabled && auth.data.user_count > 0);
  const menus = new Set(session?.menus || []);
  const isAdmin = !gate || menus.has("usermaster");
  const allowed = (id) => !HIDDEN.has(id) && (isAdmin || menus.has(id));
  const navItems = NAV.filter((n) => allowed(n.id));

  const [showChangePw, setShowChangePw] = useState(false);
  const saveSession = (s) => { setSession(s); localStorage.setItem("app_session", JSON.stringify(s)); };
  const doLogin = (s) => {
    saveSession(s);
    const admin = (s.menus || []).includes("usermaster");
    const first = NAV.find((n) => !HIDDEN.has(n.id) && (admin || (s.menus || []).includes(n.id)));
    setPage(first ? first.id : "");
  };
  const doLogout = () => { setSession(null); localStorage.removeItem("app_session"); };
  const loginName = session?.user?.username || session?.user?.user_code || "";

  useEffect(() => {   // keep the selected page within the user's granted modules
    if (gate && session && !allowed(page)) setPage(navItems[0] ? navItems[0].id : "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gate, session, page]);

  if (auth.loading) return <div className="loading" style={{ padding: 40 }}>Loading…</div>;
  if (gate && !session) return <Login onLogin={doLogin} />;
  if (gate && session && session.user?.must_change_password) {
    return <ChangePassword login={loginName} forced
      onDone={() => saveSession({ ...session, user: { ...session.user, must_change_password: false } })} />;
  }

  const pages = {
    overview: <Overview onChange={bump} />,
    dq: <DataQuality />,
    validation: <Validation onChange={bump} />,
    forecasting: <Forecasting />,
    segmentation: <Segmentation />,
    supply: <Supply />,
    mfgstock: <MfgStock />,
    vooki: <Vooki />,
    adhoc: <AdhocPlanning />,
    agedrm: <AgedRM />,
    msl: <MSL />,
    projsales: <ProjectionSales />,
    projaccuracy: <ProjectionAccuracy />,
    "rd-samples": <SRDMS session={session} mode="requester" />,
    "wh-dispatch": <SRDMS session={session} mode="warehouse" />,
    "qc-samples": <SRDMS session={session} mode="qc" />,
    srdms: <SRDMS session={session} mode="all" />,
    scorecard: <SupplierScorecard />,
    ppv: <PPV />,
    prodsched: <ProductionSchedule />,
    receipt: <ItemReceiptSchedule />,
    jcplan: <JCPlan />,
    analytics: <Analytics />,
    kpis: <KPIs />,
    governance: <Governance />,
    planningsetting: <PlanningSetting />,
    roles: <RoleMaster />,
    usermaster: <UserMaster />,
    audit: <Audit version={version} />,
  };

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span>Supply Chain</span>
          <h1>Planning Tool</h1>
        </div>
        <nav>
          {navItems.map((n) => (
            <button
              key={n.id}
              className={page === n.id ? "active" : ""}
              onClick={() => setPage(n.id)}
            >
              <span>{n.icon}</span>
              {n.label}
              {n.id === "validation" && openEx > 0 && <span className="badge">{openEx}</span>}
            </button>
          ))}
        </nav>
        <div className="foot">
          {session && (
            <div style={{ marginBottom: 8 }}>
              👤 <b>{session.user?.name || session.user?.username}</b>
              <br />
              <button onClick={() => setShowChangePw(true)}>🔑 Change password</button>
              <button onClick={doLogout}>⎋ Sign out</button>
            </div>
          )}
          Integrated S&amp;OP / IBP
          <br />
          <button
            onClick={async () => {
              if (confirm("Reset the cycle to its initial synthetic state?")) {
                await api.reset();
                bump();
              }
            }}
          >
            ↺ Reset cycle
          </button>
        </div>
      </aside>

      <div className="main">
        <div className="topbar">
          <h2>{NAV.find((n) => n.id === page)?.label}</h2>
          <div className="cycle-pill">
            <DataFreshness />
            {cycle && (
              <>
                <span className="pill">Cycle {cycle.cycle_period}</span>
                <span className="pill">{cycle.step}</span>
              </>
            )}
          </div>
        </div>
        <div className="content">
          {allowed(page) && pages[page] ? pages[page] : (
            <div className="banner warn">
              You don’t have access to this module. {navItems.length === 0
                ? "No modules are assigned to your account — please contact your administrator."
                : "Pick a module from the sidebar."}
            </div>
          )}
        </div>
      </div>

      {showChangePw && session && (
        <ChangePassword login={loginName}
          onDone={() => { setShowChangePw(false); alert("Password updated."); }}
          onCancel={() => setShowChangePw(false)} />
      )}
    </div>
  );
}

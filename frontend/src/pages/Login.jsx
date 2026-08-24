import React, { useState } from "react";
import { api } from "../api";

export default function Login({ onLogin }) {
  const [login, setLogin] = useState("");
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const r = await api.userMaster.login(login.trim(), pw);
      onLogin({ user: r.user, menus: (r.user.menus || []).map((m) => m.id) });
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
      background: "linear-gradient(135deg,#1F3A5F,#2A9D8F)", fontFamily: "'Segoe UI',Roboto,Helvetica,Arial,sans-serif" }}>
      <form onSubmit={submit} style={{ width: 360, maxWidth: "92%", background: "#fff", borderRadius: 14,
        padding: "30px 30px 26px", boxShadow: "0 10px 40px rgba(0,0,0,.25)" }}>
        <div style={{ textAlign: "center", marginBottom: 18 }}>
          <div style={{ fontSize: 12, letterSpacing: 1, color: "#64748b", textTransform: "uppercase" }}>Supply Chain</div>
          <div style={{ fontSize: 22, fontWeight: 800, color: "#1F3A5F" }}>Planning Tool</div>
          <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 2 }}>Sign in to continue</div>
        </div>
        {err && <div className="banner err" style={{ marginBottom: 12 }}>⚠ {err}</div>}
        <label style={{ display: "block", fontSize: 12, color: "#475569", marginBottom: 4 }}>Username or user code</label>
        <input className="searchbox" style={{ width: "100%", marginBottom: 12 }} autoFocus value={login}
          onChange={(e) => setLogin(e.target.value)} placeholder="e.g. SARANYAA" />
        <label style={{ display: "block", fontSize: 12, color: "#475569", marginBottom: 4 }}>Password</label>
        <input className="searchbox" style={{ width: "100%", marginBottom: 18 }} type="password" value={pw}
          onChange={(e) => setPw(e.target.value)} placeholder="Password" />
        <button className="btn" type="submit" disabled={busy} style={{ width: "100%", justifyContent: "center" }}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 14, textAlign: "center" }}>
          New users' default password is <b>pure@123</b> — change it after first login.
        </div>
      </form>
    </div>
  );
}

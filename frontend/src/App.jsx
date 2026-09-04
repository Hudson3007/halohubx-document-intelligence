import { useEffect, useMemo, useRef, useState } from "react";
import {
  acceptInvite,
  AuthError,
  checkHealth,
  checkoutTopup,
  confirmDocument,
  createMember,
  devSimulateBilling,
  documentFileUrl,
  getAudit,
  getBilling,
  getBillingPlans,
  getDashboard,
  getDocumentForReview,
  getStatus,
  getUsage,
  inviteInfo,
  listMembers,
  login,
  logout,
  signup,
  subscribePlan,
  uploadDocument,
} from "./api.js";

const THRESHOLD = 0.85;
const SESSION_KEY = "halohubx.session.v1";

function confidenceClass(score) {
  if (score == null) return "conf-low";
  if (score < 0.7) return "conf-low";
  if (score < THRESHOLD) return "conf-mid";
  return "";
}

function ConfBadge({ score }) {
  return (
    <span className={`conf-badge ${confidenceClass(score)}`} title={`Confidence: ${score}`}>
      {Math.round((score ?? 0) * 100)}%
    </span>
  );
}

function TextField({ label, value, onChange, score }) {
  return (
    <label className={`field ${confidenceClass(score)}`}>
      <span className="field-label">
        {label}
        <ConfBadge score={score} />
      </span>
      <input value={value ?? ""} onChange={(e) => onChange(e.target.value)} placeholder="—" />
    </label>
  );
}

function NumField({ label, value, onChange, score }) {
  const set = (raw) => onChange(raw === "" ? null : Number(raw));
  return (
    <label className={`field ${confidenceClass(score)}`}>
      <span className="field-label">
        {label}
        <ConfBadge score={score} />
      </span>
      <input value={value ?? ""} type="number" onChange={(e) => set(e.target.value)} placeholder="—" />
    </label>
  );
}

function LineItemEditor({ item, index, onChange, onRemove }) {
  const set = (patch) => onChange(index, { ...item, ...patch });
  return (
    <div className={`line-item ${confidenceClass(item.confidence)}`}>
      <div className="line-item-head">
        <span>
          Item #{index + 1}
          <ConfBadge score={item.confidence} />
        </span>
        <button className="ghost danger" onClick={() => onRemove(index)}>Remove</button>
      </div>
      <div className="line-item-grid">
        <TextField label="Description" value={item.description} score={item.confidence} onChange={(v) => set({ description: v })} />
        <TextField label="HSN/SAC" value={item.hsn_sac_code} score={item.confidence} onChange={(v) => set({ hsn_sac_code: v })} />
        <NumField label="Qty" value={item.quantity} score={item.confidence} onChange={(v) => set({ quantity: v })} />
        <NumField label="Unit Price" value={item.unit_price} score={item.confidence} onChange={(v) => set({ unit_price: v })} />
        <NumField label="CGST" value={item.tax_cgst} score={item.confidence} onChange={(v) => set({ tax_cgst: v })} />
        <NumField label="SGST" value={item.tax_sgst} score={item.confidence} onChange={(v) => set({ tax_sgst: v })} />
        <NumField label="IGST" value={item.tax_igst} score={item.confidence} onChange={(v) => set({ tax_igst: v })} />
        <NumField label="Total" value={item.total_value} score={item.confidence} onChange={(v) => set({ total_value: v })} />
      </div>
    </div>
  );
}

function FlagsPanel({ flags }) {
  if (!flags || !flags.length) return null;
  return (
    <div className="flags">
      <h4>Model flags</h4>
      {flags.map((f, i) => (
        <div key={i} className="flag flag-warn">
          <code>{typeof f === "string" ? f : JSON.stringify(f)}</code>
        </div>
      ))}
    </div>
  );
}

function InvoiceEditor({ invoice, index, onChange, onRemove }) {
  const set = (patch) => onChange(index, { ...invoice, ...patch });
  const editVendor = (p) => set({ vendor: { ...invoice.vendor, ...p } });
  const editDetails = (p) => set({ invoice_details: { ...invoice.invoice_details, ...p } });
  const editItem = (i, patch) => set({ line_items: invoice.line_items.map((it, idx) => (idx === i ? patch : it)) });
  const addItem = () => set({ line_items: [...(invoice.line_items || []), { description: "", confidence: 1 }] });
  const removeItem = (i) => set({ line_items: invoice.line_items.filter((_, idx) => idx !== i) });

  return (
    <div className="card invoice-card">
      <div className="card-head">
        <h5>
          Invoice #{index + 1}
          <ConfBadge score={invoice.invoice_details?.confidence} />
        </h5>
        <button className="ghost danger" onClick={() => onRemove(index)}>Remove invoice</button>
      </div>
      <FlagsPanel flags={invoice.flags} />
      <div className="grid2">
        <TextField label="Vendor" value={invoice.vendor?.name} score={invoice.vendor?.confidence} onChange={(v) => editVendor({ name: v })} />
        <TextField label="GSTIN" value={invoice.vendor?.gstin} score={invoice.vendor?.confidence} onChange={(v) => editVendor({ gstin: v })} />
        <TextField label="Invoice no." value={invoice.invoice_details?.invoice_number} score={invoice.invoice_details?.confidence} onChange={(v) => editDetails({ invoice_number: v })} />
        <TextField label="Date" value={invoice.invoice_details?.date} score={invoice.invoice_details?.confidence} onChange={(v) => editDetails({ date: v })} />
      </div>
      <div className="card-head" style={{ marginTop: 12 }}>
        <h5>Line items</h5>
        <button className="ghost" onClick={addItem}>+ Add item</button>
      </div>
      {(invoice.line_items || []).map((item, i) => (
        <LineItemEditor key={i} item={item} index={i} onChange={editItem} onRemove={removeItem} />
      ))}
    </div>
  );
}

function ReviewPanel({ docId, apiKey, onReset }) {
  const [data, setData] = useState(null);
  const [status, setStatus] = useState("loading");
  const [error, setError] = useState("");
  const [confirmState, setConfirmState] = useState("idle");
  const [message, setMessage] = useState("");
  const [pdfUrl, setPdfUrl] = useState(null);
  const [pdfError, setPdfError] = useState("");
  const timer = useRef(null);

  // Load the original PDF for side-by-side review once the document resolves.
  useEffect(() => {
    if (!docId) return;
    documentFileUrl(docId, apiKey)
      .then((url) => setPdfUrl(url))
      .catch((e) => setPdfError(String(e.message || e)));
    return () => { if (pdfUrl) URL.revokeObjectURL(pdfUrl); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId, apiKey]);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const s = await getStatus(docId, apiKey);
        if (!alive) return;
        setStatus(s.status);
        if (s.status === "completed") {
          const d = await getDocumentForReview(docId, apiKey);
          if (!alive) return;
          setData(d);
          clearInterval(timer.current);
        } else if (s.status === "failed") {
          setError("Extraction failed. Check the backend logs.");
          clearInterval(timer.current);
        }
      } catch (e) {
        if (alive) setError(String(e.message || e));
      }
    };
    poll();
    timer.current = setInterval(poll, 2500);
    return () => {
      alive = false;
      clearInterval(timer.current);
    };
  }, [docId, apiKey]);

  const confirm = async () => {
    setConfirmState("pushing");
    setMessage("");
    try {
      const result = { ...data.result };
      // Keep invoice_count consistent with the number of invoices.
      result.invoice_count = (result.invoices || []).length;
      const r = await confirmDocument(docId, apiKey, result);
      setConfirmState("done");
      setMessage(`Confirmed. Status: ${r.status}${r.webhook_delivered ? " — webhook delivered." : ""}`);
    } catch (e) {
      setConfirmState("idle");
      setError(String(e.message || e));
    }
  };

  const editInvoice = (i, patch) =>
    setData((d) => {
      const invoices = d.result.invoices.map((inv, idx) => (idx === i ? patch : inv));
      return { ...d, result: { ...d.result, invoices } };
    });
  const removeInvoice = (i) =>
    setData((d) => ({
      ...d,
      result: {
        ...d.result,
        invoices: d.result.invoices.filter((_, idx) => idx !== i),
        invoice_count: d.result.invoices.length - 1,
      },
    }));

  if (error) {
    return (
      <div className="panel">
        <p className="error">{error}</p>
        <button className="primary" onClick={onReset}>Go back</button>
      </div>
    );
  }
  if ((status === "processing" || status === "pending") && !data) {
    return (
      <div className="panel waiting">
        <div className="spinner" />
        <p>Extracting document… (status: {status})</p>
      </div>
    );
  }
  if (!data?.result) {
    return <div className="panel"><p>No result yet.</p></div>;
  }

  const invoices = data.result.invoices || [];
  const lowCount = invoices.reduce(
    (n, inv) =>
      n +
      (inv.vendor?.confidence ?? 0) < THRESHOLD +
        0 * ((inv.invoice_details?.confidence ?? 0) < THRESHOLD ? 1 : 0) +
        (inv.line_items || []).filter((li) => (li.confidence ?? 0) < THRESHOLD).length,
    0
  );

  return (
    <div className="review">
      <div className="review-head">
        <div>
          <h2>Validate extracted data</h2>
          <p className="muted">
            Client: {data.client_name} · {invoices.length} invoice(s) detected · fields below 85% are highlighted.
          </p>
        </div>
        <button className="ghost" onClick={onReset}>Back to list</button>
      </div>

      <FlagsPanel flags={data.result.document_flags} />

      <div className="review-split">
        <div className="review-editors">
          {invoices.map((inv, i) => (
            <InvoiceEditor key={i} invoice={inv} index={i} onChange={editInvoice} onRemove={removeInvoice} />
          ))}
        </div>

        <aside className="pdf-pane">
          <div className="pdf-pane-head">
            <strong>Original document</strong>
            {pdfUrl ? <a className="ghost small" href={pdfUrl} target="_blank" rel="noreferrer">Open ↗</a> : null}
          </div>
          {pdfUrl ? (
            <iframe className="pdf-frame" src={pdfUrl} title="Original PDF" />
          ) : pdfError ? (
            <p className="error">{pdfError}</p>
          ) : (
            <p className="muted">Loading preview…</p>
          )}
        </aside>
      </div>

      <div className="push-bar">
        <span className="muted">Document: {docId}</span>
        <button
          className={`primary ${confirmState === "pushing" ? "busy" : ""}`}
          onClick={confirm}
          disabled={confirmState === "pushing"}
        >
          {confirmState === "pushing" ? "Confirming…" : confirmState === "done" ? "✓ Confirmed" : "Approve & push to ERP"}
        </button>
      </div>
      {message ? <p className="ok">{message}</p> : null}
    </div>
  );
}

function UploadPanel({ apiKey, onUploaded }) {
  const [file, setFile] = useState(null);
  const [clientName, setClientName] = useState("");
  const [webhookUrl, setWebhookUrl] = useState("");
  const [buisy, setBuisy] = useState(false);
  const [err, setErr] = useState("");

  const doUpload = async () => {
    if (!file || !clientName.trim()) {
      setErr("Select a file and enter a client name.");
      return;
    }
    setErr("");
    setBuisy(true);
    try {
      const r = await uploadDocument(file, { apiKey, clientName, webhookUrl: webhookUrl.trim() });
      setBuisy(false);
      onUploaded(r.document_id);
    } catch (e) {
      setBuisy(false);
      setErr(String(e.message || e));
    }
  };

  return (
    <div className="upload">
      <div className="hero">
        <h1>HaloHubX Document Engine</h1>
        <p className="muted">Upload a GST invoice / PO — review what the model extracted — push to your ERP.</p>
      </div>
      <div className="card">
        <label className="field">
          <span className="field-label">Client name</span>
          <input value={clientName} onChange={(e) => setClientName(e.target.value)} placeholder="e.g. InfraBuild Steels Pvt Ltd" />
        </label>
        <label className="field">
          <span className="field-label">Webhook URL (optional)</span>
          <input value={webhookUrl} onChange={(e) => setWebhookUrl(e.target.value)} placeholder="https://your-erp.example.com/webhook" />
        </label>
        <label className="drop">
          <input type="file" accept=".pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          {file ? <strong>{file.name}</strong> : <span>Drop a PDF here or click to browse</span>}
        </label>
        {err ? <p className="error">{err}</p> : null}
        <button className="primary big" disabled={buisy || !file} onClick={doUpload}>
          {buisy ? "Uploading…" : "Upload & extract"}
        </button>
      </div>
    </div>
  );
}

function fmtDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  } catch {
    return "—";
  }
}

function DocsTable({ docs, loading, error, onOpen, empty }) {
  if (error) return <p className="error">{error}</p>;
  if (loading) return <p className="muted">Loading…</p>;
  if (!docs.length) return <p className="muted">{empty || "No documents yet. Upload one first."}</p>;
  return (
    <div className="doc-table">
      {docs.map((d) => (
        <div className="doc-row" key={d.document_id}>
          <div className="doc-main">
            <strong title={d.document_id}>{d.filename}</strong>
            <span className="status-badge">{d.status || "pending"}</span>
            {d.needs_review ? <span className="status-badge warn">Needs review</span> : null}
          </div>
          <div className="doc-meta">
            <span>{d.client_name || "—"}</span>
            <span>{d.invoice_count ? `${d.invoice_count} invoice(s)` : "—"}</span>
            <span>{fmtDate(d.created_at)}</span>
          </div>
          <button className="ghost" onClick={() => onOpen(d.document_id)}>Review</button>
        </div>
      ))}
    </div>
  );
}

function CreditMeter({ usage, compact }) {
  if (!usage) return null;
  const usedPct = Math.min(100, Math.round((usage.current_month_used / usage.monthly_quota) * 100));
  if (compact) {
    return (
      <div className="credit-chip" title={compactUsageTitle(usage)}>
        <span className="credit-dot" />
        {usage.remaining_total} credits left
      </div>
    );
  }
  return (
    <div className="meter">
      <div className="meter-head">
        <span className="muted">Monthly usage · {usage.period}</span>
        <strong>{usage.current_month_used} / {usage.monthly_quota} credits</strong>
      </div>
      <div className="meter-bar"><div className="meter-fill" style={{ width: `${usedPct}%` }} /></div>
      <div className="meter-legend">
        <span>Top-up balance: <strong>{usage.topup_credits}</strong></span>
        <span>Remaining this month: <strong>{usage.remaining_monthly}</strong></span>
        <span>Total available: <strong>{usage.remaining_total}</strong></span>
      </div>
    </div>
  );
}

function compactUsageTitle(u) {
  return `Credits: ${u.remaining_total} total (quota ${u.remaining_monthly} remaining this month + ${u.topup_credits} top-up)`;
}

function UsagePanel({ apiKey }) {
  const [usage, setUsage] = useState(null);
  const [err, setErr] = useState("");
  const load = () => {
    setErr("");
    getUsage(apiKey).then(setUsage).catch((e) => setErr(String(e.message || e)));
  };
  useEffect(load, [apiKey]);
  return (
    <div className="panel">
      <div className="recent-head">
        <div>
          <h2>Usage &amp; credits</h2>
          <p className="muted">1 credit = 1 PDF page scanned. Top-up credits are used first, then your monthly quota. Unused monthly credits reset each period.</p>
        </div>
        <button className="ghost" onClick={load}>Refresh</button>
      </div>
      {err ? <p className="error">{err}</p> : null}
      {!usage && !err ? <p className="muted">Loading…</p> : null}
      {usage ? (
        <>
          <CreditMeter usage={usage} />
          <div className="stat-grid" style={{ marginTop: 22 }}>
            <div className="stat-card"><span className="stat-num">{usage.monthly_quota}</span><span>Monthly quota</span></div>
            <div className="stat-card"><span className="stat-num">{usage.current_month_used}</span><span>Used this period</span></div>
            <div className="stat-card"><span className="stat-num">{usage.remaining_monthly}</span><span>Remaining this month</span></div>
            <div className="stat-card"><span className="stat-num">{usage.topup_credits}</span><span>Top-up balance</span></div>
          </div>
        </>
      ) : null}
    </div>
  );
}

function DashboardPanel({ apiKey, onOpenDoc, onGotoUpload }) {
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [client, setClient] = useState("all");

  const load = () => {
    setLoading(true);
    setErr("");
    getDashboard(apiKey)
      .then((d) => setDocs(d))
      .catch((e) => setErr(String(e.message || e)))
      .finally(() => setLoading(false));
  };
  useEffect(load, [apiKey]);

  const stats = useMemo(() => {
    const total = docs.length;
    const completed = docs.filter((d) => d.status === "completed").length;
    const needs = docs.filter((d) => d.needs_review).length;
    const clients = new Set(docs.map((d) => d.client_name).filter((c) => c && c !== "—"));
    return { total, completed, needs, clients: clients.size };
  }, [docs]);

  const clientList = useMemo(() => {
    const set = new Set(docs.map((d) => d.client_name).filter((c) => c && c !== "—"));
    return ["all", ...set];
  }, [docs]);

  const filtered = useMemo(
    () => (client === "all" ? docs : docs.filter((d) => d.client_name === client)),
    [docs, client]
  );

  if (loading && docs.length === 0) return <div className="panel"><p className="muted">Loading dashboard…</p></div>;
  if (err && docs.length === 0) return <div className="panel"><p className="error">{err}</p></div>;

  return (
    <div className="panel">
      <div className="panel-head">
        <div>
          <h2>Dashboard</h2>
          <p className="muted">Overview of extraction activity for this partner.</p>
        </div>
        <div className="row-gap">
          <select className="select" value={client} onChange={(e) => setClient(e.target.value)}>
            {clientList.map((c) => (
              <option key={c} value={c}>{c === "all" ? "All clients" : c}</option>
            ))}
          </select>
          <button className="ghost" onClick={load}>Refresh</button>
        </div>
      </div>

      <div className="stat-grid">
        <div className="stat-card"><span className="stat-num">{stats.total}</span><span>Documents processed</span></div>
        <div className="stat-card"><span className="stat-num">{stats.completed}</span><span>Completed</span></div>
        <div className="stat-card"><span className="stat-num">{stats.needs}</span><span>Needs review</span></div>
        <div className="stat-card"><span className="stat-num">{stats.clients}</span><span>Clients</span></div>
      </div>

      <div className="recent">
        <div className="recent-head">
          <h3>Document history</h3>
          <span className="muted">{filtered.length} shown</span>
        </div>
        <DocsTable docs={filtered} loading={loading} error={err} onOpen={onOpenDoc}
                   empty={client === "all" ? "No documents yet. Upload one first." : "No documents for this client yet."} />
        {filtered.length === 0 ? <button className="primary" onClick={onGotoUpload}>Upload a document</button> : null}
      </div>
    </div>
  );
}

function DocumentsList({ apiKey, onOpen }) {
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [client, setClient] = useState("all");
  const load = () => {
    setLoading(true);
    getDashboard(apiKey)
      .then((d) => setDocs(d))
      .catch((e) => setErr(String(e.message || e)))
      .finally(() => setLoading(false));
  };
  useEffect(load, [apiKey]);

  const clients = useMemo(() => ["all", ...new Set(docs.map((d) => d.client_name).filter((c) => c && c !== "—"))], [docs]);
  const filtered = useMemo(
    () => (client === "all" ? docs : docs.filter((d) => d.client_name === client)),
    [docs, client]
  );

  return (
    <div className="panel">
      <div className="recent-head">
        <div>
          <h2>Documents</h2>
          <p className="muted">Every document uploaded for this partner.</p>
        </div>
        <div className="row-gap">
          <select className="select" value={client} onChange={(e) => setClient(e.target.value)}>
            {clients.map((c) => <option key={c} value={c}>{c === "all" ? "All clients" : c}</option>)}
          </select>
          <button className="ghost" onClick={load}>Refresh</button>
        </div>
      </div>
      <DocsTable docs={filtered} loading={loading} error={err} onOpen={onOpen} />
    </div>
  );
}

function AuthPanel({ onAuthenticated, initialKey, serverOk }) {
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const submit = async () => {
    setErr("");
    if (!email.trim() || !password) {
      setErr("Enter your email and password.");
      return;
    }
    if (mode === "signup" && password.length < 8) {
      setErr("Password must be at least 8 characters.");
      return;
    }
    setBusy(true);
    try {
      const res = mode === "signup"
        ? await signup({ name: name.trim() || undefined, email: email.trim(), password })
        : await login({ email: email.trim(), password });
      onAuthenticated(res); // { api_key, partner_name, user }
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <div className="login-card">
        <div className="brand brand-block">
          <span className="logo-mark">H</span>
          <span className="brand-name">HaloHubX</span>
        </div>
        <div className="auth-tabs">
          <button className={mode === "login" ? "auth-tab active" : "auth-tab"} onClick={() => { setMode("login"); setErr(""); }}>
            Sign in
          </button>
          <button className={mode === "signup" ? "auth-tab active" : "auth-tab"} onClick={() => { setMode("signup"); setErr(""); }}>
            Create account
          </button>
        </div>
        {serverOk === false ? (
          <p className="error">Backend not reachable. Start it with: <code>uvicorn app.main:app --reload</code></p>
        ) : null}
        {mode === "login" ? (
          <p className="muted">Review AI-extracted invoices and push them to your ERP.</p>
        ) : (
          <p className="muted">Create a workspace — we provision your API key instantly.</p>
        )}
        {mode === "signup" ? (
          <label className="field">
            <span className="field-label">Full name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Priya Rao" />
          </label>
        ) : null}
        <label className="field">
          <span className="field-label">Email</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
            autoComplete="email"
          />
        </label>
        <label className="field">
          <span className="field-label">Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
            placeholder={mode === "signup" ? "At least 8 characters" : "••••••••"}
            autoComplete={mode === "signup" ? "new-password" : "current-password"}
          />
        </label>
        {err ? <p className="error">{err}</p> : null}
        <button className="primary big" disabled={busy} onClick={submit}>
          {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
        </button>
        <p className="hint muted">
          {mode === "login"
            ? "Your account was created on sign-up, or provisioned by your partner admin."
            : "Sign-up creates a Partner workspace and issues you an API key."}
        </p>
      </div>
    </div>
  );
}

function InviteAccept({ token, onAuthenticated }) {
  const [info, setInfo] = useState(null);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!token) return;
    inviteInfo(token)
      .then((i) => { setInfo(i); setName(i.name || ""); })
      .catch((e) => setErr(String(e.message || e)));
  }, [token]);

  const accept = async () => {
    if (password.length < 8) { setErr("Password must be at least 8 characters."); return; }
    setErr(""); setBusy(true);
    try {
      const res = await acceptInvite(token, { name: name.trim() || undefined, password });
      onAuthenticated(res); // lands signed-in
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <div className="login-card">
        <div className="brand brand-block">
          <span className="logo-mark">H</span>
          <span className="brand-name">HaloHubX</span>
        </div>
        <h1>You're invited</h1>
        {info ? (
          <p className="muted">Join <strong>{info.email}</strong> as a workspace {info.role} — set a password to finish.</p>
        ) : !err ? (
          <p className="muted">Loading invite…</p>
        ) : null}
        {err && !info ? <p className="error">{err}</p> : null}
        {info ? (
          <>
            <label className="field">
              <span className="field-label">Full name</span>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" />
            </label>
            <label className="field">
              <span className="field-label">Password</span>
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                     onKeyDown={(e) => { if (e.key === "Enter") accept(); }} placeholder="At least 8 characters" />
            </label>
            {err ? <p className="error">{err}</p> : null}
            <button className="primary big" disabled={busy} onClick={accept}>
              {busy ? "Setting up…" : "Accept invite & continue"}
            </button>
          </>
        ) : null}
      </div>
    </div>
  );
}

const NAV = [
  { id: "dashboard", label: "Dashboard", icon: "▦" },
  { id: "upload", label: "Upload", icon: "↑", ownerOnly: true },
  { id: "docs", label: "Documents", icon: "☰" },
  { id: "usage", label: "Usage", icon: "●" },
  { id: "billing", label: "Billing", icon: "₹", ownerOnly: true },
  { id: "audit", label: "Activity", icon: "◷" },
  { id: "members", label: "Members", icon: "👥", ownerOnly: true },
];

function actionLabel(action) {
  const map = {
    upload: "Upload",
    approval: "Approved",
    review_edit: "Edited",
    login: "Signed in",
    member_added: "Member added",
  };
  return map[action] || action;
}

function fmtAuditTime(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(); } catch { return "—"; }
}

function AuditPanel({ apiKey }) {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const load = () => {
    setLoading(true); setErr("");
    getAudit(apiKey)
      .then((r) => setEntries(r.entries || []))
      .catch((e) => setErr(String(e.message || e)))
      .finally(() => setLoading(false));
  };
  useEffect(load, [apiKey]);

  return (
    <div className="panel">
      <div className="recent-head">
        <div>
          <h2>Activity log</h2>
          <p className="muted">A chronological audit trail of uploads, reviews, approvals, logins and member changes.</p>
        </div>
        <button className="ghost" onClick={load}>Refresh</button>
      </div>
      {err ? <p className="error">{err}</p> : null}
      {loading ? <p className="muted">Loading…</p> : null}
      {!loading && !entries.length ? <p className="muted">No activity yet.</p> : null}
      <div className="doc-table">
        {entries.map((e) => (
          <div className="doc-row" key={e.id}>
            <div className="doc-main">
              <strong>{actionLabel(e.action)}</strong>
              <span className={`status-badge ${e.actor_role === "analyst" ? "warn" : ""}`}>{e.actor_role}</span>
              {e.document_id ? <code className="muted" style={{ fontSize: 11 }}>{e.document_id.slice(0, 8)}…</code> : null}
            </div>
            <div className="doc-meta">
              <span>{e.actor_email || "—"}</span>
              <span>{e.summary}</span>
              <span>{fmtAuditTime(e.created_at)}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function MembersPanel({ apiKey, currentUser }) {
  const [members, setMembers] = useState([]);
  const [err, setErr] = useState("");
  const [form, setForm] = useState({ name: "", email: "", role: "analyst" });
  const [busy, setBusy] = useState(false);
  const [inviteLink, setInviteLink] = useState("");
  const [copied, setCopied] = useState(false);

  const load = () => {
    setErr("");
    listMembers(apiKey).then((r) => setMembers(r.members || [])).catch((e) => setErr(String(e.message || e)));
  };
  useEffect(load, [apiKey]);

  const invite = async () => {
    if (!form.email.trim()) { setErr("Email is required."); return; }
    setErr(""); setInviteLink(""); setCopied(false); setBusy(true);
    try {
      const r = await createMember(apiKey, { email: form.email.trim(), name: form.name.trim() || undefined, role: form.role });
      setForm({ name: "", email: "", role: "analyst" });
      setInviteLink(`${window.location.origin}${r.invite_link}`);
      load();
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  const copyLink = async () => {
    try { await navigator.clipboard.writeText(inviteLink); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch {}
  };

  return (
    <div className="panel">
      <div className="recent-head">
        <div>
          <h2>Members</h2>
          <p className="muted">Owners manage the workspace; analysts can review and approve but not upload or change settings.</p>
        </div>
        <button className="ghost" onClick={load}>Refresh</button>
      </div>
      {err ? <p className="error">{err}</p> : null}
      <div className="doc-table">
        {members.map((m) => (
          <div className="doc-row" key={m.id}>
            <div className="doc-main">
              <strong>{m.name || m.email}</strong>
              <span className={`status-badge ${m.role === "owner" ? "" : "warn"}`}>{m.role}</span>
              {m.email === currentUser?.email ? <span className="status-badge">You</span> : null}
            </div>
            <div className="doc-meta"><span>{m.email}</span></div>
          </div>
        ))}
      </div>

      <h3 style={{ marginTop: 22 }}>Invite a member</h3>
      <p className="muted" style={{ marginTop: 0 }}>They'll set their own password via the invite link — you never see it.</p>
      <div className="grid2">
        <label className="field">
          <span className="field-label">Name</span>
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Ravi Kumar" />
        </label>
        <label className="field">
          <span className="field-label">Email</span>
          <input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} type="email" placeholder="teammate@company.com" />
        </label>
        <label className="field">
          <span className="field-label">Role</span>
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            <option value="analyst">Analyst (review only)</option>
            <option value="owner">Owner (full access)</option>
          </select>
        </label>
      </div>
      <button className="primary" disabled={busy} onClick={invite}>{busy ? "Inviting…" : "Create invite"}</button>

      {inviteLink ? (
        <div className="invite-result">
          <input readOnly value={inviteLink} className="invite-input" />
          <button className="ghost" onClick={copyLink}>{copied ? "Copied ✓" : "Copy link"}</button>
        </div>
      ) : null}
    </div>
  );
}

function fmtPaise(paise) {
  return `₹${(paise / 100).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

function BillingPanel({ apiKey }) {
  const [bill, setBill] = useState(null);
  const [catalogue, setCatalogue] = useState([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const [topup, setTopup] = useState(1000);
  const [msg, setMsg] = useState("");

  const load = () => {
    setErr(""); setMsg("");
    getBilling(apiKey)
      .then(setBill)
      .catch((e) => setErr(String(e.message || e)));
    getBillingPlans()
      .then((r) => setCatalogue(r.plans || []))
      .catch(() => {});
  };
  useEffect(load, [apiKey]);

  const run = async (label, fn, successMsg) => {
    setBusy(label); setErr(""); setMsg("");
    try {
      const r = await fn();
      setMsg(successMsg);
      load();
      return r;
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy("");
    }
  };

  const buyTopup = () =>
    run("topup", () => checkoutTopup(apiKey, topup),
      `Checkout created for ${topup} credit(s). Complete payment to add them to your balance.`);
  const subscribe = (plan) =>
    run("plan", () => subscribePlan(apiKey, plan), `Subscribed to the ${plan} plan.`);
  const simulate = (which) =>
    run("sim", () =>
      devSimulateBilling(apiKey, which === "plan" ? { plan: "growth" } : { credits: 500 }),
      which === "plan" ? "Applied the growth plan (demo)." : "Added 500 credit(s) (demo).");

  return (
    <div className="panel">
      <div className="recent-head">
        <div>
          <h2>Billing &amp; credits</h2>
          <p className="muted">1 credit = 1 PDF page. Subscriptions set your monthly quota; top-ups add to your pre-paid balance.</p>
        </div>
        <button className="ghost" onClick={load}>Refresh</button>
      </div>
      {err ? <p className="error">{err}</p> : null}
      {msg ? <p className="ok">{msg}</p> : null}
      {!bill && !err ? <p className="muted">Loading…</p> : null}

      {bill ? (
        <>
          <CreditMeter usage={bill.usage} />
          <div className="stat-grid" style={{ marginTop: 22 }}>
            <div className="stat-card"><span className="stat-num">{bill.plan}</span><span>Plan</span></div>
            <div className="stat-card"><span className="stat-num">{bill.usage.monthly_quota}</span><span>Monthly quota</span></div>
            <div className="stat-card"><span className="stat-num">{bill.usage.topup_credits}</span><span>Top-up balance</span></div>
            <div className="stat-card"><span className="stat-num">{fmtPaise(bill.credit_price_paise)}</span><span>per credit</span></div>
          </div>

          <h3 style={{ marginTop: 26 }}>Subscription plans</h3>
          <div className="stat-grid">
            {catalogue.map((p) => (
              <div className="stat-card" key={p.id}>
                <span className="stat-num">{p.id}</span>
                <span>{p.monthly_quota.toLocaleString("en-IN")} cr/mo · {fmtPaise(p.monthly_price_paise)}/mo</span>
                <button
                  className="ghost"
                  style={{ marginTop: 8 }}
                  disabled={busy}
                  onClick={() => subscribe(p.id)}
                >
                  {bill.plan === p.id ? "Current plan" : busy === "plan" ? "…" : `Subscribe ${p.id}`}
                </button>
              </div>
            ))}
          </div>

          <h3 style={{ marginTop: 26 }}>Buy top-up credits</h3>
          <div className="grid2" style={{ maxWidth: 420 }}>
            <label className="field">
              <span className="field-label">Credits (non-expiring)</span>
              <input
                type="number" min={100} step={100} value={topup}
                onChange={(e) => setTopup(parseInt(e.target.value, 10) || 0)}
              />
            </label>
            <div className="field"><span className="field-label">&nbsp;</span>
              <button className="primary" disabled={busy || topup <= 0} onClick={buyTopup}>
                {busy === "topup" ? "Creating…" : `Buy ${fmtPaise(topup * (bill.credit_price_paise || 0))}`}
              </button>
            </div>
          </div>

          {bill.dev_enabled ? (
            <div className="invite-result" style={{ marginTop: 20 }}>
              <span className="muted" style={{ flex: 1 }}>
                Demo mode (no Razorpay key): grant credits or apply a plan locally.
              </span>
              <button className="ghost" disabled={busy} onClick={() => simulate("credits")}>
                {busy === "sim" ? "…" : "+500 demo credits"}
              </button>
              <button className="ghost" disabled={busy} onClick={() => simulate("plan")}>
                {busy === "sim" ? "…" : "Apply growth plan"}
              </button>
            </div>
          ) : null}

          <h3 style={{ marginTop: 26 }}>Recent orders</h3>
          {!bill.orders.length ? <p className="muted">No purchases yet.</p> : null}
          <div className="doc-table">
            {bill.orders.map((o) => (
              <div className="doc-row" key={o.id}>
                <div className="doc-main">
                  <strong>{o.type === "plan" ? "Plan" : "Top-up"}{o.plan ? ` · ${o.plan}` : ""}</strong>
                  <span className={`status-badge ${o.status === "paid" ? "" : "warn"}`}>{o.status}</span>
                </div>
                <div className="doc-meta">
                  <span>{o.credits?.toLocaleString("en-IN")} credits · {fmtPaise(o.amount_paise)}</span>
                  <span>{new Date(o.created_at).toLocaleString()}</span>
                </div>
              </div>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}

export default function App() {
  const [session, setSession] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(SESSION_KEY) || "null") || { token: "", user: null, partnerName: "" };
    } catch {
      return { token: "", user: null, partnerName: "" };
    }
  });
  const token = session.token || "";
  const user = session.user || null;
  const partnerName = session.partnerName || "";
  const [connected, setConnected] = useState(false);
  const [tab, setTab] = useState("dashboard");
  const [docId, setDocId] = useState(null);
  const [serverOk, setServerOk] = useState(null);
  const [usage, setUsage] = useState(null);

  // Public invite-link route: /invite/<token> shows the accept screen.
  const inviteToken = useMemo(() => {
    const m = window.location.pathname.match(/^\/invite\/([^/]+)/);
    return m ? decodeURIComponent(m[1]) : null;
  }, []);

  useEffect(() => {
    checkHealth().then(() => setServerOk(true)).catch(() => setServerOk(false));
  }, []);

  // Restore a persisted session by validating the stored token on load. Only
  // a 401 (expired/revoked session) forces a sign-out; a transient network
  // failure keeps the session so the user isn't logged out by a hiccup.
  const triedRestore = useRef(false);
  useEffect(() => {
    if (!token || triedRestore.current) return;
    triedRestore.current = true;
    getDashboard(token)
      .then(() => setConnected(true))
      .catch((err) => {
        if (err instanceof AuthError) {
          setSession((s) => ({ ...s, token: "", user: null }));
          try { localStorage.removeItem(SESSION_KEY); } catch {}
        }
      });
  }, [token]);

  // Called by AuthPanel after login or signup succeeds.
  const authenticate = async (res) => {
    if (!res.session_token) throw new Error("No session token returned.");
    const next = { token: res.session_token, user: res.user || null, partnerName: res.partner_name || "" };
    setSession(next);
    setConnected(true);
    setTab("dashboard");
    try { localStorage.setItem(SESSION_KEY, JSON.stringify(next)); } catch {}
    refreshUsage(res.session_token);
    return true;
  };

  const refreshUsage = (key = token) => {
    if (!key) return;
    getUsage(key).then(setUsage).catch(() => {});
  };

  // Refresh the credit meter whenever we come back from a review/upload.
  useEffect(() => {
    if (connected && !docId) refreshUsage();
  }, [connected, docId, token]);

  const disconnect = () => {
    setConnected(false);
    setDocId(null);
    setSession({ token: "", user: null, partnerName: "" });
    setUsage(null);
    try { localStorage.removeItem(SESSION_KEY); } catch {}
  };

  // Sign out explicitly: revoke the session server-side, then clear locally.
  const signOut = async () => {
    if (token) {
      try { await logout(token); } catch { /* session may already be stale */ }
    }
    disconnect();
  };

  // If a user is already signed in and hits an invite link, still let them
  // see the accept page (it's public). Rendered outside the logged-in shell.
  if (inviteToken) {
    return (
      <div className="app">
        <main>
          <InviteAccept token={inviteToken} onAuthenticated={authenticate} />
        </main>
      </div>
    );
  }

  const body = !connected ? (
    <AuthPanel onAuthenticated={authenticate} serverOk={serverOk} />
  ) : docId ? (
    <ReviewPanel docId={docId} apiKey={token} onReset={() => setDocId(null)} />
  ) : tab === "dashboard" ? (
    <DashboardPanel apiKey={token} onOpenDoc={(id) => setDocId(id)} onGotoUpload={() => setTab("upload")} />
  ) : tab === "docs" ? (
    <DocumentsList apiKey={token} onOpen={(id) => setDocId(id)} />
  ) : tab === "usage" ? (
    <UsagePanel apiKey={token} />
  ) : tab === "billing" && user?.role !== "analyst" ? (
    <BillingPanel apiKey={token} />
  ) : tab === "audit" ? (
    <AuditPanel apiKey={token} />
  ) : tab === "members" && user?.role !== "analyst" ? (
    <MembersPanel apiKey={token} currentUser={user} />
  ) : (
    <div className="panel"><UploadPanel apiKey={token} onUploaded={(id) => setDocId(id)} /></div>
  );

  if (!connected) {
    return (
      <div className="app">
        <main>{body}</main>
      </div>
    );
  }

  return (
    <div className="app desktop">
      <aside className="sidebar">
        <div className="brand brand-block">
          <span className="logo-mark">H</span>
          <span className="brand-name">HaloHubX</span>
        </div>
        <nav className="side-nav">
          {NAV.filter((n) => (user?.role === "analyst" ? !n.ownerOnly : true)).map((n) => (
            <button
              key={n.id}
              className={`side-item ${tab === n.id ? "active" : ""}`}
              onClick={() => { setTab(n.id); setDocId(null); }}
            >
              <span className="side-icon">{n.icon}</span>
              {n.label}
            </button>
          ))}
        </nav>
        <div className="side-foot">
          <button className="ghost full" onClick={disconnect}>Sign out</button>
        </div>
      </aside>
      <div className="content">
        <header className="topbar">
          <div className="page-title">
            {NAV.find((n) => n.id === tab)?.label ||
              (docId ? "Review document" : "")}
          </div>
          <div className="topbar-right">
            <CreditMeter usage={usage} compact />
            <span className="user-badge" title={user?.email || ""}>
              {user?.name || "Console user"}
              <em className="role-tag">{user?.role === "analyst" ? "analyst" : "owner"}</em>
            </span>
            <button className="logout-btn" onClick={signOut} title="Sign out">
              Sign out
            </button>
          </div>
        </header>
        <main>{body}</main>
        <footer className="footer muted">HaloHubX Document Engine · Partner console</footer>
      </div>
    </div>
  );
}

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
  updateMember,
  removeMember,
  uploadBatch,
  batchStatus,
  retryFailed,
  uploadDocument,
  deleteDocument,
  restoreDocument,
  approveDelete,
  getBin,
  verifyPayment,
  searchDocuments,
} from "./api.js";

const THRESHOLD = 0.85;
const SESSION_KEY = "halohubx.session.v1";

// --- Toast feedback (event-based, mounted once in the app shell) ---
const TOAST_EVENT = "halohubx-toast";
export function toast(message, type = "ok", opts = {}) {
  try {
    window.dispatchEvent(new CustomEvent(TOAST_EVENT, { detail: { message, type, ...opts } }));
  } catch { /* ignore */ }
}

function ToastItem({ t, onDone }) {
  const [left, setLeft] = useState(t.undoSecs ? t.undoSecs : null);
  useEffect(() => {
    if (!t.undoSecs) return;
    const t0 = Date.now();
    const iv = setInterval(() => {
      const el = t.undoSecs - Math.floor((Date.now() - t0) / 1000);
      setLeft(Math.max(0, el));
      if (el <= 0) clearInterval(iv);
    }, 200);
    return () => clearInterval(iv);
  }, []);
  return (
    <div className={`toast ${t.type === "err" ? "err" : "ok"}`}>
      <span className="toast-msg">{t.message}</span>
      {t.actionLabel ? (
        <button
          className="toast-action"
          onClick={() => { t.onAction?.(); onDone(); }}
        >
          {t.actionLabel}{left != null ? ` (${left}s)` : ""}
        </button>
      ) : null}
    </div>
  );
}

function ToastHost() {
  const [items, setItems] = useState([]);
  useEffect(() => {
    const onToast = (e) => {
      const id = Math.random().toString(36).slice(2);
      const dur = e.detail.undoSecs ? e.detail.undoSecs * 1000 : 4500;
      setItems((prev) => [...prev, { id, ...e.detail }]);
      setTimeout(() => setItems((prev) => prev.filter((t) => t.id !== id)), dur);
    };
    window.addEventListener(TOAST_EVENT, onToast);
    return () => window.removeEventListener(TOAST_EVENT, onToast);
  }, []);
  return (
    <div className="toast-host">
      {items.map((t) => (
        <ToastItem
          key={t.id}
          t={t}
          onDone={() => setItems((prev) => prev.filter((x) => x.id !== t.id))}
        />
      ))}
    </div>
  );
}

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
  const [removeTarget, setRemoveTarget] = useState(null); // invoice index awaiting confirmation
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
          setError(s.error || "Extraction failed. Check the backend logs.");
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
      toast(`Approved — ${r.status}${r.webhook_delivered ? " · webhook delivered" : ""}`);
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

  const doRemoveInvoice = () => {
    const i = removeTarget;
    if (i == null) return;
    setRemoveTarget(null);
    const removedInv = data?.result?.invoices?.[i];
    removeInvoice(i);
    // Undo window: if the user grabs it fast enough, re-insert right back at
    // the same slot so line-item edits made before removal survive too.
    toast(`Removed invoice #${i + 1}`, "err", {
      actionLabel: "Undo",
      undoSecs: 5,
      onAction: () => {
        setData((d) => {
          if (!d || !removedInv) return d;
          const invoices = [...d.result.invoices];
          invoices.splice(Math.min(i, invoices.length), 0, removedInv);
          return { ...d, result: { ...d.result, invoices, invoice_count: invoices.length } };
        });
        toast("Invoice restored.");
      },
    });
  };

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
            <InvoiceEditor key={i} invoice={inv} index={i} onChange={editInvoice}
                           onRemove={() => setRemoveTarget(i)} />
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

      {removeTarget != null ? (
        <ConfirmModal
          title="Remove invoice?"
          body={`This action cannot be reverted. Invoice #${removeTarget + 1} will be removed from the extracted result. You can undo for 5 seconds. I understand.`}
          confirmLabel="Remove invoice"
          onCancel={() => setRemoveTarget(null)}
          onConfirm={doRemoveInvoice}
        />
      ) : null}
    </div>
  );
}

function UploadPanel({ apiKey, onUploaded, onGotoDocs }) {
  const [files, setFiles] = useState([]);
  const [clientName, setClientName] = useState("");
  const [webhookUrl, setWebhookUrl] = useState("");
  const [phase, setPhase] = useState("select"); // select | uploading | processing | done
  const [uploaded, setUploaded] = useState(0);
  const [docs, setDocs] = useState([]); // {document_id, filename, status, error}
  const [rejected, setRejected] = useState([]); // {filename, reason}
  const [err, setErr] = useState("");
  const [retrying, setRetrying] = useState(false);
  const docsRef = useRef({});
  const timerRef = useRef(null);

  const CHUNK = 20; // files per /upload/batch request (keeps each request small)

  const pick = (e) => {
    const list = Array.from(e.target.files || []);
    setFiles(list);
    setErr("");
    setRejected([]);
    setDocs([]);
    setUploaded(0);
    setPhase("select");
  };

  const clearAll = () => {
    if (phase === "uploading" || phase === "processing") return;
    setFiles([]);
    setDocs([]);
    setRejected([]);
    setUploaded(0);
    setPhase("select");
  };

  const retryFails = async () => {
    setRetrying(true);
    try {
      const r = await retryFailed(apiKey, true);
      setRetrying(false);
      if (r.queued > 0) {
        toast(`Re-queued ${r.queued} failed document${r.queued !== 1 ? "s" : ""}.`);
        onGotoDocs();
      } else {
        toast("No failed documents to retry.", "err");
      }
    } catch (e) {
      setRetrying(false);
      toast("Could not retry failed documents.", "err");
    }
  };

  const doUpload = async () => {
    if (!files.length) { setErr("Select at least one PDF file."); return; }
    if (!clientName.trim()) { setErr("Enter a client name for this batch."); return; }
    setErr("");
    setPhase("uploading");
    setUploaded(0);
    const map = {};
    const rej = [];
    try {
      for (let i = 0; i < files.length; i += CHUNK) {
        const chunk = files.slice(i, i + CHUNK);
        const r = await uploadBatch(chunk, { apiKey, clientName, webhookUrl: webhookUrl.trim() });
        for (const d of r.documents || []) map[d.document_id] = { ...d, error: null };
        for (const rj of r.rejected || []) rej.push(rj);
        setUploaded(Math.min(files.length, i + chunk.length));
      }
      docsRef.current = map;
      setDocs(Object.values(map));
      setRejected(rej);
      setPhase("processing");
    } catch (e) {
      setPhase("select");
      let msg = String(e.message || e);
      try {
        const parsed = JSON.parse(msg.slice(msg.indexOf(":") + 1).trim());
        msg = parsed.message || (parsed.error === "insufficient_credits" ? parsed.message : msg);
      } catch { /* keep raw */ }
      setErr(msg);
    }
  };

  useEffect(() => {
    if (phase !== "processing") return;
    const ids = Object.keys(docsRef.current);
    if (!ids.length) { setPhase("done"); return; }
    const poll = async () => {
      try {
        const r = await batchStatus(ids, apiKey);
        let pending = 0;
        for (const d of r.documents || []) {
          if (docsRef.current[d.document_id]) {
            docsRef.current[d.document_id].status = d.status;
            docsRef.current[d.document_id].error = d.error;
          }
          if (d.status !== "completed" && d.status !== "failed") pending++;
        }
        setDocs(Object.values(docsRef.current));
        if (pending === 0) {
          clearInterval(timerRef.current);
          const done = Object.values(docsRef.current).filter((d) => d.status === "completed").length;
          toast(`Batch finished — ${done} of ${ids.length} documents extracted.`);
          setPhase("done");
        }
      } catch { /* transient failure; keep polling */ }
    };
    poll();
    timerRef.current = setInterval(poll, 2500);
    return () => clearInterval(timerRef.current);
  }, [phase]);

  const totalFiles = files.length;
  const doneCount = docs.filter((d) => d.status === "completed").length;
  const failedCount = docs.filter((d) => d.status === "failed").length;

  const statusClass = (s) =>
    s === "completed" ? "ok" : s === "failed" ? "error" : "muted";

  return (
    <div className="upload">
      <div className="hero">
        <h1>HaloHubX Document Engine</h1>
        <p className="muted">Upload GST invoices / POs in bulk — pick hundreds of PDFs at once.</p>
      </div>

      {phase === "select" ? (
        <div className="card">
          <label className="field">
            <span className="field-label">Client name (applies to this whole batch)</span>
            <input value={clientName} onChange={(e) => setClientName(e.target.value)} placeholder="e.g. InfraBuild Steels Pvt Ltd" />
          </label>
          <label className="field">
            <span className="field-label">Webhook URL (optional)</span>
            <input value={webhookUrl} onChange={(e) => setWebhookUrl(e.target.value)} placeholder="https://your-erp.example.com/webhook" />
          </label>
          <label className="drop">
            <input type="file" accept=".pdf" multiple onChange={pick} />
            {files.length ? (
              <strong>{files.length} file{files.length !== 1 ? "s" : ""} selected</strong>
            ) : (
              <span>Drop PDFs here or click to browse — select multiple / Ctrl+A a folder</span>
            )}
          </label>
          {files.length > 0 && (
            <div className="file-preview">
              {files.slice(0, 5).map((f) => (
                <span key={f.name} className="file-chip">{f.name}</span>
              ))}
              {files.length > 5 && (
                <span className="file-chip muted">+{files.length - 5} more…</span>
              )}
              <button className="link" onClick={clearAll}>Clear</button>
            </div>
          )}
          {err ? <p className="error">{err}</p> : null}
          <button className="primary big" disabled={!files.length || !clientName.trim()} onClick={doUpload}>
            {files.length ? `Upload & extract ${files.length} document${files.length !== 1 ? "s" : ""}` : "Upload & extract"}
          </button>
        </div>
      ) : null}

      {phase === "uploading" ? (
        <div className="card">
          <strong className="field-label">Uploading…</strong>
          <div className="bar"><div className="bar-fill" style={{ width: `${(uploaded / totalFiles) * 100}%` }} /></div>
          <p className="muted">{uploaded} / {totalFiles} files sent</p>
        </div>
      ) : null}

      {(phase === "processing" || phase === "done") ? (
        <div className="card">
          <strong className="field-label">{phase === "processing" ? "Extracting in the background…" : "Batch complete"}</strong>
          <div className="bar"><div className="bar-fill" style={{ width: `${((doneCount + failedCount) / Math.max(1, totalFiles)) * 100}%` }} /></div>
          <p className="muted">
            {doneCount} done · {failedCount} failed · {totalFiles - doneCount - failedCount} in progress
          </p>

          {rejected.length > 0 && (
            <div className="rejected-list">
              <strong className="field-label">Skipped {rejected.length} invalid file{rejected.length !== 1 ? "s" : ""}</strong>
              {rejected.slice(0, 8).map((r) => (
                <p key={r.filename} className="error small">{r.filename} — {r.reason}</p>
              ))}
              {rejected.length > 8 && <p className="muted small">…and {rejected.length - 8} more</p>}
            </div>
          )}

          {docs.length > 0 && (
            <div className="batch-list">
              {docs.slice(0, 12).map((d) => (
                <div key={d.document_id} className="batch-row">
                  <span className="file-chip">{d.filename}</span>
                  <span className={statusClass(d.status)}>{d.status}</span>
                </div>
              ))}
              {docs.length > 12 && (
                <div className="batch-row"><span className="muted">…and {docs.length - 12} more files</span></div>
              )}
            </div>
          )}

          {phase === "done" ? (
            <div className="row gap">
              <button className="primary" onClick={() => onGotoDocs()}>View documents</button>
              {failedCount > 0 && (
                <button className="ghost" disabled={retrying} onClick={retryFails}>
                  {retrying ? "Retrying…" : `Retry ${failedCount} failed`}
                </button>
              )}
              <button className="ghost" onClick={clearAll}>Upload more</button>
            </div>
          ) : null}
        </div>
      ) : null}
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

function DocsTable({ docs, loading, error, onOpen, empty, onDelete, role }) {
  if (error) return <p className="error">{error}</p>;
  if (loading) return <p className="muted">Loading…</p>;
  if (!docs.length) return (
    <div className="empty-state">
      <span className="empty-emoji">📄</span>
      <h3>{empty || "No documents yet"}</h3>
      <p className="muted">Upload one to start extracting invoice data.</p>
    </div>
  );
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
          <div className="row-gap">
            <button className="ghost" onClick={() => onOpen(d.document_id)}>Review</button>
            {onDelete ? (
              <button
                className="ghost danger"
                title={role === "analyst" ? "Request owner to delete" : "Delete document"}
                onClick={() => onDelete(d)}
              >
                Delete
              </button>
            ) : null}
          </div>
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

function DashboardPanel({ apiKey, onOpenDoc, onGotoUpload, onGotoDocs }) {
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
          <button className="add-doc-btn" onClick={onGotoDocs}>
            <span className="add-doc-ico">+</span>
            <span>Add document</span>
          </button>
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

function useDocumentDelete({ apiKey, role, onDeleted }) {
  const [pending, setPending] = useState(null); // doc awaiting confirmation

  const confirmDelete = async (doc) => {
    const r = await deleteDocument(doc.document_id, apiKey);
    if (r.status === "already_deleted") { onDeleted?.(); return; }
    toast(`Deleted ${doc.filename || "document"}${role === "analyst" ? " — sent to owner for approval" : ""}`, "err", {
      actionLabel: role === "owner" ? "Undo" : null,
      undoSecs: role === "owner" ? 5 : null,
      onAction: () => {
        restoreDocument(doc.document_id, apiKey).then(() => {
          toast(`Restored ${doc.filename || "document"}.`);
          onDeleted?.();
        }).catch(() => {});
      },
    });
    onDeleted?.();
  };

  const confirmPurge = async (doc) => {
    await approveDelete(doc.document_id, apiKey);
    toast(`Permanently deleted ${doc.filename || "document"}.`, "err");
    onDeleted?.();
  };

  const confirmRestore = async (doc) => {
    await restoreDocument(doc.document_id, apiKey);
    toast(`Restored ${doc.filename || "document"} from the bin.`);
    onDeleted?.();
  };

  return { pending, setPending, confirmDelete, confirmPurge, confirmRestore };
}

function ConfirmModal({ title, body, confirmLabel, onConfirm, onCancel, busy }) {
  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        <p className="modal-body">{body}</p>
        <div className="row gap" style={{ justifyContent: "flex-end" }}>
          <button className="ghost" onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="primary danger-solid" onClick={onConfirm} disabled={busy}>
            {busy ? "Working…" : confirmLabel || "Delete"}
          </button>
        </div>
      </div>
    </div>
  );
}

function BinList({ items, loading, error, onApprove, onRestore, busy }) {
  if (error) return <p className="error">{error}</p>;
  if (loading) return <p className="muted">Loading…</p>;
  if (!items.length) return (
    <div className="empty-state">
      <span className="empty-emoji">🗑️</span>
      <h3>Bin is empty</h3>
      <p className="muted">Documents deleted by analysts wait here for your approval.</p>
    </div>
  );
  return (
    <div className="doc-table">
      {items.map((d) => (
        <div className="doc-row" key={d.document_id}>
          <div className="doc-main">
            <strong title={d.document_id}>{d.filename}</strong>
            <span className="status-badge warn">Pending deletion</span>
          </div>
          <div className="doc-meta">
            <span>Asked by: {d.requested_by || "—"}</span>
            <span>{fmtDate(d.deleted_at)}</span>
          </div>
          <div className="row-gap">
            <button className="primary" disabled={busy} onClick={() => onApprove(d)}>Approve delete</button>
            <button className="ghost" disabled={busy} onClick={() => onRestore(d)}>Restore</button>
          </div>
        </div>
      ))}
    </div>
  );
}

function DocumentsList({ apiKey, role, onOpen, initialClient }) {
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [client, setClient] = useState(initialClient || "all");
  const [showBin, setShowBin] = useState(false);
  const [binItems, setBinItems] = useState([]);
  const [binLoading, setBinLoading] = useState(false);
  const [binErr, setBinErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [purgeTarget, setPurgeTarget] = useState(null);

  const isOwner = role !== "analyst";

  const load = () => {
    setLoading(true);
    setErr("");
    getDashboard(apiKey)
      .then((d) => setDocs(d))
      .catch((e) => setErr(String(e.message || e)))
      .finally(() => setLoading(false));
  };
  const dl = useDocumentDelete({ apiKey, role, onDeleted: load });
  useEffect(load, [apiKey]);

  const loadBin = () => {
    if (!isOwner) return;
    setBinLoading(true);
    setBinErr("");
    getBin(apiKey)
      .then((r) => setBinItems(r.documents || []))
      .catch((e) => setBinErr(String(e.message || e)))
      .finally(() => setBinLoading(false));
  };
  useEffect(() => { if (showBin) loadBin(); }, [showBin, apiKey]);

  const doDelete = async (doc) => {
    setBusy(true);
    try {
      await dl.confirmDelete(doc);
      setBusy(false);
      dl.setPending(null);
    } catch (e) {
      setBusy(false);
      toast("Delete failed: " + String(e.message || e), "err");
    }
  };

  const doPurge = async (doc) => {
    setBusy(true);
    try {
      await dl.confirmPurge(doc);
      setBusy(false);
      setPurgeTarget(null);
      loadBin();
    } catch (e) {
      setBusy(false);
      toast("Delete failed: " + String(e.message || e), "err");
    }
  };

  const doRestore = async (doc) => {
    setBusy(true);
    try {
      await dl.confirmRestore(doc);
      setBusy(false);
      loadBin();
    } catch (e) {
      setBusy(false);
      toast("Restore failed: " + String(e.message || e), "err");
    }
  };

  const clients = useMemo(() => ["all", ...new Set(docs.map((d) => d.client_name).filter((c) => c && c !== "—"))], [docs]);
  const filtered = useMemo(
    () => (client === "all" ? docs : docs.filter((d) => d.client_name === client)),
    [docs, client]
  );

  return (
    <div className="panel">
      <div className="recent-head">
        <div>
          <h2>{isOwner ? "Documents & Bin" : "Documents"}</h2>
          <p className="muted">{isOwner ? "Manage the workspace. Deleted items wait here for you to approve or restore." : "Review documents. Deletes need owner approval."}</p>
        </div>
        <div className="row-gap">
          {isOwner ? (
            <button className={`ghost ${showBin ? "active" : ""}`} onClick={() => setShowBin((v) => !v)}>
              {showBin ? "Show documents" : `Bin (${binItems.length})`}
            </button>
          ) : null}
          <select className="select" value={client} onChange={(e) => setClient(e.target.value)}>
            {clients.map((c) => <option key={c} value={c}>{c === "all" ? "All clients" : c}</option>)}
          </select>
          <button className="ghost" onClick={() => (showBin ? loadBin() : load())}>Refresh</button>
        </div>
      </div>

      {showBin ? (
        <BinList items={binItems} loading={binLoading} error={binErr}
                 onApprove={(d) => setPurgeTarget(d)} onRestore={doRestore} busy={busy} />
      ) : (
        <DocsTable docs={filtered} loading={loading} error={err} onOpen={onOpen}
                   onDelete={(d) => dl.setPending(d)} role={role}
                   empty={client === "all" ? "No documents yet. Upload one first." : "No documents for this client yet."} />
      )}

      {dl.pending && !showBin ? (
        <ConfirmModal
          title="Delete document?"
          body={role === "analyst"
            ? "This action cannot be reverted once approved. The owner will be notified and the deletion stays in the bin until they approve it."
            : `This action cannot be reverted. This will delete the extraction for "${dl.pending.filename}". You can undo for 5 seconds. I understand.`}
          confirmLabel="Delete"
          busy={busy}
          onCancel={() => dl.setPending(null)}
          onConfirm={() => doDelete(dl.pending)}
        />
      ) : null}

      {purgeTarget ? (
        <ConfirmModal
          title="Permanently delete?"
          body={`This action cannot be reverted. The document "${purgeTarget.filename}" and its stored PDF will be permanently removed. I understand.`}
          confirmLabel="Delete forever"
          busy={busy}
          onCancel={() => setPurgeTarget(null)}
          onConfirm={() => doPurge(purgeTarget)}
        />
      ) : null}
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
      setErr("Enter your login and password.");
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
            type="text"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="admin or you@company.com"
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

function Icon({ name, size = 20 }) {
  const p = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true };
  switch (name) {
    case "dashboard":
      return <svg {...p}><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></svg>;
    case "upload":
      return <svg {...p}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" /></svg>;
    case "docs":
      return <svg {...p}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /><line x1="16" y1="13" x2="8" y2="13" /><line x1="16" y1="17" x2="8" y2="17" /></svg>;
    case "search":
      return <svg {...p}><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>;
    case "usage":
      return <svg {...p}><path d="M22 12h-4l-3 9L9 3l-3 9H2" /></svg>;
    case "billing":
      return <svg {...p}><rect x="2" y="4" width="20" height="16" rx="2" /><line x1="2" y1="10" x2="22" y2="10" /></svg>;
    case "audit":
      return <svg {...p}><circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" /></svg>;
    case "members":
      return <svg {...p}><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></svg>;
    case "settings":
      return <svg {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg>;
    case "logout":
      return <svg {...p}><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><polyline points="16 17 21 12 16 7" /><line x1="21" y1="12" x2="9" y2="12" /></svg>;
    default:
      return null;
  }
}

function SettingsPanel({ user, partnerName, onSignOut }) {
  const initials = (user?.name || "?")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() || "")
    .join("");
  return (
    <div className="panel">
      <div className="panel-head">
        <div>
          <h2>Profile & Settings</h2>
          <p className="muted">Your account details and session info.</p>
        </div>
      </div>
      <div className="profile-card">
        <div className="avatar">{initials || "U"}</div>
        <div className="profile-meta">
          <h3>{user?.name || "Console user"}</h3>
          <p className="muted">{user?.email || "—"}</p>
          <p className="muted">Partner: <strong>{partnerName || "—"}</strong></p>
          <span className="role-tag">{user?.role === "analyst" ? "Analyst" : "Owner"}</span>
        </div>
        <button className="ghost" onClick={onSignOut}>Sign out</button>
      </div>
    </div>
  );
}

const NAV = [
  { id: "dashboard", label: "Dashboard", icon: "dashboard" },
  { id: "upload", label: "Upload", icon: "upload", ownerOnly: true },
  { id: "docs", label: "Documents", icon: "docs" },
  { id: "search", label: "Search", icon: "search" },
  { id: "usage", label: "Usage", icon: "usage" },
  { id: "billing", label: "Billing", icon: "billing", ownerOnly: true },
  { id: "audit", label: "Activity", icon: "audit" },
  { id: "members", label: "Members", icon: "members", ownerOnly: true },
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
  const [editingId, setEditingId] = useState(null);
  const [editForm, setEditForm] = useState({ name: "", email: "", role: "analyst" });
  const [removeTarget, setRemoveTarget] = useState(null);

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
      toast("Invite created — share the link.");
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

  const startEdit = (m) => {
    setEditingId(m.id);
    setEditForm({ name: m.name || "", email: m.email || "", role: m.role || "analyst" });
    setErr("");
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditForm({ name: "", email: "", role: "analyst" });
  };

  const saveEdit = async (m) => {
    const body = {};
    if (editForm.name.trim() && editForm.name.trim() !== m.name) body.name = editForm.name.trim();
    if (editForm.email.trim() && editForm.email.trim() !== m.email) body.email = editForm.email.trim();
    if (editForm.role && editForm.role !== m.role) body.role = editForm.role;
    if (!Object.keys(body).length) { cancelEdit(); return; }
    setBusy(true);
    try {
      await updateMember(apiKey, m.id, body);
      toast("Member updated.");
      cancelEdit();
      load();
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  const onRemove = async (m) => {
    setBusy(true);
    try {
      await removeMember(apiKey, m.id);
      toast(`Removed ${m.name || m.email}.`, "err");
      setRemoveTarget(null);
      load();
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
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
          editingId === m.id ? (
            <div className="doc-row member-edit-row" key={m.id}>
              <div className="member-edit-fields">
                <input
                  className="member-edit-input"
                  value={editForm.name}
                  onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                  placeholder="Name"
                />
                <input
                  className="member-edit-input"
                  value={editForm.email}
                  onChange={(e) => setEditForm({ ...editForm, email: e.target.value })}
                  type="email"
                  placeholder="Email"
                />
                <select
                  className="select member-edit-select"
                  value={editForm.role}
                  onChange={(e) => setEditForm({ ...editForm, role: e.target.value })}
                >
                  <option value="analyst">Analyst (review only)</option>
                  <option value="owner">Owner (full access)</option>
                </select>
              </div>
              <div className="row-gap">
                <button className="primary member-edit-btn" disabled={busy} onClick={() => saveEdit(m)}>
                  {busy ? "Saving…" : "Save"}
                </button>
                <button className="ghost" disabled={busy} onClick={cancelEdit}>Cancel</button>
              </div>
            </div>
          ) : (
            <div className="doc-row" key={m.id}>
              <div className="doc-main">
                <strong>{m.name || m.email}</strong>
                <span className={`status-badge ${m.role === "owner" ? "" : "warn"}`}>{m.role === "owner" ? "Owner" : "Analyst"}</span>
                {m.email === currentUser?.email ? <span className="status-badge">You</span> : null}
              </div>
              <div className="doc-meta"><span>{m.email}</span></div>
              <div className="row-gap">
                <button className="ghost" disabled={busy} onClick={() => startEdit(m)}>Edit</button>
                {m.email !== currentUser?.email ? (
                  <button className="ghost danger" disabled={busy} onClick={() => setRemoveTarget(m)}>Remove</button>
                ) : null}
              </div>
            </div>
          )
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
        <label className="field role-field">
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

      {removeTarget ? (
        <ConfirmModal
          title="Remove member?"
          body={`This removes "${removeTarget.name || removeTarget.email}" from the workspace. They keep their existing documents, but lose access to the console immediately.`}
          confirmLabel="Remove member"
          busy={busy}
          onCancel={() => setRemoveTarget(null)}
          onConfirm={() => onRemove(removeTarget)}
        />
      ) : null}
    </div>
  );
}

const SEARCH_HISTORY_KEY = "halohubx.search_history.v1";

function loadSearchHistory() {
  try { return JSON.parse(localStorage.getItem(SEARCH_HISTORY_KEY) || "[]") || []; }
  catch { return []; }
}

function saveSearchHistory(list) {
  try { localStorage.setItem(SEARCH_HISTORY_KEY, JSON.stringify(list)); } catch {}
}

function QuickSearch({ apiKey, onOpenDoc, onPickClient }) {
  const [q, setQ] = useState("");
  const [docs, setDocs] = useState([]);
  const [open, setOpen] = useState(false);
  const boxRef = useRef(null);

  useEffect(() => {
    let alive = true;
    if (!apiKey) return;
    getDashboard(apiKey)
      .then((d) => alive && setDocs(d))
      .catch(() => {});
    return () => { alive = false; };
  }, [apiKey]);

  useEffect(() => {
    const h = (e) => { if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  const ql = q.trim().toLowerCase();
  const matchedDocs = ql
    ? docs.filter((d) =>
        (d.filename || "").toLowerCase().includes(ql) ||
        (d.client_name || "").toLowerCase().includes(ql) ||
        (d.document_id || "").toLowerCase().includes(ql)
      )
    : [];
  const seen = new Set();
  const clients = ql
    ? docs
        .map((d) => d.client_name)
        .filter((c) => c && c !== "—")
        .filter((c) => !seen.has(c) && seen.add(c))
        .filter((c) => c.toLowerCase().includes(ql))
    : [];

  const close = (fn) => { setOpen(false); fn && fn(); };

  return (
    <div className={`qsearch ${open && ql ? "open" : ""}`} ref={boxRef}>
      <span className="qsearch-ico"><Icon name="search" size={16} /></span>
      <input
        className="qsearch-input"
        placeholder="Search documents, clients…"
        value={q}
        onChange={(e) => { setQ(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            if (clients.length) close(() => onPickClient?.(clients[0]));
            else if (matchedDocs.length) close(() => onOpenDoc?.(matchedDocs[0].document_id));
            else setOpen(false);
          } else if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && ql ? (
        <div className="qsearch-drop">
          {clients.length ? (
            <div className="qsearch-group">
              <span className="qsearch-label">Clients</span>
              {clients.slice(0, 5).map((c) => (
                <button key={c} className="qsearch-item" onClick={() => close(() => onPickClient?.(c))}>
                  <span className="qsearch-item-t">Client</span>
                  <span className="qsearch-item-v">{c}</span>
                </button>
              ))}
            </div>
          ) : null}
          {matchedDocs.length ? (
            <div className="qsearch-group">
              <span className="qsearch-label">Documents</span>
              {matchedDocs.slice(0, 8).map((d) => (
                <button key={d.document_id} className="qsearch-item" onClick={() => close(() => onOpenDoc?.(d.document_id))}>
                  <span className="qsearch-item-t">Doc</span>
                  <span className="qsearch-item-v">{d.filename}</span>
                  <span className="qsearch-item-m">{d.client_name || "—"}</span>
                </button>
              ))}
            </div>
          ) : null}
          {!clients.length && !matchedDocs.length ? (
            <div className="qsearch-empty">No matches for "{q.trim()}"</div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function SearchPanel({ apiKey, onOpen }) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [history, setHistory] = useState(loadSearchHistory);

  const run = async (q = "") => {
    const text = (q || query).trim();
    if (!text || busy) return;
    setErr(""); setResult(null); setBusy(true);
    try {
      const res = await searchDocuments(apiKey, text);
      setResult(res);
      setHistory((prev) => {
        const next = [
          { q: text, at: Date.now(), answer: res.answer || null },
          ...prev.filter((h) => h.q !== text),
        ].slice(0, 15);
        saveSearchHistory(next);
        return next;
      });
      if (!res.answer) toast("No answer found — try rephrasing.", "err");
    } catch (e) {
      setErr(String(e.message || e));
      toast(String(e.message || e), "err");
    } finally {
      setBusy(false);
    }
  };

  const clearHistory = () => {
    setHistory([]);
    saveSearchHistory([]);
  };

  const fmtTime = (ts) => {
    try { return new Date(ts).toLocaleString(); } catch { return ""; }
  };

  return (
    <div className="panel search-panel">
      <div className="recent-head">
        <div>
          <h2>Ask your documents</h2>
          <p className="muted">Ask a question across every processed invoice / PO — get a precise, sourced answer from the extracted data.</p>
        </div>
      </div>

      <div className="search-box">
        <input
          className="search-input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") run(); }}
          placeholder='e.g. "Which vendor has the highest invoice total this month?"'
          disabled={busy}
        />
        <button className="primary" disabled={busy || !query.trim()} onClick={run}>
          {busy ? "Searching…" : "Search"}
        </button>
      </div>

      {err ? <p className="error">{err}</p> : null}

      {busy ? (
        <div className="waiting">
          <div className="spinner" />
          <p className="muted">Reading your documents…</p>
        </div>
      ) : null}

      {result ? (
        <div className="answer-card">
          <div className="answer-main">
            <span className="answer-label">Answer</span>
            <p className="answer-text">{result.answer}</p>
          </div>
          {result.sources?.length ? (
            <div className="source-list">
              <span className="answer-label">Sources ({result.sources.length}) · {result.scanned} documents scanned</span>
              {result.sources.map((s) => (
                <button key={s.document_id} className="source-chip" onClick={() => onOpen(s.document_id)}>
                  <span className="source-name">{s.filename}</span>
                  <span className="source-meta">{s.client_name || "—"}</span>
                  <span className="source-open">Open →</span>
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      {history.length ? (
        <div className="search-history">
          <div className="history-head">
            <span className="answer-label">Recent searches</span>
            <button className="ghost small" onClick={clearHistory}>Clear</button>
          </div>
          <div className="history-list">
            {history.map((h) => (
              <button key={h.at} className="history-item" onClick={() => { setQuery(h.q); run(h.q); }}>
                <span className="history-q">"{h.q}"</span>
                <span className="history-meta">
                  {h.answer ? <span className="history-has">answered</span> : <span className="history-has no">no answer</span>}
                  <span className="history-time">{fmtTime(h.at)}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {!busy && !result && !err && !history.length ? (
        <div className="empty-state">
          <span className="empty-emoji">⌕</span>
          <h3>Search across all your documents</h3>
          <p className="muted">Try: "What was the total GST paid across all invoices?" or "Find invoices from vendor XYZ."</p>
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
    run("topup", async () => {
      const chk = await checkoutTopup(apiKey, topup);
      if (!window.Razorpay) {
        const s = document.createElement("script");
        s.src = "https://checkout.razorpay.com/v1/checkout.js";
        s.async = true;
        await new Promise((res, rej) => { s.onload = res; s.onerror = rej; document.head.appendChild(s); });
      }
      const rzp = new window.Razorpay({
        key: chk.key_id,
        amount: chk.amount_paise,
        currency: chk.currency,
        name: "HaloHubX",
        description: `${chk.credits} credits top-up`,
        order_id: chk.razorpay_order_id,
        prefill: { name: "HaloHubX Partner" },
        handler: async (resp) => {
          await verifyPayment(apiKey, {
            razorpay_order_id: resp.razorpay_order_id,
            razorpay_payment_id: resp.razorpay_payment_id,
            razorpay_signature: resp.razorpay_signature,
          });
          toast(`Payment successful! ${chk.credits} credit(s) added.`);
          setMsg(`Payment successful! ${chk.credits} credit(s) added to your balance.`);
          load();
        },
        modal: { ondismiss: () => setBusy("") },
      });
      rzp.open();
      return null;
    }, "");
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
  const [docsClient, setDocsClient] = useState(null);
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
        } else {
          // Backend hiccup — still enter the app; panels show their own errors.
          setConnected(true);
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
        <ToastHost />
      </div>
    );
  }

  const body = !connected ? (
    token ? (
      <div className="panel splash-panel">
        <div className="spinner" />
        <p className="muted">Restoring session…</p>
      </div>
    ) : (
      <AuthPanel onAuthenticated={authenticate} serverOk={serverOk} />
    )
  ) : docId ? (
    <ReviewPanel docId={docId} apiKey={token} onReset={() => setDocId(null)} />
  ) : tab === "dashboard" ? (
    <DashboardPanel apiKey={token} onOpenDoc={(id) => setDocId(id)} onGotoUpload={() => setTab("upload")} onGotoDocs={() => { setTab("docs"); setDocId(null); setDocsClient(null); }} />
  ) : tab === "docs" ? (
    <DocumentsList key={docsClient || "all"} apiKey={token} role={user?.role || "owner"} initialClient={docsClient} onOpen={(id) => setDocId(id)} />
  ) : tab === "search" ? (
    <SearchPanel apiKey={token} onOpen={(id) => setDocId(id)} />
  ) : tab === "usage" ? (
    <UsagePanel apiKey={token} />
  ) : tab === "billing" && user?.role !== "analyst" ? (
    <BillingPanel apiKey={token} />
  ) : tab === "audit" ? (
    <AuditPanel apiKey={token} />
  ) : tab === "members" && user?.role !== "analyst" ? (
    <MembersPanel apiKey={token} currentUser={user} />
  ) : tab === "settings" ? (
    <SettingsPanel user={user} partnerName={partnerName} onSignOut={signOut} />
  ) : (
    <div className="panel"><UploadPanel apiKey={token} onUploaded={(id) => setDocId(id)} onGotoDocs={() => { setTab("docs"); setDocId(null); setDocsClient(null); }} /></div>
  );

  if (!connected) {
    return (
      <div className="app">
        <main>{body}</main>
        <ToastHost />
      </div>
    );
  }

  return (
    <div className="app desktop">
      <aside className="sidebar">
        <div className="brand brand-block" title={user?.email || ""}>
          <span className="nav-avatar">{user?.name?.split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase() || "").join("") || "U"}</span>
          <span className="brand-name">{user?.name || "Console user"}</span>
        </div>
        <nav className="side-nav">
          {NAV.filter((n) => (user?.role === "analyst" ? !n.ownerOnly : true)).map((n) => (
            <button
              key={n.id}
              className={`side-item ${tab === n.id ? "active" : ""}`}
              title={n.label}
              onClick={() => { setTab(n.id); setDocId(null); setDocsClient(null); }}
            >
              <span className="side-icon"><Icon name={n.icon} /></span>
              <span className="side-label">{n.label}</span>
            </button>
          ))}
        </nav>
        <div className="side-foot">
          <button className="side-item" title="Settings" onClick={() => { setTab("settings"); setDocId(null); }}>
            <span className="side-icon"><Icon name="settings" /></span>
            <span className="side-label">Settings</span>
          </button>
          <button className="side-item" title="Sign out" onClick={signOut}>
            <span className="side-icon"><Icon name="logout" /></span>
            <span className="side-label">Sign out</span>
          </button>
        </div>
      </aside>
      <div className="content">
        <header className="topbar">
          <div className="page-title">
            {NAV.find((n) => n.id === tab)?.label ||
              (tab === "settings" ? "Profile & Settings" : "") ||
              (docId ? "Review document" : "")}
          </div>
          <QuickSearch
            apiKey={token}
            onOpenDoc={(id) => setDocId(id)}
            onPickClient={(c) => { setDocsClient(c); setTab("docs"); setDocId(null); }}
          />
          <div className="topbar-right">
            <CreditMeter usage={usage} compact />
            <button className="profile-btn" onClick={() => { setTab("settings"); setDocId(null); }} title="Profile & settings">
              <span className="avatar">{(["", user?.name].includes(user?.name) ? "?" : user.name.split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase() || "").join("")) || "U"}</span>
              <span className="profile-info">
                <span className="profile-name">{user?.name || "Console user"}</span>
                <em className="role-tag">{user?.role === "analyst" ? "analyst" : "owner"}</em>
              </span>
            </button>
          </div>
        </header>
        <main>{body}</main>
        <footer className="footer muted">HaloHubX Document Engine · Partner console</footer>
      </div>
      <ToastHost />
    </div>
  );
}

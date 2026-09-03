// @ts-nocheck
const API_BASE = ""; // team backend serves /upload, /status, /hitl at root (proxied)

// Thrown on an unauthenticated (401) response so the console can clear an
// expired/revoked session and bounce to the sign-in screen.
export class AuthError extends Error {
  constructor(message) {
    super(message);
    this.name = "AuthError";
  }
}

async function jfetch(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, options);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = JSON.stringify(body.detail || body);
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json();
}

async function authedFetch(path, apiKey, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (apiKey) headers.Authorization = `Bearer ${apiKey}`;
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (res.status === 401) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = JSON.stringify(body.detail || body);
    } catch {
      /* ignore */
    }
    throw new AuthError(`${res.status}: ${detail}`);
  }
  if (!res.ok) throw new Error(`${res.status}: ${res.statusText}`);
  return res.json();
}

export async function uploadDocument(file, { apiKey, clientName, webhookUrl }) {
  const form = new FormData();
  form.append("file", file);
  form.append("client_name", clientName);
  if (webhookUrl) form.append("webhook_url", webhookUrl);
  return authedFetch("/upload", apiKey, { method: "POST", body: form });
}

export async function getStatus(documentId, apiKey) {
  return authedFetch(`/status/${documentId}`, apiKey);
}

export async function retrieve(documentId, apiKey) {
  return authedFetch(`/retrieve/${documentId}`, apiKey);
}

export async function listDocuments(apiKey) {
  return authedFetch(`/hitl/documents`, apiKey);
}

export async function getDocumentForReview(documentId, apiKey) {
  return authedFetch(`/hitl/document/${documentId}`, apiKey);
}

export async function confirmDocument(documentId, apiKey, result) {
  return authedFetch(`/hitl/document/${documentId}/confirm`, apiKey, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ result }),
  });
}

export async function checkHealth() {
  return jfetch(`/`);
}

// Console auth — returns { api_key, partner_id, partner_name, user } on success.
export async function login(body) {
  return jfetch(`/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function signup(body) {
  return jfetch(`/auth/signup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function logout(apiKey) {
  return authedFetch(`/auth/logout`, apiKey, { method: "POST" });
}

export async function listAllDocuments(apiKey) {
  return authedFetch(`/documents?review_status=all`, apiKey);
}

// Dashboard view = merge the two lists into a single per-document array with
// status, needs_review, invoice_count, client_name, filename, created_at.
export async function getDashboard(apiKey) {
  const [hitl, all] = await Promise.all([
    authedFetch(`/hitl/documents`, apiKey),
    listAllDocuments(apiKey),
  ]);
  // /hitl/documents -> { documents: [...] } (id + client_name)
  // /documents?review_status=all -> bare array (document_id + status + needs_review)
  const base = (hitl.documents || []).map((d) => ({
    document_id: d.id || d.document_id,
    filename: d.filename,
    client_name: d.client_name || "—",
    status: d.status,
    created_at: d.created_at,
  }));
  const byId = new Map(base.map((d) => [d.document_id, d]));
  const list = Array.isArray(all) ? all : all.documents || [];
  for (const d of list) {
    const existing = byId.get(d.document_id) || {
      document_id: d.document_id, filename: d.filename, client_name: "—",
    };
    existing.status = d.status;
    existing.needs_review = Boolean(d.needs_review);
    existing.invoice_count = d.invoice_count ?? 0;
    existing.created_at = existing.created_at || d.created_at;
    byId.set(d.document_id, existing);
  }
  return Array.from(byId.values());
}

export async function getUsage(apiKey) {
  return authedFetch(`/usage`, apiKey);
}

// Download the original PDF for side-by-side review (returns a blob URL).
export async function documentFileUrl(documentId, apiKey) {
  const res = await fetch(`/documents/${documentId}/file`, {
    headers: { Authorization: `Bearer ${apiKey}` },
  });
  if (!res.ok) throw new Error(`${res.status}: could not load PDF`);
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export async function approveDocument(documentId, apiKey) {
  return authedFetch(`/documents/${documentId}/approve`, apiKey, { method: "POST" });
}

export async function getAudit(apiKey, limit = 50) {
  return authedFetch(`/audit?limit=${limit}`, apiKey);
}

export async function listMembers(apiKey) {
  return authedFetch(`/auth/partner/members`, apiKey);
}

export async function createMember(apiKey, body) {
  return authedFetch(`/auth/partner/members`, apiKey, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function inviteInfo(token) {
  return jfetch(`/auth/invite/${token}`);
}

export async function acceptInvite(token, body) {
  return jfetch(`/auth/invite/${token}/accept`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

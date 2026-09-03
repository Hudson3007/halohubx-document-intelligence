// Content + data for the HaloHubX marketing site.
//
// The benchmark numbers shown on the "Accuracy" section are read live from
// /benchmark.json at runtime (see components.jsx). Re-run benchmark.py to
// regenerate that file; the live site picks up the change on refresh.
// This object is only the offline fallback if the JSON can't be fetched.

export const benchmarkData = {
  // This is the honest default state: we do NOT headline a single-document
  // measurement as if it were a product stat. benchmark.py overwrites this
  // file with the latest measured run; the site shows a sample-size disclosure
  // until a representative (multi-document, independently labelled) set exists.
  overall: null,
  totalScored: 0,
  documents: 1,
  perField: [],
  note:
    "Our accuracy chart is populated by benchmark.py from independently " +
    "human-labelled documents — not from marketing estimates. Run it on your " +
    "own PDF set and the same chart reflects your measured numbers.",
};

export const sampleResult = {
  invoice_count: 2,
  invoices: [
    {
      document_type: "gst_invoice",
      vendor: { name: "BYTESWARE ELECTRONICS", gstin: "29AABCT1332L1Z5", confidence: 1.0 },
      invoice_details: { invoice_number: "IN-27992", date: "2023-04-12", confidence: 1.0 },
      line_items: [
        { description: "Hex Bolts M8x40", hsn_sac_code: "73181500", quantity: 500, unit_price: 2.1, total_value: 1239.0, confidence: 0.97 },
        { description: "Washers 8mm SS", hsn_sac_code: "73182200", quantity: 1000, unit_price: 0.35, total_value: 413.0, confidence: 0.95 },
      ],
      total_amount: { value: 2116.0, confidence: 1.0 },
      flags: [],
    },
    {
      document_type: "gst_invoice",
      vendor: { name: "BYTESWARE ELECTRONICS", gstin: "29AABCT1332L1Z5", confidence: 0.99 },
      invoice_details: { invoice_number: "IN-27993", date: "2023-05-18", confidence: 0.96 },
      line_items: [
        { description: "Anchor Fasteners 10mm", hsn_sac_code: "73181500", quantity: 200, unit_price: 5.6, total_value: 1323.2, confidence: 0.9 },
      ],
      total_amount: { value: 1323.2, confidence: 0.98 },
      flags: ["handwritten_notes_detected"],
    },
  ],
  document_flags: [],
};

export const features = [
  {
    title: "Multi-invoice extraction",
    body: "One PDF, many invoices. HaloHubX identifies every distinct invoice in a batch and returns them all — no need to split files first.",
    icon: "▤",
  },
  {
    title: "Confidence for every field",
    body: "Each extracted value carries a self-reported confidence score, so your team knows exactly which fields deserve a second look.",
    icon: "◎",
  },
  {
    title: "Human-in-the-loop review",
    body: "Low-confidence documents land in a staging queue where a person corrects fields against the original PDF before it's pushed to the ERP.",
    icon: "✎",
  },
  {
    title: "ERP integration",
    body: "Clean structured JSON via REST + webhooks, or a lightweight sync agent for Tally and other on-prem ERP systems.",
    icon: "⇄",
  },
  {
    title: "Multi-tenant by design",
    body: "Partner → client → document isolation baked into the schema. Every reseller sees only their own clients' data.",
    icon: "⊞",
  },
  {
    title: "GST-aware",
    body: "Understands GSTIN, HSN/SAC codes, CGST/SGST/IGST splitting, and Indian invoice formats from messy scans and photos.",
    icon: "₹",
  },
];

export const flowSteps = [
  { k: "1", t: "Upload a PDF", d: "Any invoice, PO, receipt or contract — clean scan or messy screenshot." },
  { k: "2", t: "AI extracts structure", d: "Vision model pulls vendors, GSTINs, line items, taxes and totals with confidence scores." },
  { k: "3", t: "Human reviews (only when needed)", d: "Low-confidence fields are highlighted and corrected against the original." },
  { k: "4", t: "Push to ERP", d: "Structured JSON lands in Odoo, Zoho, or Tally — via webhook or sync agent." },
];

export const processSteps = [
  { num: "01", title: "Upload", desc: "Drop one PDF or a batch. It's processed synchronously." },
  { num: "02", title: "Extract", desc: "Vision model builds structured invoice JSON with confidence scores." },
  { num: "03", title: "Review", desc: "Humans correct only the low-confidence fields." },
  { num: "04", title: "Deliver", desc: "Webhook or sync agent moves data into the ERP." },
];

export const integrations = ["Odoo", "Zoho", "NetSuite", "Tally", "Custom REST"];
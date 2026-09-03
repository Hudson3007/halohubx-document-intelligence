import { useEffect, useRef, useState } from "react";
import { integrations, sampleResult } from "./data.js";
import { benchmarkData } from "./data.js";

function SectionHead({ kicker, title, body }) {
  return (
    <div className="sec-head">
      <span className="kicker">{kicker}</span>
      <h2>{title}</h2>
      {body ? <p>{body}</p> : null}
    </div>
  );
}

export function Navbar({ open, onToggle, onClose }) {
  const links = [
    ["#how", "How it works"],
    ["#review", "Human review"],
    ["#features", "Features"],
    ["#benchmark", "Accuracy"],
    ["#pricing", "Pricing"],
  ];
  return (
    <nav className="nav">
      <div className="nav-inner">
        <a className="brand" href="#top" onClick={onClose}>
          <span className="logo-mark">H</span>
          <span className="brand-name">HaloHubX</span>
        </a>
        <button className="menu-btn" onClick={onToggle} aria-label="Menu">
          <span className="menu-icon">{open ? "×" : "☰"}</span>
        </button>
        <div className={`nav-links ${open ? "open" : ""}`}>
          {links.map(([href, label]) => (
            <a key={href} href={href} onClick={onClose}>{label}</a>
          ))}
          <a href="#pricing" className="btn btn-primary nav-cta" onClick={onClose}>Book a demo</a>
        </div>
      </div>
    </nav>
  );
}

export function Hero() {
  return (
    <header className="hero" id="top">
      <div className="hero-inner">
        <span className="kicker">Document Intelligence for Indian SMEs</span>
        <h1>Turn messy invoices and PO&apos;s into clean data your ERP can use.</h1>
        <p className="hero-sub">
          HaloHubX extracts vendors, GSTINs, line items and totals from scanned
          and digital documents — with confidence scores and human review before
          anything reaches Odoo, Zoho, or Tally.
        </p>
        <div className="hero-actions">
          <a href="#pricing" className="btn btn-primary btn-lg">Book a demo</a>
          <a href="#how" className="btn btn-ghost btn-lg">See how it works</a>
        </div>
        <div className="hero-stats">
          <div><strong>6+</strong><span>GST fields</span></div>
          <div><strong>Multi</strong><span>invoices / file</span></div>
          <div><strong>0</strong><span>heavy licence fees</span></div>
        </div>
      </div>
      <HeroSample/>
    </header>
  );
}

function HeroSample() {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(sampleResult, null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard may be blocked; ignore */ }
  };
  return (
    <div className="hero-card">
      <div className="hero-card-head">
        <span className="dot"/><span className="dot"/><span className="dot"/>
        <span className="hero-card-title">extracted_invoice.json</span>
        <button className="copy-btn" onClick={copy}>{copied ? "Copied ✓" : "Copy"}</button>
      </div>
      <pre className="json" onClick={copy}>{JSON.stringify(sampleResult, null, 2)}</pre>
    </div>
  );
}

export function Process() {
  const steps = [
    { num: "01", title: "Upload", desc: "Drop one PDF or a batch. Processed synchronously." },
    { num: "02", title: "Extract", desc: "Vision model builds structured JSON with confidence." },
    { num: "03", title: "Review", desc: "Humans correct only the low-confidence fields." },
    { num: "04", title: "Deliver", desc: "Webhook or sync agent moves data into the ERP." },
  ];
  return (
    <section className="strip">
      <div className="strip-inner">
        {steps.map((s) => (
          <div className="strip-step" key={s.num}>
            <span className="strip-num">{s.num}</span>
            <h3>{s.title}</h3>
            <p>{s.desc}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

export function HowItWorks({ id = "how" }) {
  return (
    <section className="section" id={id}>
      <div className="container">
        <SectionHead
          kicker="How it works"
          title="From a messy PDF to ERP-ready data in three steps"
          body="No templates to design, no rules to write. Show the model a document and it learns the layout."
        />
        <div className="how-grid">
          <HowCard n="01" t="Upload" d="Send a PDF via a single REST call or our drop-in UI. Works with scans and photos, not just clean digital files."/>
          <HowCard n="02" t="Extract" d="The vision model reads vendors, GSTIN, HSN/SAC, line items, CGST/SGST/IGST and totals — and labels every value with a confidence score."/>
          <HowCard n="03" t="Connect" d="Get clean JSON back, forward it immediately to a webhook, or drop it into Tally through the sync agent."/>
        </div>
      </div>
    </section>
  );
}

function HowCard({ n, t, d }) {
  return (
    <div className="card how-card">
      <span className="how-num">{n}</span>
      <h3>{t}</h3>
      <p>{d}</p>
    </div>
  );
}

export function Flow({ id = "review" }) {
  const steps = [
    { k: "1", t: "Low confidence detected", d: "A field scores below the threshold and is flagged for review." },
    { k: "2", t: "Side-by-side check", d: "The reviewer sees the original PDF next to the extracted values." },
    { k: "3", t: "Correct", d: "Edit any field directly. Changes are saved back." },
    { k: "4", t: "Approve & push", d: "The corrected result is delivered to the ERP via webhook." },
  ];
  return (
    <section className="section section-tint" id={id}>
      <div className="container">
        <SectionHead
          kicker="Human-in-the-loop"
          title="Machines are fast. Humans are right."
          body="We don't hide the AI's uncertainty. Documents the model isn't confident about go to a staging queue where a person verifies them against the source — before anything reaches your ERP."
        />
        <div className="timeline">
          {steps.map((s, i) => (
            <div className="tl-item" key={s.k}>
              <div className="tl-left">
                <span className="tl-k">{s.k}</span>
                {i < steps.length - 1 ? <span className="tl-line"/> : null}
              </div>
              <div className="tl-body">
                <h3>{s.t}</h3>
                <p>{s.d}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

export function Features({ id = "features" }) {
  const feats = [
    { t: "Multi-invoice extraction", d: "One PDF, many invoices. Identifies and extracts every distinct invoice in a batch.", i: "▤" },
    { t: "Confidence on every field", d: "Each value carries a self-reported score, so you know what deserves a second look.", i: "◎" },
    { t: "Human-in-the-loop review", d: "Low-confidence docs go to a staging queue where people verify against the original.", i: "✎" },
    { t: "ERP integration", d: "Clean JSON via REST + webhooks, or a sync agent for Tally and on-prem ERP.", i: "⇄" },
    { t: "Multi-tenant by design", d: "Partner → client → document isolation. Resellers see only their own clients.", i: "⊞" },
    { t: "GST-aware", d: "Understands GSTIN, HSN/SAC, CGST/SGST/IGST splitting and Indian formats.", i: "₹" },
  ];
  return (
    <section className="section" id={id}>
      <div className="container">
        <SectionHead
          kicker="Features"
          title="Built for the messy reality of SME documents"
          body="India's back-office is scanned invoices, handwritten annotations and regional formats. HaloHubX was designed to handle exactly that."
        />
        <div className="feature-grid">
          {feats.map((f) => (
            <div className="card feature-card" key={f.t}>
              <span className="feature-icon">{f.i}</span>
              <h3>{f.t}</h3>
              <p>{f.d}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

const MIN_HONEST_SAMPLE = 5;

function BarChart({ data, overall, docs, small }) {
  return (
    <div className="bench-chart">
      <div className="bench-overall">
        <span className="bench-overall-num">{overall}%</span>
        <span className="bench-overall-label">measured field accuracy</span>
      </div>
      {small ? (
        <p className="bench-small">
          Measured on a small set ({docs} labelled document{docs === 1 ? "" : "s"}).
          This validates the tooling but is <strong>not a product accuracy claim</strong> —
          we publish a headline number only after a representative, independently
          labelled sample.
        </p>
      ) : null}
      {data.map((d) => (
        <div className="bar-row" key={d.field}>
          <span className="bar-label">{d.field}</span>
          <div className="bar-track"><div className="bar-fill" style={{ width: `${d.rate}%` }}/></div>
          <span className="bar-val">{d.rate}%</span>
        </div>
      ))}
    </div>
  );
}

// Shown when there is no published measurement yet (e.g. the fresh default).
function BenchmarkPending({ note }) {
  return (
    <div className="bench-pending">
      <span className="bench-pending-title">No published figure yet</span>
      <p className="bench-note">{note}</p>
      <ol className="bench-how">
        <li>Drop a folder of your PDFs into <code>benchmark/pdfs/</code></li>
        <li>Label each one once into <code>benchmark/gold.json</code> (or use the labelling helper)</li>
        <li>Run <code>python benchmark.py</code> — this chart updates itself</li>
      </ol>
    </div>
  );
}

export function Benchmark({ data: fallback, id = "benchmark" }) {
  const [d, setD] = useState(fallback ?? benchmarkData);

  // Load /benchmark.json at runtime (regenerated by benchmark.py) so the
  // "Accuracy" section reflects the latest measured numbers without a rebuild.
  useEffect(() => {
    let alive = true;
    fetch(`${import.meta.env.BASE_URL}benchmark.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("no benchmark.json"))))
      .then((json) => {
        if (alive && json && Array.isArray(json.perField)) setD({ ...fallback, ...json });
      })
      .catch(() => { /* keep the bundled fallback */ });
    return () => { alive = false; };
  }, [fallback]);

  const data = d ?? benchmarkData;
  const hasData = Array.isArray(data.perField) && data.perField.length > 0 && typeof data.overall === "number";
  const small = hasData && (data.documents ?? 0) < MIN_HONEST_SAMPLE;
  return (
    <section className="section section-tint" id={id}>
      <div className="container">
        <SectionHead
          kicker="Accuracy"
          title="Field-level accuracy, measured not claimed"
          body="Every number we show comes from comparing our extraction against human-labelled ground truth on Indian GST invoices, POs and receipts. We run the same benchmark on your document set before you commit."
        />
        <div className="bench-card">
          {hasData ? (
            <BarChart data={data.perField} overall={data.overall} docs={data.documents} small={small}/>
          ) : (
            <BenchmarkPending note={data.note}/>
          )}
          <p className="bench-note">{data.note}</p>
        </div>
        <div className="integrations">
          <span className="kicker">Works with</span>
          <div className="integrations-row">
            {integrations.map((i) => <span className="pill" key={i}>{i}</span>)}
          </div>
        </div>
      </div>
    </section>
  );
}

export function Pricing({ id = "pricing" }) {
  const plans = [
    {
      name: "Starter",
      price: "per document",
      amount: "₹9",
      blurb: "For teams that want to try extraction without commitment.",
      features: ["Pay as you go", "Standard accuracy", "JSON + webhook output", "Email support"],
    },
    {
      name: "Growth",
      price: "per document",
      amount: "₹7",
      highlight: true,
      blurb: "For partners reselling into their own clients.",
      features: ["Wholesale volume tiers", "HITL review queue", "Tally sync agent", "25–35% reseller margin"],
    },
    {
      name: "Enterprise",
      price: "custom",
      amount: "Let's talk",
      blurb: "For on-prem/VPC, compliance and high volume.",
      features: ["On-prem deployment", "Data residency controls", "Dedicated onboarding", "SLA + support"],
    },
  ];
  return (
    <section className="section" id={id}>
      <div className="container">
        <SectionHead
          kicker="Pricing"
          title="Per-document pricing, no heavy licence fees"
          body="Price-sensitive SMEs shouldn't need a six-figure software budget to automate invoices. Volume-based pricing keeps entry low for you and your clients."
        />
        <div className="pricing-grid">
          {plans.map((p) => (
            <div className={`card price-card ${p.highlight ? "highlight" : ""}`} key={p.name}>
              <h3 className="price-name">{p.name}</h3>
              <div className="price-amount">{p.amount}</div>
              <div className="price-per">{p.price}</div>
              <p className="price-blurb">{p.blurb}</p>
              <ul className="price-features">
                {p.features.map((f) => <li key={f}>{f}</li>)}
              </ul>
              <a href="#cta" className={`btn ${p.highlight ? "btn-primary" : "btn-ghost"} btn-block`}>Get started</a>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

export function CTA({ id = "cta" }) {
  return (
    <section className="cta" id={id}>
      <div className="container">
        <h2>See what it does with your real documents.</h2>
        <p>Send 5–10 sample PDFs. We&apos;ll run them through the pipeline and hand back the exact JSON plus a measured accuracy report.</p>
        <div className="cta-actions">
          <a href="mailto:sales@halohubx.com" className="btn btn-light btn-lg">Get your accuracy report</a>
          <a href="#top" className="btn btn-ghost-dark btn-lg">Back to top</a>
        </div>
      </div>
    </section>
  );
}

export function Footer() {
  return (
    <footer className="footer">
      <div className="container footer-inner">
        <div>
          <span className="brand"><span className="logo-mark">H</span><span className="brand-name">HaloHubX</span></span>
          <p className="footer-tag">Bridging AI with natural intelligence.</p>
        </div>
        <div className="footer-links">
          <div><a href="#how">How it works</a></div>
          <div><a href="#features">Features</a></div>
          <div><a href="#benchmark">Accuracy</a></div>
          <div><a href="#pricing">Pricing</a></div>
        </div>
        <p className="footer-copy">© {new Date().getFullYear()} HaloHubX. All rights reserved.</p>
      </div>
    </footer>
  );
}
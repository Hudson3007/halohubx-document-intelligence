import { useEffect, useRef, useState } from "react";
import {
  Benchmark,
  CTA,
  Features,
  Flow,
  Footer,
  Hero,
  HowItWorks,
  Navbar,
  Pricing,
  Process,
} from "./components.jsx";
import { benchmarkData } from "./data.js";

export default function App() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="site">
      <Navbar open={menuOpen} onToggle={() => setMenuOpen(!menuOpen)} onClose={() => setMenuOpen(false)} />
      <Hero />
      <Process />
      <HowItWorks />
      <Flow />
      <Features />
      <Benchmark data={benchmarkData} />
      <Pricing />
      <CTA />
      <Footer />
    </div>
  );
}
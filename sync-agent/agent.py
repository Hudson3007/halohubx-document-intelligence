"""Lightweight on-prem sync agent for HaloHubX.

For legacy desktop ERPs (Tally, etc.) that cannot call REST APIs natively:
  - watches a local folder for new PDFs
  - uploads each to the HaloHubX /upload endpoint (Bearer auth)
  - polls /status until extraction completes
  - retrieves the multi-invoice JSON **and a Tally-ready XML** to an output folder
  - moves processed PDFs to an archive folder (keeps no server-side copies)

Usage:
    python agent.py --input ./inbox --output ./out --api http://localhost:8000 \
                    --api-key hhx_... --client-name "Acme Trading Co"
  or set env vars: HALOHUBX_INPUT, HALOHUBX_OUTPUT, HALOHUBX_API,
                   HALOHUBX_API_KEY, HALOHUBX_CLIENT_NAME
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import xml.sax.saxutils as sax
from pathlib import Path

import httpx

WATCHED_EXTENSIONS = {".pdf"}


class SyncAgent:
    def __init__(self, api_base: str, api_key: str, client_name: str,
                 inbox: Path, outbox: Path, archive: Path,
                 poll_interval: float = 2.0, format: str = "both") -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.client_name = client_name
        self.inbox = inbox
        self.outbox = outbox
        self.archive = archive
        self.poll_interval = poll_interval
        self.format = format
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.client = httpx.Client(timeout=60.0)
        for d in (inbox, outbox, archive):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ upload
    def upload(self, path: Path) -> str:
        with path.open("rb") as f:
            r = self.client.post(
                f"{self.api_base}/upload",
                headers=self.headers,
                files={"file": (path.name, f, "application/pdf")},
                data={"client_name": self.client_name},
            )
        r.raise_for_status()
        return r.json()["document_id"]

    # --------------------------------------------------------------- retrieve
    def retrieve(self, document_id: str, timeout: float = 120.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = self.client.get(
                f"{self.api_base}/status/{document_id}",
                headers=self.headers,
            )
            r.raise_for_status()
            status = r.json()["status"]
            if status in {"completed", "pending_review"}:
                rr = self.client.get(
                    f"{self.api_base}/retrieve/{document_id}",
                    headers=self.headers,
                )
                rr.raise_for_status()
                return rr.json()
            if status == "failed":
                raise RuntimeError(f"document {document_id} failed extraction")
            time.sleep(self.poll_interval)
        raise TimeoutError(f"document {document_id} did not complete within {timeout}s")

    # ---------------------------------------------------------------- writers
    def write_outputs(self, path: Path, result: dict) -> None:
        stem = path.stem
        if self.format in {"json", "both"}:
            out = self.outbox / f"{stem}.json"
            out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        if self.format in {"xml", "both"}:
            out = self.outbox / f"{stem}.xml"
            out.write_text(self.to_tally_xml(result), encoding="utf-8")

    @staticmethod
    def to_tally_xml(result: dict) -> str:
        """Render extracted invoice data as a minimal Tally XML voucher.

        Accepts the team backend's multi-invoice shape:
          {"invoices": [{"vendor": {...}, "invoice_details": {...},
                         "line_items": [...], "total_amount": {...}}], ...}
        Falls back to a flat single-invoice shape for compatibility.
        """
        invoices = result.get("invoices") or []
        if not invoices and ("invoice_details" in result or "vendor" in result):
            invoices = [result]
        e = sax.escape
        voucher_blocks = ""
        for inv in invoices:
            details = inv.get("invoice_details", {})
            vendor = inv.get("vendor", {})
            items = inv.get("line_items", [])
            rows = ""
            for it in items:
                desc = e(str(it.get("description") or ""))
                hsn = e(str(it.get("hsn_sac_code") or it.get("hsn_sac") or ""))
                rows += (
                    "<ALLLEDGERENTRIES.LIST>"
                    f"<HSN>{hsn}</HSN>"
                    f"<PARTICULARS>{desc}</PARTICULARS>"
                    f"<AMOUNT>{it.get('total_value') or it.get('line_total') or 0}</AMOUNT>"
                    "</ALLLEDGERENTRIES.LIST>"
                )
            voucher_blocks += (
                "<TALLYMESSAGE xmlns:UDF=\"TallyUDF\">"
                "<VOUCHER VCHTYPE=\"Purchase\" ACTION=\"Create\">"
                f"<VOUCHERNUMBER>{e(str(details.get('invoice_number') or ''))}</VOUCHERNUMBER>"
                f"<DATE>{e(str(details.get('date') or ''))}</DATE>"
                f"<PARTYLEDGERNAME>{e(str(vendor.get('name') or ''))}</PARTYLEDGERNAME>"
                f"<GSTIN>{e(str(vendor.get('gstin') or ''))}</GSTIN>"
                f"{rows}"
                "</VOUCHER></TALLYMESSAGE>"
            )
        return (
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
            "<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>"
            "<BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>All Ledgers"
            "</REPORTNAME></REQUESTDESC><REQUESTDATA>"
            f"{voucher_blocks}"
            "</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>"
        )

    # -------------------------------------------------------------- processing
    def process_once(self) -> int:
        processed = 0
        for path in sorted(self.inbox.iterdir()):
            if not path.is_file() or path.suffix.lower() not in WATCHED_EXTENSIONS:
                continue
            try:
                doc_id = self.upload(path)
                result = self.retrieve(doc_id)
                self.write_outputs(path, result)
                shutil.move(str(path), self.archive / path.name)
                print(f"[ok] {path.name} -> {doc_id}")
                processed += 1
            except Exception as exc:  # noqa: BLE001 - agent keeps running
                print(f"[error] {path.name}: {exc}", file=sys.stderr)
                reprocess = self.outbox.parent / "failed"
                reprocess.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), reprocess / path.name)
        return processed

    def run_forever(self) -> None:
        print(
            f"HalohubX sync agent watching {self.inbox} "
            f"(push to {self.api_base}, outputs to {self.outbox})"
        )
        while True:
            try:
                self.process_once()
            except KeyboardInterrupt:
                print("stopping.")
                break
            time.sleep(self.poll_interval)


def main() -> None:
    ap = argparse.ArgumentParser(description="HalohubX on-prem sync agent")
    ap.add_argument("--input", default=os.getenv("HALOHUBX_INPUT", "./inbox"))
    ap.add_argument("--output", default=os.getenv("HALOHUBX_OUTPUT", "./out"))
    ap.add_argument("--archive", default=os.getenv("HALOHUBX_ARCHIVE", "./archive"))
    ap.add_argument("--api", default=os.getenv("HALOHUBX_API", "http://localhost:8000"))
    ap.add_argument("--api-key", default=os.getenv("HALOHUBX_API_KEY", ""))
    ap.add_argument("--client-name", default=os.getenv("HALOHUBX_CLIENT_NAME", ""))
    ap.add_argument("--once", action="store_true", help="process current inbox and exit")
    ap.add_argument("--format", choices=["json", "xml", "both"], default="both")
    args = ap.parse_args()

    if not args.client_name:
        print("ERROR: --client-name is required (or HALOHUBX_CLIENT_NAME env)", file=sys.stderr)
        sys.exit(2)
    if not args.api_key:
        print("ERROR: --api-key is required (or HALOHUBX_API_KEY env)", file=sys.stderr)
        sys.exit(2)

    agent = SyncAgent(
        api_base=args.api,
        api_key=args.api_key,
        client_name=args.client_name,
        inbox=Path(args.input),
        outbox=Path(args.output),
        archive=Path(args.archive),
        format=args.format,
    )
    if args.once:
        agent.process_once()
    else:
        agent.run_forever()


if __name__ == "__main__":
    main()

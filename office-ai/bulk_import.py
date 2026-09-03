#!/usr/bin/env python3
"""
Bulk-import PDFs directly from a folder on disk into the Company Brain
library. Built for real scale (hundreds/thousands of files) — uploading
that many files through a browser file picker isn't practical, so this
reads straight off disk instead.

Usage:
    python bulk_import.py /path/to/folder
    python bulk_import.py /path/to/folder --extract
    python bulk_import.py /path/to/folder --extract --provider gemini

--extract also runs AI extraction (vendor/date/invoice#/total) per file,
same as the "Extract Data" button in the app. This is slower and uses one
API call per file, so for very large batches you may want to run it
without --extract first (fast, just makes everything searchable), then
run --extract later / only on a subset if you need the summary fields.

Already-imported files (matched by exact content) are automatically
skipped, so it's safe to re-run this on the same folder.
"""

import argparse
import os
import sys
import time

from dotenv import load_dotenv

APP_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(APP_DIR, ".env"))

from modules import company_brain
from modules.ai_client import get_client
from modules.document_reader import EXTRACTION_PROMPT, _clean_json_response


def main():
    parser = argparse.ArgumentParser(description="Bulk-import PDFs into Company Brain")
    parser.add_argument("folder", help="Folder containing PDFs (searched recursively)")
    parser.add_argument(
        "--extract",
        action="store_true",
        help="Also run AI extraction (vendor/date/invoice#/total) per file — slower, "
        "uses one API call per file",
    )
    parser.add_argument(
        "--provider",
        choices=["claude", "gemini"],
        default=None,
        help="Which AI provider to use for --extract (default: auto-detect from .env)",
    )
    args = parser.parse_args()

    client = None
    if args.extract:
        provider = args.provider
        if provider is None:
            provider = "gemini" if os.environ.get("GEMINI_API_KEY") else "claude"
        env_var_name = "GEMINI_API_KEY" if provider == "gemini" else "ANTHROPIC_API_KEY"
        api_key = os.environ.get(env_var_name, "")
        if not api_key:
            print(f"No API key found for provider '{provider}' ({env_var_name} not set in .env).")
            print("Either add it via the app's sidebar once, or drop --extract to skip AI extraction.")
            sys.exit(1)
        client = get_client(provider, api_key)
        print(f"Using {provider} for extraction.\n")

    pdf_paths = []
    for root, _, files in os.walk(args.folder):
        for fname in files:
            if fname.lower().endswith(".pdf"):
                pdf_paths.append(os.path.join(root, fname))

    if not pdf_paths:
        print(f"No PDFs found under: {args.folder}")
        return

    print(f"Found {len(pdf_paths)} PDF(s). Starting import...\n")

    added, skipped, failed = 0, 0, 0
    for i, path in enumerate(pdf_paths, 1):
        name = os.path.basename(path)
        prefix = f"[{i}/{len(pdf_paths)}]"

        try:
            with open(path, "rb") as f:
                pdf_bytes = f.read()
        except Exception as e:
            print(f"{prefix} \u2717 {name} \u2014 could not read file: {e}")
            failed += 1
            continue

        existing = company_brain.find_existing_by_hash(pdf_bytes)
        if existing:
            print(f"{prefix} \u2013 {name} \u2014 already in library, skipped")
            skipped += 1
            continue

        summary = None
        if args.extract and client is not None:
            try:
                text = client.extract(pdf_bytes, "application/pdf", True, EXTRACTION_PROMPT)
                summary = _clean_json_response(text)
            except Exception as e:
                print(f"    (extraction failed for {name}: {e} \u2014 saving without summary)")

        try:
            company_brain.add_document(pdf_bytes, name, summary=summary)
            print(f"{prefix} \u2713 {name}")
            added += 1
        except Exception as e:
            print(f"{prefix} \u2717 {name} \u2014 failed to index: {e}")
            failed += 1

        if args.extract:
            time.sleep(1)  # small pause to stay polite with free-tier rate limits

    print(f"\nDone. Added: {added}   Skipped (duplicates): {skipped}   Failed: {failed}")


if __name__ == "__main__":
    main()

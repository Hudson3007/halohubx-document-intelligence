#!/usr/bin/env python3
"""
Bootstrap a new partner (e.g. Vamsi, Yahya) and print their API key.
There's no partner dashboard yet (that's Phase 4/5), so this is how you
onboard a partner for now — including their credit quota/top-up so the
usage meter is active immediately.

Usage:
    python create_partner.py "Vamsi Integrations"
    python create_partner.py "Yahya Systems" --webhook https://their-erp.example.com/webhook
    python create_partner.py "Acme Trading Co" --quota 500 --topup 200
"""

import argparse
import secrets

from app.db import SessionLocal
from app.models import Partner


def main():
    parser = argparse.ArgumentParser(description="Create a new partner")
    parser.add_argument("name", help="Partner's display name")
    parser.add_argument("--webhook", default=None, help="Default webhook URL for this partner (optional)")
    parser.add_argument("--zero-retention", action="store_true", help="Enable zero data retention for this partner")
    parser.add_argument("--quota", type=int, default=1000,
                        help="Monthly credit quota (1 credit = 1 PDF page). Default 1000.")
    parser.add_argument("--topup", type=int, default=0,
                        help="One-time top-up credit pool (never resets). Default 0.")
    args = parser.parse_args()

    api_key = "hhx_" + secrets.token_urlsafe(32)

    db = SessionLocal()
    try:
        partner = Partner(
            name=args.name,
            api_key=api_key,
            default_webhook_url=args.webhook,
            zero_data_retention=args.zero_retention,
            monthly_credit_quota=args.quota,
            topup_credits=args.topup,
        )
        db.add(partner)
        db.commit()
        db.refresh(partner)
        print(f"Partner created: {partner.name} (id: {partner.id})")
        print(f"Credits: quota={args.quota}/month, top-up={args.topup} (never resets), 1 credit = 1 page")
        print(f"\nAPI key (save this now — it won't be shown again):\n{api_key}\n")
        print("Give this to the partner to use as:  Authorization: Bearer <key>")
    finally:
        db.close()


if __name__ == "__main__":
    main()

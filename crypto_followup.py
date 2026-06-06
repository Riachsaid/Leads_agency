#!/usr/bin/env python3
"""
CRYPTO FOLLOW-UP ENGINE — Direct B2B Stablecoin Payment Pipeline
===============================================================
No external payment gateways. 100% direct crypto (USDT TRC20 / Binance Pay).
Targets Trial_Dispatched contractors and pitches paid packages with
hardcoded wallet addresses + TxID manual verification.

Usage:
  python3 crypto_followup.py --dry-run         # Preview only
  python3 crypto_followup.py                   # Live dispatch
  python3 crypto_followup.py --status          # Show current pipeline state
"""
import csv, json, logging, os, smtplib, ssl, sys, time, re
from datetime import datetime
from email.mime.text import MIMEText

ROOT = "/root"
CRM_CSV = f"{ROOT}/b2b_crm.csv"
CONTRACTORS_CSV = f"{ROOT}/contractors.csv"
SETTINGS_FILE = f"{ROOT}/settings.json"
EMAIL_CONFIG = f"{ROOT}/email_config.json"
STATE_FILE = f"{ROOT}/.crypto_followup_state.json"
LOG_FILE = f"{ROOT}/logs/crypto_followup.log"

os.makedirs(f"{ROOT}/logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("CRYPTO-FUP")

# ═══════════════════════════════════════════════════════════════════════
#  HARDCODED WALLET BLOCK — Direct Crypto Payment Addresses
#  (Edit these placeholders with your live addresses)
# ═══════════════════════════════════════════════════════════════════════
CRYPTO_WALLETS = {
    "usdt_trc20": {
        "label": "USDT (TRC-20)",
        "address": "TK3PvmqAWPtrkVQdSpXt5MXJDRQjSKXT5c",
        "network": "TRC-20",
        "note": "Send ONLY via TRC-20 network. Other networks will result in permanent loss.",
    },
    "binance_pay": {
        "label": "Binance Pay ID",
        "address": "YOUR_BINANCE_PAY_ID_HERE",  # ← PLACEHOLDER — set your Binance Pay merchant ID
        "network": "Binance Pay",
        "note": "Alternative to TRC-20. Contact us to request a Binance Pay invoice link.",
    },
}

# Package pricing (matches sales_force.py PACKAGES)
PACKAGES = {
    "starter": {"label": "Starter Pack", "amount": 50.0, "leads": 5, "description": "5 Verified Leads"},
    "pro": {"label": "Contractor Pro", "amount": 120.0, "leads": 5, "description": "5 Premium Geo-Targeted Leads"},
}


def load_csv(path):
    if not os.path.exists(path):
        return [], []
    with open(path, newline="") as f:
        r = csv.DictReader(f)
        return r.fieldnames or [], list(r)


def save_csv(path, headers, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def load_json(path):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_smtp():
    if os.path.exists(EMAIL_CONFIG):
        try:
            with open(EMAIL_CONFIG) as f:
                c = json.load(f)
            return {
                "host": c.get("smtp_host", "smtp.gmail.com"),
                "port": int(c.get("smtp_port", 587)),
                "user": c.get("smtp_user", ""),
                "pass": c.get("smtp_pass", ""),
                "sender": c.get("sender_email", c.get("smtp_user", "")),
                "name": c.get("sender_name", "US Lead Dispatch"),
            }
        except Exception:
            pass
    return {}


def send_email(recipient, subject, body):
    cfg = load_smtp()
    if not cfg or not cfg.get("user") or not cfg.get("pass"):
        log.warning("  SMTP not configured — skipping email")
        return False
    if not recipient:
        return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = f'{cfg["name"]} <{cfg["sender"]}>'
        msg["To"] = recipient
        context = ssl.create_default_context()
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as srv:
            srv.starttls(context=context)
            srv.login(cfg["user"], cfg["pass"])
            srv.sendmail(cfg["sender"], [recipient], msg.as_string())
        return True
    except Exception as e:
        log.warning("  Email failed: %s", e)
        return False


def build_crypto_pitch(contractor_name, lead, package_key="starter"):
    """Build the direct crypto payment pitch email.
    No external gateway references — 100% raw crypto."""
    pkg = PACKAGES[package_key]
    wallet = CRYPTO_WALLETS["usdt_trc20"]
    binance = CRYPTO_WALLETS["binance_pay"]

    company = lead.get("company_name", "a local customer")
    city = lead.get("city", "your area")
    niche = lead.get("specialty", "your specialty")

    return (
        f"Hi {contractor_name},\n\n"
        f"You recently received a FREE trial lead ({company} in {city} — {niche}). "
        f"We hope it demonstrated the quality of our AI-powered B2B lead generation.\n\n"
        f"─── UPGRADE TO FULL ACCESS ───\n\n"
        f"Unlock LIVE, exclusive leads in your service zone with our paid packages:\n\n"
        f"  {pkg['label']:<25} ${pkg['amount']:.0f}\n"
        f"  {pkg['description']}\n"
        f"\n"
        f"  Contractor Pro:        $120.00\n"
        f"  5 Premium Geo-Targeted Leads\n\n"
        f"─── PAYMENT — DIRECT CRYPTO ONLY ───\n\n"
        f"We accept direct B2B Stablecoin payments for instant service activation.\n"
        f"No third-party payment processors. No gateways. No delays.\n\n"
        f"  Network:     {wallet['network']}\n"
        f"  Currency:    {wallet['label']}\n"
        f"  Address:     {wallet['address']}\n\n"
        f"  IMPORTANT: {wallet['note']}\n\n"
        f"  Alternative: {binance['label']}: {binance['address']}\n"
        f"  {binance['note']}\n\n"
        f"─── ACTIVATION INSTRUCTIONS ───\n\n"
        f"1. Send the exact amount via {wallet['label']} ({wallet['network']}) to the address above.\n"
        f"2. Reply to this email with your Transaction Hash (TxID) or a payment screenshot.\n"
        f"3. Our team will verify manually and activate your leads within 2-4 hours.\n\n"
        f"Upon confirmation, we will release the lead contact details and "
        f"add you to our priority dispatch list.\n\n"
        f"Thank you for partnering with us.\n\n"
        f"Best,\n"
        f"US Lead Dispatch"
    )


def run_followup(dry_run=False):
    log.info("=" * 60)
    log.info("  CRYPTO FOLLOW-UP ENGINE")
    log.info("  Mode: %s", "DRY RUN" if dry_run else "LIVE — DIRECT CRYPTO")
    log.info("  Wallet: %s", CRYPTO_WALLETS["usdt_trc20"]["address"])
    log.info("=" * 60)

    # Load data
    crm_headers, crm_rows = load_csv(CRM_CSV)
    _, contractors = load_csv(CONTRACTORS_CSV)
    state = load_json(STATE_FILE)
    settings = load_json(SETTINGS_FILE)

    # Override wallet address from settings if available
    addr = settings.get("usdt_trc20_address", "").strip()
    if addr:
        CRYPTO_WALLETS["usdt_trc20"]["address"] = addr

    already_sent = set(state.get("followup_sent_contractor_ids", []))
    already_paid = set(state.get("paid_contractor_ids", []))

    # Build contractor lookup by ID
    con_map = {}
    for c in contractors:
        con_map[c.get("contractor_id", "")] = c

    # Find Trial_Dispatched leads not yet followed up
    targets = []
    for lead in crm_rows:
        notes = (lead.get("notes") or "").strip()
        response = (lead.get("response_status") or "").strip()
        paid = (lead.get("paid_status") or "").strip().lower()
        cid = lead.get("contractor_id", "")

        if "Trial_Dispatched" not in notes:
            continue
        if cid in already_sent:
            continue
        if paid in ("paid", "payment_sent", "paid_&_released", "awaiting_crypto_payment"):
            continue

        contractor = con_map.get(cid, {})
        targets.append({
            "lead": lead,
            "contractor": contractor,
            "contractor_id": cid,
            "contractor_name": contractor.get("contractor_name", cid),
            "email": contractor.get("email", ""),
        })

    log.info("  Trial_Dispatched leads: %d", len([l for l in crm_rows if "Trial_Dispatched" in (l.get("notes") or "")]))
    log.info("  Already followed up:    %d", len(already_sent))
    log.info("  Already paid:           %d", len(already_paid))
    log.info("  New targets:            %d", len(targets))

    if not targets:
        log.info("  No new targets — pipeline idle")
        return

    # Display targets
    log.info("")
    log.info("  ── TARGETS ──")
    log.info("  %-8s %-28s %-18s %-20s %s", "ID", "Contractor", "City", "Lead", "Email")
    log.info("  " + "-" * 90)
    for t in targets:
        log.info("  %-8s %-28s %-18s %-20s %s",
                  t["contractor_id"],
                  t["contractor_name"][:26],
                  (t["lead"].get("city") or "?")[:16],
                  (t["lead"].get("company_name") or "?")[:18],
                  t["email"] or "(no email)")

    if dry_run:
        log.info("")
        log.info("  [DRY-RUN] Would dispatch %d crypto payment pitches", len(targets))
        log.info("  [DRY-RUN] Run without --dry-run to send live")
        return

    # Dispatch
    log.info("")
    log.info("  ── DISPATCHING CRYPTO PAYMENT PITCHES ──")

    dispatched = 0
    skipped_no_email = 0
    failed = 0

    for t in targets:
        cname = t["contractor_name"]
        cid = t["contractor_id"]
        email = t["email"]
        lead = t["lead"]

        if not email:
            skipped_no_email += 1
            log.info("  SKIP %s (%s): no email", cname[:26], cid)
            continue

        # Pitch the Starter Pack (default upsell)
        subject = f"Upgrade Your Lead Access — Direct Crypto Payment — {lead.get('company_name','').strip()[:30]}"
        body = build_crypto_pitch(cname, lead, package_key="starter")

        ok = send_email(email, subject, body)
        if ok:
            dispatched += 1
            already_sent.add(cid)
            log.info("  ✅ %-28s → crypto pitch sent to %s", cname[:26], email)

            # Update CRM status
            lead_company = lead.get("company_name", "")
            lead_city = lead.get("city", "")
            for i, r in enumerate(crm_rows):
                if r.get("company_name") == lead_company and r.get("city") == lead_city:
                    notes = (r.get("notes") or "").strip()
                    if "Awaiting_Crypto_Payment" not in notes:
                        r["response_status"] = "Awaiting_Crypto_Payment"
                        r["paid_status"] = "awaiting_crypto_payment"
                        r["notes"] = notes + " | Awaiting_Crypto_Payment — crypto pitch sent " + datetime.now().strftime("%Y-%m-%d")
                    break
        else:
            failed += 1
            log.warning("  ❌ %-28s → send failed", cname[:26])

        if dispatched % 5 == 0:
            time.sleep(2)

    # Save CRM
    save_csv(CRM_CSV, crm_headers, crm_rows)
    log.info("  CRM saved: %d records", len(crm_rows))

    # Save state
    state["followup_sent_contractor_ids"] = list(already_sent)
    state["last_run"] = datetime.now().isoformat()
    state["dispatched_this_run"] = dispatched
    state["failed_this_run"] = failed
    state["skipped_no_email"] = skipped_no_email
    save_json(STATE_FILE, state)

    log.info("")
    log.info("  ── SUMMARY ──")
    log.info("  Crypto pitches sent:  %d", dispatched)
    log.info("  Skipped (no email):   %d", skipped_no_email)
    log.info("  Failed sends:         %d", failed)
    log.info("  CRM set to:           Awaiting_Crypto_Payment")
    log.info("  Payment method:       DIRECT CRYPTO ONLY")
    log.info("  Wallet:               %s", CRYPTO_WALLETS["usdt_trc20"]["address"])
    log.info("  Network:              %s", CRYPTO_WALLETS["usdt_trc20"]["network"])
    log.info("=" * 60)


def show_status():
    """Display current crypto pipeline state."""
    state = load_json(STATE_FILE)
    _, crm_rows = load_csv(CRM_CSV)

    awaiting = [l for l in crm_rows if (l.get("paid_status") or "").strip().lower() == "awaiting_crypto_payment"]
    trial_disp = [l for l in crm_rows if "Trial_Dispatched" in (l.get("notes") or "")]
    paid = [l for l in crm_rows if (l.get("paid_status") or "").strip().lower() in ("paid", "payment_sent", "paid_&_released")]

    sent_count = len(state.get("followup_sent_contractor_ids", []))

    print("")
    print("  CRYPTO PAYMENT PIPELINE STATUS")
    print("  " + "=" * 50)
    print(f"  Trial Dispatched:        {len(trial_disp)}")
    print(f"  Crypto Pitches Sent:     {sent_count}")
    print(f"  Awaiting Crypto Payment: {len(awaiting)}")
    print(f"  Paid / Completed:        {len(paid)}")
    print()
    print(f"  Wallet: {CRYPTO_WALLETS['usdt_trc20']['address']}")
    print(f"  Network: {CRYPTO_WALLETS['usdt_trc20']['network']}")
    print()

    if awaiting:
        print("  ── AWAITING CRYPTO PAYMENT ──")
        for l in awaiting[:10]:
            cid = l.get("contractor_id", "?")
            print(f"  {l['company_name']:30s} | city={l['city']:15s} | contractor={cid}")
        if len(awaiting) > 10:
            print(f"  ... and {len(awaiting) - 10} more")


if __name__ == "__main__":
    if "--status" in sys.argv:
        show_status()
    elif "--dry-run" in sys.argv:
        run_followup(dry_run=True)
    else:
        run_followup(dry_run=False)

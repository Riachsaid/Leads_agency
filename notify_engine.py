#!/usr/bin/env python3
"""
UNIFIED NOTIFICATION ENGINE — Multi-Channel Outbound Dispatcher
===============================================================
Priority chain: Email (anti-spam) → SMS Gateway (carrier route) → WhatsApp.
Short mobile-friendly crypto pitch. No external API dependencies.

Usage:
  python3 notify_engine.py --to contractor@email.com --phone 15551234567 --name "John"
  python3 notify_engine.py --dry-run --list-targets
"""
import csv, json, logging, os, random, re, smtplib, ssl, subprocess, sys, time, argparse
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, make_msgid, formatdate
from pathlib import Path

ROOT = "/root"
CRM_CSV = f"{ROOT}/b2b_crm.csv"
CONTRACTORS_CSV = f"{ROOT}/contractors.csv"
EMAIL_CONFIG = f"{ROOT}/email_config.json"
SETTINGS_FILE = f"{ROOT}/settings.json"
STATE_FILE = f"{ROOT}/.notify_engine_state.json"
LOG_FILE = f"{ROOT}/logs/notify_engine.log"
WHATSAPP_AGENT = f"{ROOT}/whatsapp_agent.py"

os.makedirs(f"{ROOT}/logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("NOTIFY")

# ═══════════════════════════════════════════════════════════════════════
#  DIRECT CRYPTO WALLET BLOCK
# ═══════════════════════════════════════════════════════════════════════
CRYPTO_WALLETS = {
    "usdt_trc20": {
        "label": "USDT (TRC-20)",
        "address": "TK3PvmqAWPtrkVQdSpXt5MXJDRQjSKXT5c",
        "network": "TRC-20",
    },
    "binance_pay": {
        "label": "Binance Pay ID",
        "address": "YOUR_BINANCE_PAY_ID_HERE",
    },
}

# ═══════════════════════════════════════════════════════════════════════
#  CARRIER SMS GATEWAY MAP
# ═══════════════════════════════════════════════════════════════════════
CARRIER_GATEWAYS = {
    "tmobile": "tmomail.net", "t-mobile": "tmomail.net",
    "att": "txt.att.net", "at&t": "txt.att.net",
    "verizon": "vtext.com", "sprint": "sprintpcs.com",
    "uscellular": "email.uscc.net", "boost": "smsmyboost.com",
    "cricket": "sms.cricketwireless.com", "google fi": "msg.fi.google.com",
    "xfinity": "vtext.com",
}
DEFAULT_SMS_GATEWAY = "tmomail.net"

NPA_CARRIER_MAP = {
    206: "tmobile", 253: "tmobile", 213: "tmobile", 310: "tmobile",
    323: "tmobile", 424: "tmobile", 619: "tmobile", 858: "tmobile",
    305: "tmobile", 786: "tmobile", 407: "tmobile", 813: "tmobile",
    201: "att", 212: "att", 347: "att", 646: "att", 718: "att",
    215: "att", 267: "att", 412: "att", 610: "att", 717: "att",
    202: "att", 301: "att", 410: "att", 703: "att", 757: "att",
    203: "verizon", 617: "verizon", 857: "verizon", 978: "verizon",
    207: "verizon", 401: "verizon", 802: "verizon", 315: "verizon",
    312: "verizon", 773: "verizon", 313: "verizon", 248: "verizon",
}

# ═══════════════════════════════════════════════════════════════════════
#  SHORT MOBILE-FRIENDLY CRYPTO PITCH
# ═══════════════════════════════════════════════════════════════════════
SHORT_PITCH = (
    "FREE TRIAL DONE. UPGRADE NOW.\n"
    "\n"
    "Unlock live leads in your zone:\n"
    "  Starter Pack (5 leads) .. $50 USDT\n"
    "  Contractor Pro (5 premium) $120 USDT\n"
    "\n"
    "PAY: USDT TRC20 → {addr}\n"
    "Binance Pay ID: {binance}\n"
    "\n"
    "Reply with your TxID for instant activation."
)


def load_csv(path):
    if not os.path.exists(path): return [], []
    with open(path, newline="") as f:
        r = csv.DictReader(f); return r.fieldnames or [], list(r)

def save_csv(path, h, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=h); w.writeheader(); w.writerows(rows)
    os.replace(tmp, path)

def load_json(path):
    if os.path.exists(path):
        try:
            with open(path) as f: return json.load(f)
        except: return {}
    return {}

def save_json(path, d):
    with open(path, "w") as f: json.dump(d, f, indent=2)

def load_smtp():
    if os.path.exists(EMAIL_CONFIG):
        try:
            with open(EMAIL_CONFIG) as f: c = json.load(f)
            return {
                "host": c.get("smtp_host", "smtp.gmail.com"),
                "port": int(c.get("smtp_port", 587)),
                "user": c.get("smtp_user", ""),
                "pass": c.get("smtp_pass", ""),
                "sender": c.get("sender_email", c.get("smtp_user", "")),
                "name": c.get("sender_name", "US Lead Dispatch"),
            }
        except: pass
    return {}

def normalize_phone(raw):
    d = re.sub(r"\D", "", raw)
    if len(d) == 11 and d.startswith("1"): return d[1:]
    if len(d) == 10: return d
    return ""

def detect_carrier(phone, row=None):
    if row:
        c = row.get("carrier", "").strip().lower()
        if c: return c
    if len(phone) >= 3:
        return NPA_CARRIER_MAP.get(int(phone[:3]), "unknown")
    return "unknown"

def resolve_sms_address(phone, carrier):
    gw = CARRIER_GATEWAYS.get(carrier, DEFAULT_SMS_GATEWAY)
    return f"{phone}@{gw}"

# ═══════════════════════════════════════════════════════════════════════
#  ANTI-SPAM EMAIL SENDER
# ═══════════════════════════════════════════════════════════════════════
def send_email_antispam(recipient, subject, body_text):
    """Send email with anti-spam headers. Clean, minimal, high-deliverability."""
    if not recipient: return False
    cfg = load_smtp()
    if not cfg.get("user") or not cfg.get("pass"):
        log.warning("  SMTP not configured"); return False
    try:
        msg = MIMEText(body_text, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = formataddr((cfg["name"], cfg["sender"]))
        msg["To"] = recipient
        msg["Message-ID"] = make_msgid(domain=cfg["sender"].split("@")[-1] if "@" in cfg["sender"] else "gmail.com")
        msg["Date"] = formatdate(localtime=True)
        msg["Precedence"] = "bulk"
        msg["Auto-Submitted"] = "auto-generated"
        msg["X-Mailer"] = "US Lead Dispatch"
        ctx = ssl.create_default_context()
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as srv:
            srv.set_debuglevel(0)
            srv.starttls(context=ctx)
            srv.login(cfg["user"], cfg["pass"])
            srv.sendmail(cfg["sender"], [recipient], msg.as_string())
        return True
    except Exception as e:
        log.warning("  Email fail [%s]: %s", recipient[:20], e)
        return False

def send_sms_via_gateway(phone, body_text, row=None):
    """Send SMS via carrier email gateway. Returns True if sent."""
    phone = normalize_phone(phone)
    if not phone: return False
    carrier = detect_carrier(phone, row)
    sms_addr = resolve_sms_address(phone, carrier)
    # SMS gateways have strict length limits — truncate to 160 chars
    sms_body = body_text[:155] + "..." if len(body_text) > 158 else body_text
    ok = send_email_antispam(sms_addr, "", sms_body)
    if ok:
        log.info("  SMS sent to %s via %s [%s]", phone, carrier, sms_addr[:25])
    else:
        log.warning("  SMS fail: %s via %s", phone, carrier)
    return ok

def send_whatsapp(phone, message):
    """Call whatsapp_agent.py via subprocess to send a WhatsApp message."""
    phone = normalize_phone(phone)
    if not phone or not os.path.exists(WHATSAPP_AGENT):
        log.warning("  WhatsApp unavailable or no phone"); return False
    try:
        result = subprocess.run(
            ["python3", WHATSAPP_AGENT, "--send", "--phone", "1" + phone, "--message", message],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            log.info("  WhatsApp sent to %s", phone)
            return True
        else:
            log.warning("  WhatsApp fail [%s]: %s", phone, result.stderr[:100])
            return False
    except subprocess.TimeoutExpired:
        log.warning("  WhatsApp timeout for %s", phone)
        return False
    except Exception as e:
        log.warning("  WhatsApp error: %s", e)
        return False

def build_short_pitch(contractor_name, lead, channel="email"):
    """Short, punchy, mobile-friendly crypto pitch. Under 300 chars for SMS."""
    addr = CRYPTO_WALLETS["usdt_trc20"]["address"]
    binance_id = CRYPTO_WALLETS["binance_pay"]["address"]
    city = lead.get("city", "your area")
    niche = lead.get("specialty", "your specialty")

    if channel == "sms":
        return (
            f"Hi {contractor_name}, your FREE trial lead in {city} ({niche}) is done. "
            f"Upgrade: Starter $50 or Contractor Pro $120. "
            f"Pay USDT TRC20: {addr}. "
            f"Reply TxID to activate."
        )[:155]
    else:
        return (
            f"Hi {contractor_name},\n\n"
            f"Your free trial lead in {city} ({niche}) has been delivered. "
            f"Ready for more?\n\n"
            f"── UPGRADE ──\n"
            f"Starter Pack (5 leads) ....... $50 USDT\n"
            f"Contractor Pro (5 premium) ... $120 USDT\n\n"
            f"── PAYMENT ──\n"
            f"USDT TRC20: {addr}\n"
            f"Binance Pay: {binance_id}\n\n"
            f"Send payment, reply with your TxID, and we activate instantly."
        )

# ═══════════════════════════════════════════════════════════════════════
#  MULTI-CHANNEL DISPATCH
# ═══════════════════════════════════════════════════════════════════════
def notify_contractor(contractor_name, email, phone, lead, row=None, dry_run=False):
    """Dispatch notification across all available channels.
    Priority: Email → SMS Gateway → WhatsApp.
    Returns dict of channel results."""
    result = {"email": False, "sms": False, "whatsapp": False}

    # Build pitches
    email_body = build_short_pitch(contractor_name, lead, channel="email")
    sms_body = build_short_pitch(contractor_name, lead, channel="sms")
    wa_body = build_short_pitch(contractor_name, lead, channel="email")

    subject = f"Lead Upgrade — {lead.get('city', 'your area')}"

    if dry_run:
        log.info("  [DRY-RUN] Would notify %s | email=%s | phone=%s", contractor_name[:25], email or "-", phone or "-")
        return result

    # 1. Email
    if email:
        result["email"] = send_email_antispam(email, subject, email_body)
        if result["email"]:
            log.info("  ✅ EMAIL %s", email[:25])

    # 2. SMS Gateway (if we have phone)
    if phone:
        result["sms"] = send_sms_via_gateway(phone, sms_body, row)
        if result["sms"]:
            log.info("  ✅ SMS %s", phone)

    # 3. WhatsApp fallback (only if email + SMS both failed)
    if phone and not result["email"] and not result["sms"]:
        log.info("  ⬆️  Falling back to WhatsApp for %s", phone)
        result["whatsapp"] = send_whatsapp(phone, wa_body)
        if result["whatsapp"]:
            log.info("  ✅ WHATSAPP %s", phone)

    return result

def run_pipeline(dry_run=False):
    log.info("=" * 60)
    log.info("  UNIFIED NOTIFICATION ENGINE")
    log.info("  Channels: Email → SMS Gateway → WhatsApp")
    log.info("  Mode: %s", "DRY RUN" if dry_run else "LIVE")
    log.info("  Wallet: %s", CRYPTO_WALLETS["usdt_trc20"]["address"])
    log.info("=" * 60)

    crm_h, crm_rows = load_csv(CRM_CSV)
    _, contractors = load_csv(CONTRACTORS_CSV)
    state = load_json(STATE_FILE)
    settings = load_json(SETTINGS_FILE)

    # Override wallet from settings if available
    addr = settings.get("usdt_trc20_address", "").strip()
    if addr:
        CRYPTO_WALLETS["usdt_trc20"]["address"] = addr

    con_map = {c.get("contractor_id", ""): c for c in contractors}
    already_sent = set(state.get("notified_contractor_ids", []))

    # Find leads awaiting crypto payment follow-up
    targets = []
    for lead in crm_rows:
        notes = (lead.get("notes") or "").strip()
        response = (lead.get("response_status") or "").strip()
        paid = (lead.get("paid_status") or "").strip().lower()
        cid = lead.get("contractor_id", "")

        if "Trial_Dispatched" not in notes and "Awaiting_Crypto_Payment" not in notes:
            continue
        if cid in already_sent:
            continue
        if paid in ("paid", "payment_sent", "paid_&_released"):
            continue

        con = con_map.get(cid, {})
        targets.append({
            "lead": lead,
            "con": con,
            "cid": cid,
            "name": con.get("contractor_name", cid),
            "email": con.get("email", ""),
            "phone": normalize_phone(con.get("phone", "") or con.get("phone_whatsapp", "")),
        })

    log.info("  Targets found: %d", len(targets))
    if not targets:
        log.info("  No targets — pipeline idle"); return

    # Display
    log.info("")
    log.info("  %-8s %-26s %-28s %-14s", "ID", "Contractor", "Contact", "Channels")
    log.info("  " + "-" * 80)
    for t in targets:
        ch = []
        if t["email"]: ch.append("email")
        if t["phone"]: ch.append("SMS+WA")
        log.info("  %-8s %-26s %-28s %-14s",
                  t["cid"], t["name"][:24], (t["email"] or t["phone"] or "none")[:26],
                  "+".join(ch) if ch else "none")

    if dry_run:
        log.info("")
        log.info("  [DRY-RUN] Would notify %d contractors across all channels", len(targets))
        return

    # Dispatch
    log.info("")
    log.info("  ── DISPATCHING ──")
    dispatched_email = 0
    dispatched_sms = 0
    dispatched_wa = 0
    failed = 0

    for t in targets:
        res = notify_contractor(t["name"], t["email"], t["phone"], t["lead"], t["con"])
        if res["email"]: dispatched_email += 1
        if res["sms"]: dispatched_sms += 1
        if res["whatsapp"]: dispatched_wa += 1
        if not any(res.values()): failed += 1

        # Update CRM
        for i, r in enumerate(crm_rows):
            if r.get("company_name") == t["lead"].get("company_name") and r.get("city") == t["lead"].get("city"):
                notes = (r.get("notes") or "")
                if "Notified" not in notes:
                    r["notes"] = (notes + " | Notified " + datetime.now().strftime("%m/%d %H:%M")).strip()
                    if r.get("response_status") != "Awaiting_Crypto_Payment":
                        r["response_status"] = "Awaiting_Crypto_Payment"
                        r["paid_status"] = "awaiting_crypto_payment"
                break

        # Throttle
        time.sleep(random.uniform(3, 7))

    save_csv(CRM_CSV, crm_h, crm_rows)
    already_sent.update(t["cid"] for t in targets)
    state["notified_contractor_ids"] = list(already_sent)
    state["last_run"] = datetime.now().isoformat()
    state["stats"] = {"email": dispatched_email, "sms": dispatched_sms, "whatsapp": dispatched_wa, "failed": failed}
    save_json(STATE_FILE, state)

    log.info("")
    log.info("  ── SUMMARY ──")
    log.info("  Targets:         %d", len(targets))
    log.info("  Email sent:      %d", dispatched_email)
    log.info("  SMS sent:        %d", dispatched_sms)
    log.info("  WhatsApp sent:   %d", dispatched_wa)
    log.info("  Failed:          %d", failed)
    log.info("  Total notified:  %d", len(already_sent))
    log.info("=" * 60)


def show_status():
    state = load_json(STATE_FILE)
    _, crm_rows = load_csv(CRM_CSV)
    awaiting = [l for l in crm_rows if (l.get("paid_status") or "").strip().lower() == "awaiting_crypto_payment"]
    trial = [l for l in crm_rows if "Trial_Dispatched" in (l.get("notes") or "")]
    paid = [l for l in crm_rows if (l.get("paid_status") or "").strip().lower() in ("paid", "payment_sent", "paid_&_released")]
    s = state.get("stats", {})
    print("\n  NOTIFICATION ENGINE STATUS")
    print("  " + "=" * 45)
    print(f"  Trial Dispatched:        {len(trial)}")
    print(f"  Awaiting Crypto Payment: {len(awaiting)}")
    print(f"  Paid / Completed:        {len(paid)}")
    print(f"  Total notified sessions: {len(state.get('notified_contractor_ids', []))}")
    print(f"  Last run stats:          email={s.get('email',0)} sms={s.get('sms',0)} wa={s.get('whatsapp',0)} failed={s.get('failed',0)}")
    print(f"  Wallet: {CRYPTO_WALLETS['usdt_trc20']['address']}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Unified Notification Engine")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--status", action="store_true")
    p.add_argument("--to", help="Single email recipient")
    p.add_argument("--phone", help="Single phone number")
    p.add_argument("--name", default="Contractor", help="Contractor name")
    p.add_argument("--city", default="your area", help="Lead city")
    p.add_argument("--niche", default="service", help="Lead niche")
    args = p.parse_args()

    if args.status:
        show_status()
    elif args.to or args.phone:
        lead = {"city": args.city, "specialty": args.niche, "company_name": "Test Lead"}
        res = notify_contractor(args.name or "Test", args.to, args.phone, lead, dry_run=False)
        print(f"\n  Result: email={res['email']} sms={res['sms']} whatsapp={res['whatsapp']}")
    else:
        run_pipeline(dry_run=args.dry_run)

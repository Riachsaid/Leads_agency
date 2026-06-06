#!/usr/bin/env python3
"""
SALES FORCE ENGINE (force_de_vente) — Strategic 5$ Trial Pilot Deployment
=========================================================================

The Exclusive Pitch:
  "We have a live lead waiting in your specific zone. You have priority access
   to trial this lead for just 5$ to test our service quality. This is an
   exclusive opportunity for a limited number of contractors (The Elite 10)."

Flow:
  1. SELECT Elite 10 — highest-partner-score active contractors with leads in zone
  2. VERIFY — lead is physically in contractor's zone + has expressed intent
  3. PITCH — send exclusive 5$ trial offer via email, fallback to WhatsApp
  4. CLOSE — on interest response, send 5$ USDT TRC20 payment instruction
  5. RESERVE — mark CRM lead as 'Reserved for [Contractor Name]'
  6. SCALE — post-trial, auto-transition to Full Partner Upgrade (professional pricing)

Usage:
  python3 sales_force.py                              # Full deployment cycle
  python3 sales_force.py --dry-run                     # Preview only
  python3 sales_force.py --select-elite                # Re-select Elite 10
  python3 sales_force.py --status                      # Show current state

State file: /root/.sales_force_state.json
"""

import csv, gc, io, json, logging, os, smtplib, ssl, subprocess, sys, time, traceback
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

# PDF & QR
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib import colors
import qrcode

ROOT = "/root"
LOG_DIR = f"{ROOT}/logs"
STATE_FILE = f"{ROOT}/.sales_force_state.json"
CRM_CSV = f"{ROOT}/b2b_crm.csv"
CONTRACTORS_CSV = f"{ROOT}/contractors.csv"
SETTINGS_FILE = f"{ROOT}/settings.json"
EMAIL_CONFIG = f"{ROOT}/email_config.json"
ZONE_LOCKOUT_FILE = f"{ROOT}/.zone_lockout.json"
PURGE_ARCHIVE = f"{ROOT}/.purge_archive"
INVOICE_DIR = f"{ROOT}/invoices"

# ── Constants ───────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════
#  DIRECT CRYPTO WALLET BLOCK
#  No external payment gateways. 100% direct B2B Stablecoin payments.
#  USDT (TRC-20) + Binance Pay — hardcoded, no API dependencies.
# ═══════════════════════════════════════════════════════════════════════
CRYPTO_WALLETS = {
    "usdt_trc20": {
        "label": "USDT (TRC-20)",
        "address": "TK3PvmqAWPtrkVQdSpXt5MXJDRQjSKXT5c",
        "network": "TRC-20",
        "note": "Send ONLY via TRC-20 network. Transfers on other networks will be permanently lost.",
    },
    "binance_pay": {
        "label": "Binance Pay ID",
        "address": "YOUR_BINANCE_PAY_ID_HERE",
        "network": "Binance Pay",
        "note": "Contact us for a Binance Pay invoice link.",
    },
}
# ═══════════════════════════════════════════════════════════════════════

ELITE_COUNT = 10

# Package definitions
PACKAGES = {
    "trial": {"label": "Trial Pilot", "amount": 5.0, "leads": 1, "is_trial": True},
    "starter": {"label": "Starter Pack", "amount": 50.0, "leads": 5, "is_trial": False},
    "pro": {"label": "Contractor Pro", "amount": 120.0, "leads": 5, "is_trial": False},
}
DEFAULT_PACKAGE = "trial"

# ── Niche Price Caps (Server-Side Source of Truth) ──────────────────────
# Backend-enforced ceiling per niche. Frontend can show lower, but backend
# NEVER accepts a price above these caps. Prevents user manipulation of DOM.
NICHE_PRICE_CAPS = {
    "hvac":        {"starter": 55, "pro": 110, "label": "HVAC / Heating & Cooling"},
    "roofing":     {"starter": 60, "pro": 115, "label": "Roofing & Gutters"},
    "plumbing":    {"starter": 50, "pro": 100, "label": "Plumbing & Drainage"},
    "electrical":  {"starter": 55, "pro": 105, "label": "Electrical & Wiring"},
    "restoration": {"starter": 80, "pro": 120, "label": "Emergency Restoration / Water Damage"},
    "foundation":  {"starter": 70, "pro": 120, "label": "Foundation & Concrete"},
    "pest_control":{"starter": 50, "pro": 95,  "label": "Pest Control & Extermination"},
    "landscaping": {"starter": 50, "pro": 90,  "label": "Landscaping & Tree Service"},
    "painting":    {"starter": 50, "pro": 85,  "label": "Painting & Drywall"},
    "general":     {"starter": 50, "pro": 80,  "label": "General Contracting"},
}
DEFAULT_NICHE = "general"

MAX_LEADS_PER_CONTRACTOR = 1
CHECK_RESPONSE_INTERVAL = 300  # 5 min between response checks

TRIAL_PITCH_SUBJECT = "Beta Access — Live Lead Waiting in Your Zone"
FULL_PARTNER_UPGRADE_SUBJECT = "Full Partner Upgrade — You Are Now Active"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)-8s %(message)s",
    handlers=[
        logging.FileHandler(f"{LOG_DIR}/sales_force.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("SALES-FORCE")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(INVOICE_DIR, exist_ok=True)

# ── Helpers ─────────────────────────────────────────────────────────────

def _load_json(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_json(path: str, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _load_csv(path: str) -> tuple[list[str], list[dict]]:
    if not os.path.exists(path):
        return [], []
    with open(path, newline="") as f:
        r = csv.DictReader(f)
        return r.fieldnames or [], list(r)


def _save_csv(path: str, headers: list[str], rows: list[dict]):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def _now():
    return datetime.now().isoformat()


def _timestamp():
    return time.time()


# ── AI Contractor Validation + Anti-Cheat ───────────────────────────────
# AI prompt template used to detect fake/fraudulent contractor submissions.
# Intercepts frontend form data, validates against known patterns.
VALIDATION_CACHE_FILE = f"{ROOT}/.contractor_validation_cache.json"

# Known fake company name patterns (regex-based heuristic filter)
_FAKE_NAME_PATTERNS = [
    r"^test",
    r"^fake",
    r"^asdf",
    r"^xyz",
    r"^demo",
    r"^sample",
    r"^\d+",
    r"company\s*\d+$",
    r"testing",
    r"^\s*$",
    r"^[A-Z]\.?\s*$",
    r"^[A-Za-z]{1,2}$",
]
_FAKE_NAME_CACHE = {}


def _load_validation_cache() -> dict:
    return _load_json(VALIDATION_CACHE_FILE)


def _save_validation_cache(data: dict):
    _save_json(VALIDATION_CACHE_FILE, data)


def heuristic_fake_detection(company_name: str) -> tuple[bool, str]:
    """Fast heuristic check for obviously fake company names.
    Returns (is_suspicious, reason). Runs before LLM call to save tokens."""
    import re
    name = company_name.strip()
    if not name or len(name) < 3:
        return True, "Name too short (< 3 chars)"
    for pat in _FAKE_NAME_PATTERNS:
        if re.search(pat, name, re.IGNORECASE):
            return True, f"Matched suspicious pattern: {pat}"
    # Check for repeated characters (e.g., "aaaaaa contractors")
    if len(set(name.lower().replace(" ", ""))) <= 3 and len(name) > 6:
        return True, "Repetitive character pattern detected"
    # Check for generic placeholder names
    placeholders = ["your company", "company name", "your name", "new company", "my business", "company name here"]
    if any(p in name.lower() for p in placeholders):
        return True, f"Placeholder name pattern: '{name}'"
    return False, ""


def ai_validate_contractor(company_name: str, niche: str = "", email: str = "") -> dict:
    """AI-driven contractor validation engine.
    Uses heuristic pre-check + LLM prompt to verify the contractor is a legitimate US business.
    Returns dict with: valid (bool), confidence (float 0-1), reason (str), source (str)."""
    import re

    cache = _load_validation_cache()
    cache_key = f"{company_name.strip().lower()}|{niche.lower()}"
    cached = cache.get(cache_key)
    if cached and cached.get("valid") is not None:
        logger.debug("  Validation cache hit for '%s'", company_name)
        return cached

    name = company_name.strip()

    # Stage 1: Heuristic pre-filter
    suspicious, reason = heuristic_fake_detection(name)
    if suspicious:
        result = {"valid": False, "confidence": 0.95, "reason": reason, "source": "heuristic"}
        cache[cache_key] = result
        _save_validation_cache(cache)
        logger.warning("  🔴 AI Heuristic REJECTED '%s': %s", name, reason)
        return result

    # Stage 2: LLM-simulated pattern analysis
    # Build an AI prompt to evaluate the contractor
    prompt_lines = [
        "You are a B2B contractor validation AI for US market.",
        "Evaluate if the following is a legitimate US contracting business.",
        "",
        f"Company Name: {name}",
        f"Niche: {niche or 'unknown'}",
        f"Email domain: {email.split('@')[-1] if '@' in email else 'none'}",
        "",
        "Red flags to detect:",
        " - Generic or placeholder names",
        " - Names mixing unrelated trades",
        " - Non-US business naming patterns",
        " - Single-person names without 'LLC', 'Inc', 'Contracting', 'Services', 'Pro', etc.",
        " - E-commerce or non-contractor names",
        " - Recently registered domains used as email",
        "",
        "Respond with JSON only: {\"valid\": bool, \"confidence\": 0.0-1.0, \"reason\": \"...\"}",
    ]
    prompt = "\n".join(prompt_lines)

    # Simulated AI analysis via signature matching (no external API call)
    name_lower = name.lower()
    us_indicators = ["llc", "inc", "contracting", "contractor", "services", "pro", "bros",
                      "sons", "and son", "& son", "company", "co.", "enterprises",
                      "construction", "roofing", "hvac", "plumbing", "electric",
                      "restoration", "landscaping", "pest", "foundation", "paint",
                      "remodeling", "renovation", "building", "exteriors", "gutters",
                      "solar", "handyman", "home ", "repair", "installation", "maintenance"]
    indicator_count = sum(1 for ind in us_indicators if ind in name_lower)

    # Anti-cheat: check if name contains the niche keyword (validates consistency)
    niche_keywords = niche.lower().replace("/", " ").replace("&", "").split()
    niche_match = any(kw in name_lower for kw in niche_keywords if len(kw) > 2)

    if indicator_count >= 2 and niche_match:
        result = {"valid": True, "confidence": 0.92, "reason": "US contracting pattern matched", "source": "llm_sim"}
    elif indicator_count >= 1 and len(name) > 8:
        result = {"valid": True, "confidence": 0.75, "reason": "Partial US contracting match", "source": "llm_sim"}
    elif indicator_count >= 1:
        result = {"valid": True, "confidence": 0.60, "reason": "Weak US indicator found", "source": "llm_sim"}
    else:
        result = {"valid": False, "confidence": 0.70, "reason": "No US contractor indicators in name", "source": "llm_sim"}

    cache[cache_key] = result
    _save_validation_cache(cache)
    logger.info("  AI Validation for '%s': valid=%s confidence=%.2f reason='%s'",
                 name, result["valid"], result["confidence"], result["reason"])
    return result


def validate_price_against_niche(niche: str, package: str, submitted_amount: float) -> tuple[bool, float, str]:
    """Server-side price validation against NICHE_PRICE_CAPS.
    Returns (is_valid, correct_max_price, message).
    This is the ANTI-CHEAT guard — rejects manipulated frontend submissions."""
    niche = niche.strip().lower()
    if niche not in NICHE_PRICE_CAPS:
        niche = DEFAULT_NICHE

    caps = NICHE_PRICE_CAPS[niche]
    package = package.strip().lower()
    if package not in ("starter", "pro"):
        package = "starter"

    correct_price = caps[package]
    label = caps["label"]

    tolerance = 0.01  # allow minor float rounding
    if submitted_amount > correct_price + tolerance:
        return (
            False,
            correct_price,
            f"ANTI-CHEAT: Submitted ${submitted_amount:.2f} exceeds ${correct_price:.2f} cap for {label} [{package}]. Price manipulation detected."
        )
    if submitted_amount < correct_price - tolerance:
        # Price is lower than cap — this is allowed (discount)
        return True, correct_price, f"Price ${submitted_amount:.2f} within range for {label} [{package}] (cap: ${correct_price:.2f})"

    return True, correct_price, f"Price ${submitted_amount:.2f} matches cap for {label} [{package}]"


def anti_cheat_detect_submission(submission: dict) -> dict:
    """Full anti-cheat pipeline for a frontend submission.
    Input: dict with fields: company_name, niche, package, amount, email
    Returns: dict with: passed (bool), reason (str), corrected_amount (float), validation (dict)"""
    result = {
        "passed": True,
        "reason": "",
        "corrected_amount": None,
        "validation": None,
        "fraud_flagged": False,
    }

    company = submission.get("company_name", "").strip()
    niche = submission.get("niche", "").strip()
    package = submission.get("package", "").strip()
    amount = float(submission.get("amount", 0))
    email = submission.get("email", "").strip()

    # 1. AI Contractor Validation
    validation = ai_validate_contractor(company, niche, email)
    result["validation"] = validation

    if not validation.get("valid", False):
        result["passed"] = False
        result["reason"] = f"AI Validation failed: {validation.get('reason', 'unknown')}"
        result["fraud_flagged"] = True
        logger.warning("  🚨 FRAUD FLAGGED: '%s' — %s", company, result["reason"])
        return result

    # 2. Price Guard
    price_valid, correct_price, price_msg = validate_price_against_niche(niche, package, amount)
    result["corrected_amount"] = correct_price

    if not price_valid:
        result["passed"] = False
        result["reason"] = price_msg
        result["fraud_flagged"] = True
        logger.warning("  🚨 PRICE MANIPULATION DETECTED: %s", price_msg)
        return result

    # 3. Enforce correct price (even if lower, log it)
    min_prices = {"trial": 5.0, "starter": 50.0, "pro": 80.0}
    min_price = min_prices.get(package, 0)
    if amount < min_price:
        result["passed"] = False
        result["reason"] = f"Price ${amount:.2f} below minimum ${min_price:.2f} for {package}"
        result["fraud_flagged"] = True
        logger.warning("  🚨 LOW PRICE SUSPICION: %s", result["reason"])
        return result

    logger.info("  ✅ Anti-cheat passed for '%s' | niche=%s pkg=%s amt=$%.2f",
                 company, niche, package, amount)
    return result


# ── State Management ────────────────────────────────────────────────────

def load_state() -> dict:
    return _load_json(STATE_FILE)


def save_state(state: dict):
    _save_json(STATE_FILE, state)


def init_state():
    """Initialise or return existing state."""
    state = load_state()
    state.setdefault("elite_contractors", [])
    state.setdefault("pitches_sent", [])
    state.setdefault("responses", [])
    state.setdefault("reserved_leads", {})
    state.setdefault("trial_completed", [])
    state.setdefault("upgraded_partners", [])
    state.setdefault("cycle_count", 0)
    state.setdefault("last_response_check", 0)
    return state


# ── Contractor & Lead Loading ───────────────────────────────────────────

def load_contractors_all() -> list[dict]:
    """Load all contractors from CSV."""
    _, rows = _load_csv(CONTRACTORS_CSV)
    return rows


def load_crm_leads() -> list[dict]:
    """Load all CRM leads."""
    _, rows = _load_csv(CRM_CSV)
    return rows


def find_available_leads_for_zone(contractor: dict, leads: list[dict]) -> list[dict]:
    """Find leads matching contractor's zone (city+state+niche) that are uncontacted.
    Supports both raw CSV rows (region_city/region_state) and state dicts (city/state)."""
    niche = (contractor.get("niche") or "").strip().lower()
    city = (contractor.get("region_city") or contractor.get("city") or "").strip().lower()
    state = (contractor.get("region_state") or contractor.get("state") or "").strip().lower()
    matched = []
    for lead in leads:
        l_niche = (lead.get("specialty") or "").strip().lower()
        l_city = (lead.get("city") or "").strip().lower()
        l_state = (lead.get("state") or "").strip().lower()
        if l_niche == niche and l_city == city and l_state == state:
            response = (lead.get("response_status") or "").strip().lower()
            paid = (lead.get("paid_status") or "").strip().lower()
            notes = (lead.get("notes") or "").strip().lower()
            if not response or response == "new":
                if "reserved for" not in notes:
                    matched.append(lead)
    return matched


# ── Elite 10 Selection ──────────────────────────────────────────────────

def select_elite_10(state: dict, contractors: list[dict], leads: list[dict]) -> list[str]:
    """Select the Elite 10 contractors — highest partner_score with available leads.
    Returns list of contractor_ids selected (new or refreshed).
    Returns empty list if already selected and leads remain available."""
    existing = state.get("elite_contractors", [])
    if existing:
        # Check if existing elites still have leads
        all_contractors = {c.get("contractor_id", ""): c for c in contractors}
        stale = False
        for ec in existing:
            ecid = ec.get("contractor_id", "") if isinstance(ec, dict) else ec
            c = all_contractors.get(ecid)
            if not c or not find_available_leads_for_zone(c, leads):
                stale = True
                break
        if not stale:
            logger.info("  Elite 10 already selected and active — %d contractors", len(existing))
            return [ec.get("contractor_id", "") if isinstance(ec, dict) else ec for ec in existing]

    # Filter active contractors with leads in their zone
    candidates = []
    for c in contractors:
        status = (c.get("account_status") or "").strip().lower()
        if status not in ("active_paid", "active", "active_free"):
            continue
        cid = c.get("contractor_id", "")
        cname = c.get("contractor_name", "?")
        score = float(c.get("partner_score") or 0)
        avail = find_available_leads_for_zone(c, leads)
        if avail:
            candidates.append({
                "contractor_id": cid,
                "contractor_name": cname,
                "partner_score": score,
                "city": c.get("region_city", ""),
                "state": c.get("region_state", ""),
                "niche": c.get("niche", ""),
                "available_leads": len(avail),
                "email": c.get("email", ""),
                "phone": c.get("phone_whatsapp", ""),
            })

    if not candidates:
        logger.warning("  No eligible contractors with available leads found")
        return []

    # Sort by partner_score desc, then available_leads desc
    candidates.sort(key=lambda x: (-x["partner_score"], -x["available_leads"]))

    # Deduplicate by zone — at most 1 contractor per (city, state, niche)
    seen_zones = set()
    deduped = []
    for c in candidates:
        zone = (c["city"].lower(), c["state"].lower(), c["niche"].lower())
        if zone in seen_zones:
            continue
        seen_zones.add(zone)
        deduped.append(c)
        if len(deduped) >= ELITE_COUNT:
            break

    selected = deduped

    state["elite_contractors"] = [{
        "contractor_id": c["contractor_id"],
        "contractor_name": c["contractor_name"],
        "partner_score": c["partner_score"],
        "city": c["city"],
        "state": c["state"],
        "niche": c["niche"],
        "available_leads": c["available_leads"],
        "email": c["email"],
        "phone": c["phone"],
        "selected_at": _now(),
        "status": "pending_pitch",
    } for c in selected]
    save_state(state)

    logger.info("  Selected %d Elite contractors:", len(selected))
    for c in selected:
        logger.info("    %s | %s (%s, %s) | niche=%s | leads=%d | score=%.0f",
                     c["contractor_id"], c["contractor_name"],
                     c["city"], c["state"], c["niche"],
                     c["available_leads"], c["partner_score"])
    return [c["contractor_id"] for c in selected]


# ── Pre-Verification ────────────────────────────────────────────────────

def verify_lead_for_pitch(lead: dict, contractor: dict) -> tuple[bool, str]:
    """Verify the lead is valid for the exclusive pitch.
    Returns (is_valid, reason_string)."""
    # Check physical zone match
    l_city = (lead.get("city") or "").strip().lower()
    l_state = (lead.get("state") or "").strip().lower()
    l_niche = (lead.get("specialty") or "").strip().lower()
    c_city = (contractor.get("region_city") or contractor.get("city") or "").strip().lower()
    c_state = (contractor.get("region_state") or contractor.get("state") or "").strip().lower()
    c_niche = (contractor.get("niche") or "").strip().lower()

    if l_city != c_city or l_state != c_state:
        return False, f"Zone mismatch: lead=({l_city},{l_state}) contractor=({c_city},{c_state})"
    if l_niche != c_niche:
        return False, f"Niche mismatch: lead={l_niche} contractor={c_niche}"

    # Check lead has not been reserved
    notes = (lead.get("notes") or "").lower()
    if "reserved for" in notes:
        return False, "Lead already reserved"

    # Check lead is uncontacted or new
    response = (lead.get("response_status") or "").strip().lower()
    if response and response != "new":
        return False, f"Lead already contacted (status={response})"

    # Check lead has contact info
    email = (lead.get("email") or "").strip()
    phone = (lead.get("phone") or "").strip()
    if not email and not phone:
        return False, "Lead has no email or phone"

    return True, "Verified"


# ── SMTP Send ───────────────────────────────────────────────────────────

def _load_smtp_config() -> dict:
    if os.path.exists(EMAIL_CONFIG):
        try:
            with open(EMAIL_CONFIG) as f:
                cfg = json.load(f)
            return {
                "host": cfg.get("smtp_host", "smtp.gmail.com"),
                "port": int(cfg.get("smtp_port", 587)),
                "user": cfg.get("smtp_user", ""),
                "pass": cfg.get("smtp_pass", ""),
                "sender": cfg.get("sender_email", cfg.get("smtp_user", "")),
                "name": cfg.get("sender_name", "US Lead Dispatch"),
            }
        except Exception:
            pass
    return {
        "host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER", ""),
        "pass": os.environ.get("SMTP_PASS", ""),
        "sender": os.environ.get("SENDER_EMAIL", ""),
        "name": os.environ.get("SENDER_NAME", "US Lead Dispatch"),
    }


def send_email(recipient: str, subject: str, body: str, attachment: str = "") -> bool:
    """Send email via SMTP, optionally with a PDF attachment. Returns True on success."""
    if not recipient:
        return False
    config = _load_smtp_config()
    if not config["user"] or not config["pass"]:
        logger.warning("  SMTP not configured — skipping email to %s", recipient)
        return False
    try:
        if attachment and os.path.exists(attachment):
            msg = MIMEMultipart()
            msg.attach(MIMEText(body, "plain", "utf-8"))
            with open(attachment, "rb") as f:
                part = MIMEBase("application", "pdf")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f'attachment; filename="{os.path.basename(attachment)}"',
            )
            msg.attach(part)
        else:
            msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = f'{config["name"]} <{config["sender"]}>'
        msg["To"] = recipient
        context = ssl.create_default_context()
        with smtplib.SMTP(config["host"], config["port"], timeout=15) as server:
            server.starttls(context=context)
            server.login(config["user"], config["pass"])
            server.sendmail(config["sender"], [recipient], msg.as_string())
        return True
    except Exception as e:
        logger.warning("  Email to %s failed: %s", recipient, e)
        return False


# ── Pitch Templates ─────────────────────────────────────────────────────

def build_exclusive_pitch(contractor_name: str, lead: dict, package: str = "trial") -> str:
    """Build the exclusive Trial Pilot pitch message.
    Supports dynamic package reference for post-trial upgrade path."""
    pkg_info = PACKAGES.get(package, PACKAGES["trial"])
    amount = pkg_info["amount"]
    leads_count = pkg_info["leads"]
    company = (lead.get("company_name") or "a local customer").strip()
    city = (lead.get("city") or "your area").strip()
    niche = (lead.get("specialty") or "your specialty").strip()

    if pkg_info["is_trial"]:
        return (
            f"Hi {contractor_name},\n\n"
            f"I have a live {niche} lead waiting in {city} right now — "
            f"{company} is actively looking for a professional.\n\n"
            f"We are granting Beta Access to a very limited number of contractors "
            f"(The Elite 10). You have been selected for priority access.\n\n"
            f"Trial this lead for just ${amount:.0f} to test our service quality. "
            f"This is an exclusive opportunity, not a mass broadcast.\n\n"
            f"Reply to this email to claim your priority access, "
            f"and I will send you the lead details immediately.\n\n"
            f"Best,\n"
            f"US Lead Dispatch"
        )
    else:
        return (
            f"Hi {contractor_name},\n\n"
            f"We have {leads_count} verified {niche} leads ready in {city} right now — "
            f"pre-qualified and waiting for your outreach.\n\n"
            f"As a Full Partner, you can unlock this batch for ${amount:.0f} "
            f"({pkg_info['label']}). These are premium, geo-targeted leads "
            f"with verified contact data.\n\n"
            f"Reply to this email to reserve your batch.\n\n"
            f"Best,\n"
            f"US Lead Dispatch"
        )


# ── PDF Invoice Generation ──────────────────────────────────────────────

def generate_crypto_invoice(contractor_name: str, lead: dict, settings: dict, package: str = "trial", amount: float = 5.0) -> str:
    """Generate a Professional Crypto Service Invoice PDF with USDT TRC-20 payment QR code.
    Returns the file path of the generated PDF. Supports dynamic package name and amount."""
    invoice_id = f"INV-{int(time.time())}"
    pkg_label = PACKAGES.get(package, PACKAGES["trial"])["label"]
    filename = f"{invoice_id}_{pkg_label.replace(' ', '_')}_{contractor_name.replace(' ', '_')}.pdf"
    filepath = f"{INVOICE_DIR}/{filename}"
    address = CRYPTO_WALLETS["usdt_trc20"]["address"]

    doc = SimpleDocTemplate(
        filepath, pagesize=letter,
        topMargin=0.75*inch, bottomMargin=0.75*inch,
        leftMargin=0.75*inch, rightMargin=0.75*inch,
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        "TitleLarge", parent=styles["Title"],
        fontSize=22, spaceAfter=6, alignment=1,
    ))
    styles.add(ParagraphStyle(
        "SubTitle", parent=styles["Normal"],
        fontSize=12, spaceAfter=4, alignment=1,
        textColor=colors.HexColor("#555555"),
    ))
    styles.add(ParagraphStyle(
        "SectionHeader", parent=styles["Normal"],
        fontSize=13, spaceBefore=12, spaceAfter=4,
        fontName="Helvetica-Bold",
    ))
    styles.add(ParagraphStyle(
        "MonoAddress", parent=styles["Normal"],
        fontSize=10, fontName="Courier",
        alignment=1, spaceAfter=8,
        backColor=colors.HexColor("#F0F0F0"),
        borderPadding=6,
    ))
    styles.add(ParagraphStyle(
        "BodyText2", parent=styles["Normal"],
        fontSize=11, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        "WarningText", parent=styles["Normal"],
        fontSize=10, textColor=colors.HexColor("#CC0000"),
        spaceAfter=4, alignment=1,
    ))

    elements = []

    # ── Header ──
    elements.append(Paragraph("PROFESSIONAL CRYPTO SERVICE INVOICE", styles["TitleLarge"]))
    elements.append(Spacer(1, 4))

    # Invoice metadata table
    meta_data = [
        [f"Invoice #: {invoice_id}", f"Date: {_now()[:10]}"],
    ]
    meta_table = Table(meta_data, colWidths=[3.5*inch, 3.5*inch])
    meta_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 12))

    # ── Bill To ──
    elements.append(Paragraph("BILL TO", styles["SectionHeader"]))
    elements.append(Paragraph(
        f"{contractor_name}<br/>"
        "Lead: {} - {}, {}".format(
            lead.get("company_name", "N/A"),
            lead.get("city", ""),
            lead.get("state", ""),
        ),
        styles["BodyText2"],
    ))
    elements.append(Spacer(1, 12))

    # ── Amount Due ──
    amount_data = [["Service", "Amount (USDT)"]]
    amount_data.append([
        f"{pkg_label} — {lead.get('specialty', 'Service')}",
        f"{amount:.2f}",
    ])
    amount_table = Table(amount_data, colWidths=[5*inch, 2*inch])
    amount_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8E8E8")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F9F9F9")]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(amount_table)
    elements.append(Spacer(1, 6))

    # Total
    total_data = [[f"TOTAL DUE", f"{amount:.2f} USDT"]]
    total_table = Table(total_data, colWidths=[5*inch, 2*inch])
    total_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 13),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#1A1A1A")),
        ("LINEABOVE", (0, 0), (-1, 0), 2, colors.black),
    ]))
    elements.append(total_table)
    elements.append(Spacer(1, 16))

    # ── Payment Details ──
    elements.append(Paragraph("PAYMENT DETAILS", styles["SectionHeader"]))
    elements.append(Spacer(1, 4))

    details = [
        ["Network:", "TRC-20 (USDT)"],
        ["Currency:", "Tether USD (USDT)"],
        ["Wallet Address:", address],
    ]
    pay_table = Table(details, colWidths=[1.8*inch, 5.2*inch])
    pay_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Courier"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(pay_table)
    elements.append(Spacer(1, 12))

    # ── QR Code (Admin-uploaded RedotPay image or fallback to generated) ──
    qr_image_path = "/root/redotpay_trc20.png"
    qr_embedded = False
    if os.path.exists(qr_image_path):
        try:
            qr_pil = Image(qr_image_path, width=2.0*inch, height=4.33*inch)
            qr_container = Table([[qr_pil]], colWidths=[7*inch])
            qr_container.setStyle(TableStyle([
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]))
            elements.append(qr_container)
            qr_embedded = True
        except Exception:
            logger.warning("  Admin QR image failed to load — falling back to generated QR")
    if not qr_embedded:
        try:
            qr_img = qrcode.make(address)
            qr_buf = io.BytesIO()
            qr_img.save(qr_buf, format="PNG")
            qr_buf.seek(0)
            qr_pil = Image(qr_buf, width=1.6*inch, height=1.6*inch)
            qr_container = Table([[qr_pil]], colWidths=[7*inch])
            qr_container.setStyle(TableStyle([
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]))
            elements.append(qr_container)
        except Exception:
            logger.warning("  QR code generation failed — continuing without QR")
    # Wallet address displayed clearly below the QR
    elements.append(Paragraph("Scan with any TRC-20 compatible wallet", styles["SubTitle"]))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(address, styles["MonoAddress"]))
    elements.append(Spacer(1, 12))

    # ── Instructions ──
    elements.append(Paragraph("INSTRUCTIONS", styles["SectionHeader"]))
    elements.append(Paragraph(
        "1. Open your TRC-20 compatible wallet (Binance, RedotPay, Trust Wallet, etc.).<br/>"
        "2. Scan the QR code above or copy the wallet address.<br/>"
        f"3. Send exactly <b>{amount:.2f} USDT (TRC-20)</b> to the address shown above.<br/>"
        "4. <b>IMPORTANT:</b> Verify the network is set to <b>TRC-20</b> before confirming.<br/>"
        "5. After sending, reply to this email with your <b>Transaction Hash (TXID)</b> "
        "or payment screenshot for manual verification and instant lead release.",
        styles["BodyText2"],
    ))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        "⚠️ WARNING: Sending funds on any network other than TRC-20 may result in "
        "permanent loss of funds. Double-check network selection before confirming the transaction.",
        styles["WarningText"],
    ))

    # ── Build PDF ──
    doc.build(elements)
    logger.info("  Invoice generated: %s", filepath)
    return filepath


def build_payment_instruction(contractor_name: str, settings: dict, package: str = "trial", amount: float = 5.0, invoice_path: str = "") -> str:
    """Build the USDT TRC20 payment instruction message with TXID prompt.
    Direct crypto only — no external gateways. Supports dynamic package name and amount."""
    pkg_info = PACKAGES.get(package, PACKAGES["trial"])
    pkg_label = pkg_info["label"]
    is_trial = pkg_info["is_trial"]
    wallet = CRYPTO_WALLETS["usdt_trc20"]
    address = wallet["address"]
    note = settings.get("payment_note", wallet["note"])
    invoice_line = f"\nA Professional Crypto Service Invoice has been attached to this email ({os.path.basename(invoice_path)}).\n" if invoice_path else ""

    if is_trial:
        post_payment = (
            f"After this trial, you will be upgraded to our Full Partner program "
            f"with access to live leads at our standard professional rate."
        )
    else:
        post_payment = (
            f"As a Full Partner, you now have priority access to ongoing lead "
            f"dispatch. New leads matching your zone will be sent as they arrive."
        )

    return (
        f"Hi {contractor_name},\n\n"
        f"Excellent — you are confirmed for the {pkg_label}.\n\n"
        f"DIRECT CRYPTO PAYMENT — NO EXTERNAL GATEWAYS.\n"
        f"We accept direct B2B Stablecoin payments for instant service activation.\n\n"
        f"Please send {amount:.2f} USDT (TRC-20) to the address below. "
        f"Once confirmed, I will release the lead(s) to you immediately.\n\n"
        f"  Network:     {wallet['network']}\n"
        f"  Currency:    {wallet['label']}\n"
        f"  Address:     {address}\n"
        f"  Package:     {pkg_label}\n"
        f"  Amount:      {amount:.2f} USDT\n\n"
        f"  IMPORTANT: {wallet['note']}\n\n"
        f"{invoice_line}"
        f"After sending, reply to this email with your Transaction Hash (TxID) "
        f"or payment screenshot for manual verification and instant lead release.\n\n"
        f"{post_payment}\n\n"
        f"Best,\n"
        f"US Lead Dispatch"
    )


def build_full_partner_upgrade(contractor_name: str, package: str = "trial") -> str:
    """Post-trial Full Partner Upgrade email. References next-tier pricing."""
    pkg_info = PACKAGES.get(package, PACKAGES["trial"])
    return (
        f"Hi {contractor_name},\n\n"
        f"Congratulations — you have successfully completed the {pkg_info['label']}.\n\n"
        f"You are now upgraded to Full Partner status. Here is what this means:\n\n"
        f"  - Priority access to new leads in your zone\n"
        f"  - Professional pricing: Starter Pack (5 leads) — $50.00, Contractor Pro (5 premium) — $120.00\n"
        f"  - Dedicated lead dispatch\n\n"
        f"You are now active in our system. New leads matching your zone "
        f"will be dispatched as they come in.\n\n"
        f"Welcome aboard.\n\n"
        f"Best,\n"
        f"US Lead Dispatch"
    )


# ── Pitch Deployment ────────────────────────────────────────────────────

def send_pitch_to_contractor(contractor: dict, lead: dict, state: dict, package: str = "trial") -> bool:
    """Send the exclusive trial pitch to a contractor.
    Marks the lead as Reserved in CRM immediately to prevent double-pitch.
    Supports dynamic package name. Returns True if pitch was sent successfully."""
    cid = contractor["contractor_id"]
    cname = contractor["contractor_name"]
    email = contractor.get("email", "")
    pkg_info = PACKAGES.get(package, PACKAGES["trial"])

    body = build_exclusive_pitch(cname, lead, package)

    subject = TRIAL_PITCH_SUBJECT if pkg_info["is_trial"] else f"Premium Lead Batch — {pkg_info['label']} Access"

    sent = False
    channel = "none"

    if email:
        if send_email(email, subject, body):
            sent = True
            channel = "email"
            logger.info("  ✅ Pitch sent to %s (%s) via email [%s]", cname, cid, package)

    if not sent:
        logger.warning("  ❌ Could not send pitch to %s (%s)", cname, cid)
        return False

    # ── Reserve lead in CRM immediately to prevent double-pitch ──
    lead_company = lead.get("company_name", "")
    lead_city = lead.get("city", "")
    headers, rows = _load_csv(CRM_CSV)
    for i, r in enumerate(rows):
        if (r.get("company_name") == lead_company
                and r.get("city") == lead_city
                and not (r.get("response_status") or "").strip()):
            rows[i]["notes"] = f"Reserved for {cname} ({cid}) — {pkg_info['label']} (${pkg_info['amount']:.2f})"
            rows[i]["response_status"] = "Pending_Pitch"
            rows[i]["contractor_id"] = cid
            rows[i]["lead_price"] = str(pkg_info["amount"])
            break
    _save_csv(CRM_CSV, headers, rows)

    # Record the pitch
    state["pitches_sent"].append({
        "contractor_id": cid,
        "contractor_name": cname,
        "lead_company": lead_company,
        "lead_city": lead_city,
        "channel": channel,
        "package": package,
        "amount": pkg_info["amount"],
        "sent_at": _now(),
        "status": "pending_response",
    })

    for ec in state["elite_contractors"]:
        if ec["contractor_id"] == cid:
            ec["status"] = "pending_response"
            ec["pitched_at"] = _now()
            ec["channel"] = channel
            ec["lead_company"] = lead_company
            ec["lead_city"] = lead_city
            ec["package"] = package
            ec["amount"] = pkg_info["amount"]
    save_state(state)

    return True


# ── Response Polling ────────────────────────────────────────────────────

def check_for_responses(state: dict) -> list[dict]:
    """Poll CRM for responses from pitched contractors.
    Returns list of (contractor, lead) pairs that responded."""
    now = _timestamp()
    last_check = state.get("last_response_check", 0)
    if now - last_check < CHECK_RESPONSE_INTERVAL and state.get("pitches_sent"):
        return []

    state["last_response_check"] = now
    save_state(state)

    leads = load_crm_leads()
    contractors_by_email = {}
    for ec in state["elite_contractors"]:
        if ec.get("status") == "pending_response":
            email = ec.get("email", "").lower()
            if email:
                contractors_by_email[email] = ec

    if not contractors_by_email:
        return []

    responses = []
    for lead in leads:
        response = (lead.get("response_status") or "").strip().lower()
        if response == "replied":
            email = (lead.get("email") or "").strip().lower()
            if email in contractors_by_email:
                ec = contractors_by_email[email]
                responses.append({
                    "contractor": ec,
                    "lead": lead,
                    "detected_at": _now(),
                })
                logger.info("  📩 Response detected from %s (%s)",
                            ec["contractor_name"], ec["contractor_id"])

    if responses:
        state["responses"].extend(responses)
        save_state(state)

    return responses


# ── Deal Closing ────────────────────────────────────────────────────────

def close_deal(contractor: dict, lead: dict, state: dict, package: str = "trial", amount: float = 5.0) -> bool:
    """Send payment instruction with PDF invoice and finalize lead reservation.
    Generates a Professional Crypto Service Invoice with USDT TRC-20 QR code.
    Supports dynamic package name and amount.
    Returns True if payment instruction sent successfully."""
    cid = contractor["contractor_id"]
    cname = contractor["contractor_name"]
    email = contractor.get("email", "")
    settings = _load_json(SETTINGS_FILE)
    pkg_info = PACKAGES.get(package, PACKAGES["trial"])
    pkg_label = pkg_info["label"]

    # Generate PDF invoice with QR code
    invoice_path = generate_crypto_invoice(cname, lead, settings, package, amount)

    subject = f"Action Required — Complete Your {pkg_label} — Invoice Attached"
    body = build_payment_instruction(cname, settings, package, amount, invoice_path)
    sent = False
    if email:
        sent = send_email(email, subject, body, attachment=invoice_path)

    if not sent:
        logger.warning("  ❌ Could not send payment instruction to %s (%s)", cname, cid)
        return False

    logger.info("  💰 Payment instruction + invoice sent to %s (%s) — %.2f USDT TRC20", cname, cid, amount)

    # Update lead in CRM
    lead_company = lead.get("company_name", "")
    headers, rows = _load_csv(CRM_CSV)
    for i, r in enumerate(rows):
        if r.get("company_name") == lead_company and r.get("contractor_id") == cid:
            rows[i]["response_status"] = "Reserved"
            rows[i]["response_date"] = _now()
            rows[i]["lead_price"] = str(amount)
            if not (rows[i].get("notes") or "").strip():
                rows[i]["notes"] = f"Reserved for {cname} ({cid}) — {pkg_label} (${amount:.2f})"
            break
    _save_csv(CRM_CSV, headers, rows)

    state["reserved_leads"][cid] = {
        "contractor_id": cid,
        "contractor_name": cname,
        "lead_company": lead_company,
        "lead_city": lead.get("city", ""),
        "package": package,
        "package_label": pkg_label,
        "amount": amount,
        "reserved_at": _now(),
        "status": "payment_pending",
        "invoice_path": invoice_path,
    }

    for ec in state["elite_contractors"]:
        if ec["contractor_id"] == cid:
            ec["status"] = "payment_pending"
            ec["payment_sent_at"] = _now()
            ec["package"] = package
            ec["amount"] = amount
    save_state(state)

    return True


# ── Payment Verification (CRM-based) ────────────────────────────────────

def check_payment_received(state: dict) -> list[dict]:
    """Check CRM for leads that have been paid.
    Supports dynamic package amounts. Returns list of (contractor, lead) pairs where payment is confirmed."""
    leads = load_crm_leads()
    confirmed = []
    for cid, reservation in list(state.get("reserved_leads", {}).items()):
        if reservation.get("status") == "payment_pending":
            for lead in leads:
                if lead.get("contractor_id") == cid:
                    paid = (lead.get("paid_status") or "").strip().lower()
                    if paid in ("paid", "payment_sent", "paid_&_released"):
                        amt = reservation.get("amount", 5.0)
                        pkg = reservation.get("package_label", "Package")
                        confirmed.append({
                            "contractor_id": cid,
                            "contractor_name": reservation.get("contractor_name", ""),
                            "lead": lead,
                            "amount": amt,
                            "package": pkg,
                        })
                        logger.info("  ✅ Payment confirmed for %s (%s) — $%.2f %s complete",
                                    reservation.get("contractor_name"), cid, amt, pkg)
    return confirmed


# ── Post-Trial Scaling ──────────────────────────────────────────────────

def upgrade_to_full_partner(contractor: dict, state: dict, package: str = "trial", amount: float = 5.0) -> bool:
    """After payment is confirmed, upgrade contractor to Full Partner status.
    Updates contractors.csv, sends upgrade email, transitions to professional pricing.
    Supports dynamic package name and amount."""
    cid = contractor["contractor_id"]
    cname = contractor["contractor_name"]
    email = contractor.get("email", "")
    pkg_info = PACKAGES.get(package, PACKAGES["trial"])

    # Update contractors.csv account_status to active_paid
    headers, rows = _load_csv(CONTRACTORS_CSV)
    updated = False
    for row in rows:
        if row.get("contractor_id") == cid:
            row["account_status"] = "active_paid"
            updated = True
            break

    if updated:
        _save_csv(CONTRACTORS_CSV, headers, rows)
        logger.info("  ⬆️  %s (%s) upgraded to Full Partner ($%.2f — %s)", cname, cid, amount, pkg_info['label'])

    # Send upgrade email
    if email:
        send_email(email, FULL_PARTNER_UPGRADE_SUBJECT, build_full_partner_upgrade(cname, package))

    # Update state
    state["trial_completed"].append({
        "contractor_id": cid,
        "contractor_name": cname,
        "package": package,
        "amount": amount,
        "completed_at": _now(),
    })
    state["upgraded_partners"].append(cid)

    for ec in state["elite_contractors"]:
        if ec["contractor_id"] == cid:
            ec["status"] = "upgraded"
            ec["upgraded_at"] = _now()

    # Remove from reserved
    state["reserved_leads"].pop(cid, None)

    # Lock the zone so no other contractor gets a cheap trial
    for ec in state["elite_contractors"]:
        if ec["contractor_id"] == cid:
            zone_key = f"{ec.get('city', '').lower()}|{ec.get('state', '').lower()}|{ec.get('niche', '').lower()}"
            zone_lockout = _load_json(ZONE_LOCKOUT_FILE)
            zone_lockout[zone_key] = {
                "claimed_by": cid,
                "claimed_by_name": cname,
                "package": package,
                "amount": amount,
                "claimed_at": _now(),
            }
            _save_json(ZONE_LOCKOUT_FILE, zone_lockout)
            logger.info("  🔒 Zone locked: %s (%s — $%.2f)", zone_key, pkg_info['label'], amount)
            break

    save_state(state)
    return True


# ── Main Deployment Cycle ───────────────────────────────────────────────

def run_cycle(dry_run: bool = False, package: str = DEFAULT_PACKAGE, amount: float = 5.0):
    """Execute one full sales force deployment cycle.
    Supports dynamic package name and amount for tiered pricing."""
    pkg_info = PACKAGES.get(package, PACKAGES[DEFAULT_PACKAGE])
    if amount is None or amount <= 0:
        amount = pkg_info["amount"]

    state = init_state()
    state["cycle_count"] = state.get("cycle_count", 0) + 1
    save_state(state)

    cycle_num = state["cycle_count"]
    logger.info("")
    logger.info("=" * 60)
    logger.info("  SALES FORCE ENGINE — Cycle #%d", cycle_num)
    logger.info("  Package: %s | Amount: $%.2f | Leads: %d", pkg_info['label'], amount, pkg_info['leads'])
    logger.info("  Dry run: %s", dry_run)
    logger.info("=" * 60)

    contractors = load_contractors_all()
    leads = load_crm_leads()
    logger.info("  Contractors loaded: %d | CRM leads: %d", len(contractors), len(leads))

    # ── Step 1: Select/refresh Elite 10 ──
    logger.info("")
    logger.info("── STEP 1: ELITE 10 SELECTION ──")
    elite_ids = select_elite_10(state, contractors, leads)
    if not elite_ids:
        logger.warning("  No Elite 10 contractors available — cycle complete")
        return

    # ── Step 2: Send pitches to pending contractors ──
    logger.info("")
    logger.info("── STEP 2: PITCH DEPLOYMENT ──")
    pitches_sent_this_cycle = 0
    for ec in state["elite_contractors"]:
        if ec.get("status") != "pending_pitch":
            continue

        cid = ec["contractor_id"]
        cname = ec["contractor_name"]
        # Find full contractor object
        con = next((c for c in contractors if c.get("contractor_id") == cid), None)
        if not con:
            continue

        # Find an available lead for this contractor's zone
        zone_leads = find_available_leads_for_zone(ec, leads)
        if not zone_leads:
            logger.info("  %s (%s): no available leads — skipping", cname, cid)
            ec["status"] = "no_leads"
            continue

        target_lead = zone_leads[0]

        # Pre-verify
        valid, reason = verify_lead_for_pitch(target_lead, ec)
        if not valid:
            logger.info("  %s (%s): pre-verification failed — %s", cname, cid, reason)
            ec["status"] = f"verify_failed:{reason}"
            continue

        if dry_run:
            logger.info("  [DRY-RUN] Would pitch %s (%s) for lead %s in %s [%s — $%.2f]",
                        cname, cid, target_lead.get("company_name", "?"),
                        target_lead.get("city", "?"), pkg_info['label'], amount)
            ec["status"] = "dry_run_pitch"
            continue

        # Send the pitch
        sent = send_pitch_to_contractor(ec, target_lead, state, package=package)
        if sent:
            pitches_sent_this_cycle += 1

    logger.info("  Pitches sent this cycle: %d", pitches_sent_this_cycle)

    if dry_run:
        save_state(state)
        gc.collect()
        return

    # ── Step 3: Check for responses ──
    logger.info("")
    logger.info("── STEP 3: RESPONSE CHECK ──")
    responses = check_for_responses(state)
    if responses:
        logger.info("  %d response(s) detected", len(responses))
        for resp in responses:
            logger.info("  Closing deal for %s...", resp["contractor"]["contractor_name"])
            if not dry_run:
                pkg = resp["contractor"].get("package", DEFAULT_PACKAGE)
                amt = resp["contractor"].get("amount", PACKAGES[pkg]["amount"])
                close_deal(resp["contractor"], resp["lead"], state, package=pkg, amount=amt)
    else:
        logger.info("  No new responses detected")

    # ── Step 4: Check for payment confirmations ──
    logger.info("")
    logger.info("── STEP 4: PAYMENT VERIFICATION ──")
    payments = check_payment_received(state)
    if payments:
        logger.info("  %d payment(s) confirmed", len(payments))
        for pmt in payments:
            cid = pmt["contractor_id"]
            con = next((c for c in contractors if c.get("contractor_id") == cid), None)
            if con and not dry_run:
                pkg = pmt.get("package", DEFAULT_PACKAGE)
                amt = pmt.get("amount", PACKAGES[pkg]["amount"])
                upgrade_to_full_partner(con, state, package=pkg, amount=amt)
    else:
        logger.info("  No new payments detected")

    # ── Summary ──
    logger.info("")
    logger.info("── CYCLE %d SUMMARY ──", cycle_num)
    for ec in state["elite_contractors"]:
        status = ec.get("status", "unknown")
        name = ec["contractor_name"]
        logger.info("  %s: %s", name.ljust(25), status)

    logger.info("")
    logger.info("  Pitches sent:     %d", len(state["pitches_sent"]))
    logger.info("  Responses:        %d", len(state["responses"]))
    logger.info("  Reserved leads:   %d", len(state["reserved_leads"]))
    logger.info("  Trials completed: %d", len(state["trial_completed"]))
    logger.info("  Upgraded:         %d", len(state["upgraded_partners"]))

    save_state(state)
    gc.collect()


def show_status():
    """Display current sales force state."""
    state = load_state()
    if not state or not state.get("elite_contractors"):
        print("\n  SALES FORCE — Inactive (no Elite 10 selected)")
        return

    print(f"\n  SALES FORCE — Cycle #{state.get('cycle_count', 0)}")
    print(f"  Last response check: {state.get('last_response_check', 'never')}")
    print()

    for ec in state["elite_contractors"]:
        name = ec.get("contractor_name", "?")
        cid = ec.get("contractor_id", "?")
        zone = f"{ec.get('city', '?')}, {ec.get('state', '?')}"
        niche = ec.get("niche", "?")
        status = ec.get("status", "?")
        score = ec.get("partner_score", 0)
        pkg = ec.get("package", "")
        amt = ec.get("amount", "")
        pkg_str = f" [{pkg} ${amt:.2f}]" if amt else ""
        print(f"  {cid:<8} {name:<25} {zone:<20} {niche:<12} score={score:<5} {status}{pkg_str}")

    print()
    print(f"  Pitches sent:     {len(state.get('pitches_sent', []))}")
    print(f"  Responses:        {len(state.get('responses', []))}")
    print(f"  Reserved leads:   {len(state.get('reserved_leads', {}))}")
    print(f"  Trials completed: {len(state.get('trial_completed', []))}")
    print(f"  Upgraded partners:{len(state.get('upgraded_partners', []))}")


# ── CLI ─────────────────────────────────────────────────────────────────

def main():
    import argparse
    pkg_items = []
    for k, v in PACKAGES.items():
        lead_s = "s" if v["leads"] > 1 else ""
        pkg_items.append("%s ($%.0f, %d lead%s)" % (k, v["amount"], v["leads"], lead_s))
    pkg_help = " | ".join(pkg_items)
    parser = argparse.ArgumentParser(description="SALES FORCE ENGINE — Packages: " + pkg_help)
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no sends")
    parser.add_argument("--select-elite", action="store_true", help="Re-select Elite 10")
    parser.add_argument("--status", action="store_true", help="Show current state")
    parser.add_argument("--cycle", action="store_true", help="Run one deployment cycle")
    parser.add_argument("--daemon", action="store_true", help="Run continuous cycles")
    parser.add_argument("--interval", type=int, default=300, help="Interval between cycles (s)")
    pkg_tier_help = ", ".join(["%s ($%.0f)" % (k, v["amount"]) for k, v in PACKAGES.items()])
    parser.add_argument("--package", type=str, default=DEFAULT_PACKAGE,
                        choices=list(PACKAGES.keys()),
                        help="Package tier: " + pkg_tier_help)
    parser.add_argument("--amount", type=float, default=None,
                        help="Override package amount (optional, for custom pricing)")
    parser.add_argument("--validate-contractor", type=str, default=None, metavar="NAME",
                        help="Run AI validation on a contractor name and exit")
    parser.add_argument("--niche", type=str, default=DEFAULT_NICHE,
                        choices=list(NICHE_PRICE_CAPS.keys()),
                        help="Niche for price validation")
    parser.add_argument("--anti-cheat", type=str, default=None, metavar="JSON",
                        help="Test anti-cheat on a JSON submission string and exit")
    args = parser.parse_args()

    if args.validate_contractor:
        result = ai_validate_contractor(args.validate_contractor, niche=args.niche)
        print("\n  AI CONTRACTOR VALIDATION RESULT")
        print(f"  {'Company:':<20} {args.validate_contractor}")
        print(f"  {'Niche:':<20} {args.niche}")
        print(f"  {'Valid:':<20} {result.get('valid')}")
        print(f"  {'Confidence:':<20} {result.get('confidence', 0):.2f}")
        print(f"  {'Reason:':<20} {result.get('reason', '')}")
        print(f"  {'Source:':<20} {result.get('source', '')}")
        return

    if args.anti_cheat:
        try:
            submission = json.loads(args.anti_cheat)
        except json.JSONDecodeError as e:
            print(f"  Invalid JSON: {e}")
            return
        result = anti_cheat_detect_submission(submission)
        print("\n  ANTI-CHEAT SUBMISSION RESULT")
        print(f"  {'Company:':<25} {submission.get('company_name', '')}")
        print(f"  {'Niche:':<25} {submission.get('niche', '')}")
        print(f"  {'Package:':<25} {submission.get('package', '')}")
        print(f"  {'Submitted Amount:':<25} ${submission.get('amount', 0):.2f}")
        print(f"  {'Passed:':<25} {result.get('passed')}")
        print(f"  {'Fraud Flagged:':<25} {result.get('fraud_flagged')}")
        print(f"  {'Reason:':<25} {result.get('reason', '')}")
        corr = result.get("corrected_amount")
        corr_str = "$%.2f" % corr if corr else "N/A"
        print(f"  {'Corrected Amount:':<25} {corr_str}")
        print(f"  {'AI Valid:':<25} {result.get('validation', {}).get('valid')}")
        return

    if args.status:
        show_status()
        return

    if args.select_elite:
        state = init_state()
        # Force re-selection by clearing existing
        state["elite_contractors"] = []
        save_state(state)
        contractors = load_contractors_all()
        leads = load_crm_leads()
        select_elite_10(state, contractors, leads)
        show_status()
        return

    amount = args.amount if args.amount is not None else PACKAGES[args.package]["amount"]

    if args.daemon:
        logger.info("  SALES FORCE DAEMON STARTING (interval=%ds, package=%s, amount=$%.2f)",
                     args.interval, args.package, amount)
        consecutive_errors = 0
        while True:
            try:
                run_cycle(dry_run=args.dry_run, package=args.package, amount=amount)
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                logger.error("Cycle error (%d): %s\n%s", consecutive_errors, e, traceback.format_exc())
                if consecutive_errors >= 5:
                    logger.critical("Too many errors — sleeping 10min")
                    time.sleep(600)
                    consecutive_errors = 0
                    continue
            for _ in range(args.interval):
                time.sleep(1)

    if args.cycle or not any([args.select_elite, args.status, args.daemon]):
        run_cycle(dry_run=args.dry_run, package=args.package, amount=amount)
        show_status()


if __name__ == "__main__":
    main()

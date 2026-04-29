from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mimetypes
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[2]
INDEX_CSV = REPO_ROOT / "leads" / "index.csv"
MISSING_FIELDS_MD = REPO_ROOT / "leads" / "views" / "missing-fields.md"
SEND_SUPPRESSIONS_JSON = REPO_ROOT / "notes" / "leadops-send-suppressions.json"
ENTITY_ALIASES_JSON = REPO_ROOT / "notes" / "leadops-entity-aliases.json"
ENTITY_PROMOTIONS_JSON = REPO_ROOT / "notes" / "leadops-entity-promotions.json"
REVIEW_DECISIONS_CSV = REPO_ROOT / "notes" / "leadops-review-decisions.csv"
CONTACT_LOG_MD = REPO_ROOT / "outreach" / "logs" / "contact-log.md"
OPT_OUT_LOG_MD = REPO_ROOT / "outreach" / "logs" / "opt-out-log.md"
SENT_ITEMS_JSON = REPO_ROOT / "outreach" / "exports" / "sent-items.json"
DELIVERED_ITEMS_JSON = REPO_ROOT / "outreach" / "exports" / "delivered-emails.json"
DRAFTS_JSON = REPO_ROOT / "outreach" / "exports" / "drafts.json"
AUDIT_EXPORTS_DIR = REPO_ROOT / "outreach" / "exports"
DEEP_AUDIT_QUEUE_JSON = REPO_ROOT / "outreach" / "exports" / "uncontacted-need-deep-audit-final.json"
BOUNCE_SUPPRESSION_JSON = REPO_ROOT / "outreach" / "exports" / "bounce-suppression.json"
ALL_BOUNCED_EMAILS_JSON = REPO_ROOT / "outreach" / "logs" / "all-bounced-emails.json"
BOUNCED_EMAILS_JSON = REPO_ROOT / "outreach" / "logs" / "bounced-emails.json"
DRAFTS_REVISED_JSON = REPO_ROOT / "outreach" / "exports" / "drafts-revised.json"
BOUNCE_FOLLOWUP_WORKLIST_JSON = REPO_ROOT / "outreach" / "queues" / "bounce-followup-worklist.json"
DBA_CANDIDATES_JSON = REPO_ROOT / "notes" / "dba-candidates-batch1-2.json"
CONTACT_PATH_SNIPPETS_TXT = REPO_ROOT / "notes" / "contact-paths-snippets.txt"
PROFILES_ROOT = REPO_ROOT / "leads" / "profiles"
AUDIT_REVIEW_ROOT = REPO_ROOT / "ops" / "audit-review"
EVIDENCE_INDEX_ROOTS = (
    PROFILES_ROOT,
    AUDIT_REVIEW_ROOT,
)
TMP_DIR = REPO_ROOT / "tmp"
DEFAULT_DB = REPO_ROOT / "crm.sqlite"
DEFAULT_FAST_SEMANTIC_DB = TMP_DIR / "crm.semantic-fast.sqlite"
DEFAULT_QUALITY_SEMANTIC_DB = TMP_DIR / "crm.semantic-quality.sqlite"
PROFILE_PARSE_VERSION = "2026-04-02-v18"
DEEP_INDEX_PARSE_VERSION = "2026-03-24-v1"
ENRICHMENT_MIN_TRUST_LEVEL = "observed"
DEFAULT_FAST_EMBEDDING_MODEL_KEY = str(
    (
        REPO_ROOT
        / "ai-models"
        / "music"
        / "ACE-Step-1.5"
        / "checkpoints"
        / "Qwen3-Embedding-0.6B"
    ).resolve()
)
DEFAULT_QUALITY_EMBEDDING_MODEL_KEY = str(
    (
        REPO_ROOT
        / "ai-models"
        / "music"
        / "ACE-Step-1.5"
        / "checkpoints"
        / "Qwen3-Embedding-4B-GGUF"
        / "Qwen3-Embedding-4B-Q4_K_M.gguf"
    ).resolve()
)
DEFAULT_VECTOR_MODEL_KEYS = (
    DEFAULT_FAST_EMBEDDING_MODEL_KEY,
    DEFAULT_QUALITY_EMBEDDING_MODEL_KEY,
)

EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?){2}\d{4}")
LEAD_ID_PREFIX_RE = re.compile(r"^(\d+)[-_]")
SUBJECT_RE = re.compile(r"Subject:\s*([^|]+)")
SECTION_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
ACTION_LINE_RE = re.compile(r"^[-*]?\s*Action:\s*(.+)$", re.IGNORECASE)
SEVERITY_LINE_RE = re.compile(
    r"^[-*]?\s*(Giant/Critical|Big|Medium|Small):\s*(.+)$",
    re.IGNORECASE,
)
MARKDOWN_LABEL_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 /+&?()'-]{1,48}):\s*(.+)$")

SECTION_ALIASES = {
    "snapshot": {"Snapshot"},
    "observations": {"Observations", "Notes"},
    "business_overview": {"Business overview", "Business Summary", "Business Information", "Business Details", "Business Identity", "Business Description"},
    "contact_decision_makers": {"Contact + decision makers", "Contact and decision makers"},
    "online_presence": {"Online presence", "Online Presence"},
    "market_position": {"Market position", "Competitors"},
    "opportunity_assessment": {"Opportunity assessment", "Opportunity assessment (including partnerships)", "Partnership opportunities"},
    "lead_metadata": {"Lead metadata"},
    "website_presence": {"Website presence", "Website Presence"},
    "audit_highlights": {"Audit highlights (ordered)", "Audit highlights", "Key Findings", "Verification Notes"},
    "security_trust": {"Security and trust", "Security headers check", "Security Headers Check", "Deep Security Audit (Passive)", "Deep Security Audit"},
    "ux_conversion": {"UX and conversion", "Contact path status"},
    "performance_tech": {"Performance and tech", "Console and runtime notes", "Lighthouse mobile (contact page)", "Website Audit (Mobile)", "Website audit (manual)", "Website audit (quick)"},
    "google_business_profile": {"Google Business Profile", "Google Business profile"},
    "social_presence": {"Social presence", "Social Presence", "Social Media"},
    "sources": {"Sources"},
    "contact_information": {"Contact Information", "Contact information"},
    "outreach_section": {"Outreach"},
    "outreach_angle": {"Outreach angle"},
    "next_steps": {"Next steps"},
    "evidence": {"Evidence"},
    "outreach_log": {"Outreach log", "Outreach Log"},
}

SECTION_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "business_overview": (
        re.compile(r"^business (overview|summary|information|details|identity|description)\b", re.IGNORECASE),
    ),
    "online_presence": (
        re.compile(r"^online presence\b", re.IGNORECASE),
    ),
    "website_presence": (
        re.compile(r"^website presence\b", re.IGNORECASE),
    ),
    "audit_highlights": (
        re.compile(r"^audit highlights?\b", re.IGNORECASE),
        re.compile(r"^key findings\b", re.IGNORECASE),
        re.compile(r"^verification notes\b", re.IGNORECASE),
    ),
    "security_trust": (
        re.compile(r"^security (and trust|headers?)\b", re.IGNORECASE),
        re.compile(r"^deep security audit\b", re.IGNORECASE),
    ),
    "ux_conversion": (
        re.compile(r"^ux( and| &) conversion\b", re.IGNORECASE),
        re.compile(r"^contact path status\b", re.IGNORECASE),
    ),
    "performance_tech": (
        re.compile(r"^performance and tech\b", re.IGNORECASE),
        re.compile(r"^console and runtime notes\b", re.IGNORECASE),
        re.compile(r"^lighthouse\b", re.IGNORECASE),
        re.compile(r"^website audit\b", re.IGNORECASE),
    ),
    "google_business_profile": (
        re.compile(r"^google business profile\b", re.IGNORECASE),
    ),
    "social_presence": (
        re.compile(r"^social (presence|media)\b", re.IGNORECASE),
    ),
    "contact_information": (
        re.compile(r"^contact information\b", re.IGNORECASE),
    ),
    "outreach_angle": (
        re.compile(r"^outreach angle\b", re.IGNORECASE),
    ),
    "next_steps": (
        re.compile(r"^next steps\b", re.IGNORECASE),
    ),
}

LOW_INFORMATION_SECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*-\s*$"),
    re.compile(r"^\s*pending research\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*pending review\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*auto-restored from explicit worklist profile path\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*(giant/critical|big|medium|small):\s*pending review\.?\s*$", re.IGNORECASE),
)

NARRATIVE_SECTION_NAMES = ("Notes", "Observations", "Research Notes", "Evidence")
BUSINESS_NARRATIVE_SECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^business (overview|summary|information|details|identity|description)\b", re.IGNORECASE),
    re.compile(r"^research notes\b", re.IGNORECASE),
    re.compile(r"^notes\b", re.IGNORECASE),
    re.compile(r"^snapshot\b", re.IGNORECASE),
    re.compile(r"^observations\b", re.IGNORECASE),
)
DISQUALIFICATION_SECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^exclusion reason\b", re.IGNORECASE),
    re.compile(r"^disqualification\b", re.IGNORECASE),
    re.compile(r"^property llc(?:\?| check)?\b", re.IGNORECASE),
    re.compile(r"^chain(?: with >3 locations\?| check)\b", re.IGNORECASE),
    re.compile(r"^duplicate note\b", re.IGNORECASE),
    re.compile(r"^relationship to lead\b", re.IGNORECASE),
    re.compile(r"^recommendation\b", re.IGNORECASE),
)
ONLINE_PRESENCE_FACT_MARKERS = (
    "no verifiable online presence",
    "no verifiable commercial presence",
    "no digital footprint",
    "no operating business contact path",
    "no web presence/contact info",
    "no verifiable website",
    "no verified official website",
    "no qualifying profile was confirmed",
    "website is no longer active",
    "no longer active",
)
WEBSITE_PRESENCE_FACT_MARKERS = (
    "website is no longer active",
    "no longer active",
    "no verifiable website",
    "no verified official website",
    "no official website found",
    "no web presence/contact info",
    "no usable urls",
    "no safe official match",
    "no qualifying profile was confirmed",
)
LEAD_METADATA_FACT_MARKERS = (
    "sales-tax entity in texas",
    "franchise-tax entity in texas",
    "multi-tenant",
)
CONSERVATIVE_MATCH_REJECTION_MARKERS = (
    "no qualified candidate from web search results",
    "no result met conservative matching rules",
    "no conservative business match found during research",
)
NEGATIVE_CONTACT_MARKERS = (
    "no direct",
    "no official",
    "no public",
    "not produce a verified",
    "not verified",
    "could not verify",
    "identity ambiguity remains",
    "bounced email",
)
SECURITY_TRUST_KEYWORDS = (
    "security",
    "header",
    "https",
    "tls",
    "ssl",
    "certificate",
    "strict-transport-security",
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
    "spf",
    "dmarc",
    "dkim",
    "mixed content",
    "cookie",
)
UX_CONVERSION_KEYWORDS = (
    "contact flow",
    "contact page",
    "contact form",
    "form",
    "mailto",
    "booking",
    "calendar",
    "newsletter",
    "subscribe",
    "cta",
    "checkout",
    "conversion",
)
SERVICE_OFFERING_LABELS = {
    "applications",
    "capabilities",
    "description",
    "offerings",
    "product offerings",
    "products",
    "service",
    "service offerings",
    "services",
    "specializes in",
}
TARGET_CUSTOMER_LABELS = {
    "audience",
    "business type",
    "client base",
    "clients",
    "customer base",
    "customers include",
    "likely client base",
    "target customer",
    "target customers",
}
DIFFERENTIATOR_LABELS = {
    "certifications",
    "competitive advantage",
    "credentials",
    "differentiators",
    "experience",
    "strengths",
}
TARGET_CUSTOMER_KEYWORDS = (
    "serves ",
    "serving ",
    "customers include",
    "client base",
    "residential and commercial",
    "residential clients",
    "commercial clients",
    "homeowners",
    "municipal",
    "industrial clients",
    "b2b",
)
DIFFERENTIATOR_KEYWORDS = (
    "veteran-owned",
    "family-owned",
    "woman-owned",
    "24/7",
    "50+ years",
    "certification",
    "certifications",
    "licensed",
    "master electrician",
    "cage",
    "uei",
    "credibility signals",
    "established operation",
)
POSITIVE_DISQUALIFICATION_MARKERS = (
    "duplicate",
    "property holding",
    "multiple locations",
    "traveling carnival",
    "chain with",
    "not local",
    "no active business",
    "excluded",
    "disqualified",
    "wrong target",
)
DISQUALIFICATION_NOTE_MARKERS = (
    "no-contact exhausted disqualification",
    "phone-only after re-check",
    "no reliable public non-phone contact path verified",
)

GENERIC_EMAIL_DOMAINS = {
    "gmail.com",
    "outlook.com",
    "hotmail.com",
    "yahoo.com",
    "aol.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "live.com",
    "msn.com",
    "protonmail.com",
    "pm.me",
}

ENTITY_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "business",
    "co",
    "com",
    "company",
    "corp",
    "corporation",
    "county",
    "enterprise",
    "enterprises",
    "group",
    "holdings",
    "inc",
    "incorporated",
    "international",
    "llc",
    "llp",
    "lp",
    "ltd",
    "management",
    "of",
    "on",
    "owner",
    "partners",
    "pllc",
    "services",
    "solutions",
    "store",
    "system",
    "systems",
    "the",
    "tx",
}

UNTRUSTED_CLUSTER_DOMAINS = {
    "example.com",
    "duckduckgo.com",
    "merriam-webster.com",
    "bizapedia.com",
    "en.wikipedia.org",
    "yes",
    "no",
    "unknown",
    "none",
    "n/a",
    "na",
    "null",
    "false",
    "true",
    "not",
}

UNTRUSTED_CLUSTER_SUFFIXES = (
    ".m-w.com",
    ".reddit.com",
)

ENRICHMENT_TRUST_ORDER = {
    "unverified": 0,
    "inferred": 1,
    "observed": 2,
    "verified": 3,
}

ENRICHMENT_SOCIAL_HOSTS = {
    "facebook_url": ("facebook.com", "fb.com"),
    "instagram_url": ("instagram.com",),
    "twitter_url": ("twitter.com", "x.com"),
    "linkedin_url": ("linkedin.com",),
    "youtube_url": ("youtube.com", "youtu.be"),
    "tiktok_url": ("tiktok.com",),
    "yelp_url": ("yelp.com",),
    "google_business_url": ("google.com", "g.page"),
}

ENRICHMENT_SOCIAL_FIELDS = tuple(ENRICHMENT_SOCIAL_HOSTS.keys())

ENRICHMENT_DIRECT_PROFILE_MAP = {
    "address_raw": "address",
    "business_description": "business_overview",
    "google_business_url": "google_business_profile",
}

ENRICHMENT_DIRECT_CONTACT_FIELDS = {
    "email_primary": "email",
    "phone_primary": "phone",
}

ENRICHMENT_EXCLUDED_FACT_FIELDS = {
    "lead_id",
    "domain",
    "website_url",
    "email_primary",
    "phone_primary",
}

ENRICHMENT_BOILERPLATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"requires cookies to be enabled", re.IGNORECASE),
    re.compile(r"enable cookies", re.IGNORECASE),
    re.compile(r"checking the site connection security", re.IGNORECASE),
    re.compile(r"site connection security", re.IGNORECASE),
)

DOMAINISH_RE = re.compile(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def norm(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(str(part).strip() for part in value if str(part).strip()).strip()
    return str(value).strip()


def low(value: str | None) -> str:
    return norm(value).lower()


def clean_markdown_inline(value: str | None) -> str:
    text = norm(value)
    if not text:
        return ""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_`]+", "", text)
    return norm(text.replace("\ufeff", ""))


def is_blank(value: object | None) -> bool:
    text = norm(value)
    if not text:
        return True
    return text.lower() == "unknown"


def parse_isoish_datetime(value: str | None) -> str:
    raw = norm(value)
    if not raw:
        return ""
    for fmt in (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%a, %d %b %Y %H:%M:%S",
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S ",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S %z (%Z)",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.isoformat(sep=" ", timespec="minutes")
        except ValueError:
            continue
    return raw


def normalize_domain(url_or_domain: str | None) -> str:
    value = norm(url_or_domain)
    if not value:
        return ""
    if "://" not in value:
        value = f"https://{value}"
    try:
        host = urlparse(value).netloc.lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def normalize_email(email: str | None) -> str:
    return low(email)


def enrichment_trust_rank(level: str | None) -> int:
    return ENRICHMENT_TRUST_ORDER.get(low(level), 0)


def enrichment_should_accept(meta: dict[str, object], min_trust: str = ENRICHMENT_MIN_TRUST_LEVEL) -> bool:
    value = meta.get("value")
    if isinstance(value, list):
        cleaned = [norm(item) for item in value if norm(item)]
        if not cleaned:
            return False
    elif isinstance(value, bool):
        if not value:
            return False
    elif is_blank(value):
        return False
    return enrichment_trust_rank(norm(meta.get("trustLevel"))) >= enrichment_trust_rank(min_trust)


def normalize_enrichment_contact_value(contact_type: str, value: object | None) -> str:
    text = norm(value)
    if not text:
        return ""
    if contact_type == "email":
        return normalize_email(text)
    if contact_type == "phone":
        return re.sub(r"\D+", "", text)
    if contact_type in {"social", "website"}:
        return text.rstrip("/").lower()
    return text.lower()


def enrichment_social_value(value: object | None) -> str:
    if isinstance(value, str):
        return norm(value)
    if isinstance(value, dict):
        return norm(value.get("url"))
    return ""


def enrichment_host(value: str | None) -> str:
    text = norm(value)
    if not text:
        return ""
    parsed = urlparse(text)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def enrichment_social_ok(field: str, value: object | None) -> bool:
    allowed_hosts = ENRICHMENT_SOCIAL_HOSTS.get(field)
    if not allowed_hosts:
        return False
    site = enrichment_host(enrichment_social_value(value))
    if not site:
        return False
    return any(site == allowed or site.endswith(f".{allowed}") for allowed in allowed_hosts)


def enrichment_matches_boilerplate(value: object | None) -> bool:
    text = norm(value)
    if not text:
        return False
    return any(pattern.search(text) for pattern in ENRICHMENT_BOILERPLATE_PATTERNS)


def enrichment_email_domain_ok(value: object | None, field_confidence: dict[str, dict[str, object]]) -> bool:
    email = normalize_email(norm(value))
    if not email or "@" not in email:
        return False
    email_domain = email.split("@", 1)[1]
    if email_domain in GENERIC_EMAIL_DOMAINS:
        return True
    domain_meta = field_confidence.get("domain")
    verified_domain = ""
    if isinstance(domain_meta, dict):
        verified_domain = normalize_domain(norm(domain_meta.get("value")))
    if not verified_domain:
        website_meta = field_confidence.get("website_url")
        if isinstance(website_meta, dict):
            verified_domain = normalize_domain(norm(website_meta.get("value")))
    return bool(verified_domain) and email_domain == verified_domain


def enrichment_value_is_usable(
    field: str,
    value: object | None,
    field_confidence: dict[str, dict[str, object]],
) -> bool:
    text = norm(value)
    if not text:
        return False
    if enrichment_matches_boilerplate(text):
        return False
    if field == "email_primary":
        return enrichment_email_domain_ok(text, field_confidence)
    if field == "business_name" and DOMAINISH_RE.match(text):
        return False
    return True


def iter_enrichment_records() -> Iterator[tuple[int, str, dict[str, dict[str, object]]]]:
    for path in sorted(PROFILES_ROOT.rglob("enrichment.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        field_confidence = payload.get("fieldConfidence")
        if not isinstance(field_confidence, dict):
            enrichment = payload.get("enrichment")
            if isinstance(enrichment, dict):
                field_confidence = enrichment.get("fieldConfidence")
        if not isinstance(field_confidence, dict) or not field_confidence:
            continue
        lead_id_text = None
        lead_meta = field_confidence.get("lead_id")
        if isinstance(lead_meta, dict):
            lead_id_text = norm(lead_meta.get("value"))
        if not lead_id_text:
            match = LEAD_ID_PREFIX_RE.match(path.parent.parent.name)
            if match:
                lead_id_text = match.group(1)
        if not lead_id_text or not lead_id_text.isdigit():
            continue
        relative_path = path.relative_to(REPO_ROOT).as_posix()
        yield int(lead_id_text), relative_path, field_confidence


def is_trustworthy_cluster_domain(domain: str | None) -> bool:
    value = normalize_domain(domain)
    if not value:
        return False
    if "." not in value:
        return False
    if value in UNTRUSTED_CLUSTER_DOMAINS:
        return False
    return not any(value.endswith(suffix) for suffix in UNTRUSTED_CLUSTER_SUFFIXES)


DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CURRENCY_RE = re.compile(r"^\$\d+(?:\.\d+)?$")


def looks_like_date_only(value: str | None) -> bool:
    return bool(DATE_ONLY_RE.match(norm(value)))


def looks_like_currency(value: str | None) -> bool:
    return bool(CURRENCY_RE.match(norm(value)))


def looks_like_batch_label(value: str | None) -> bool:
    candidate = low(value)
    return candidate.startswith("registered-entities-batch-")


def derive_name_from_profile_path(profile_path: str | None) -> str:
    raw = norm(profile_path)
    if not raw:
        return ""
    try:
        parent = Path(raw).parent.name
    except Exception:
        return ""
    if not parent:
        return ""
    parent = re.sub(r"^\d+[-_]", "", parent)
    parent = parent.replace("-", " ").replace("_", " ").strip()
    return parent


def normalize_index_status(value: str | None, *, fallback: str = "") -> str:
    status = low(value)
    if not status or looks_like_date_only(status) or looks_like_currency(status):
        return fallback
    return norm(value)


def normalize_index_outreach_status(value: str | None, *, fallback: str = "uncontacted") -> str:
    status = normalize_outreach_status(value)
    if not status or looks_like_date_only(status) or looks_like_currency(status):
        return fallback
    return status


def normalize_contact_log_channel(value: str | None) -> str:
    channel = low(value)
    if channel in {"form", "contact form"}:
        return "contact form"
    return channel


@dataclass
class LeadRow:
    lead_id: int
    name: str
    batch: str
    status: str
    outreach_status: str
    contact_path: str
    contact_search: str
    email: str
    email_domain: str
    phone: str
    website: str
    website_domain: str
    contact_form: str
    social_media: str
    website_status: str
    social_checked: str
    source: str
    disqualified: int
    updated: str
    profile_path: str
    raw_index_json: str


def score_csv_row(row: dict[str, str]) -> int:
    score = 0
    for key, value in row.items():
        if norm(value):
            score += 1
            if key in {"Email", "Website", "Phone", "ProfilePath"}:
                score += 2
    return score


def merge_csv_rows(base: dict[str, str], incoming: dict[str, str]) -> dict[str, str]:
    merged = dict(base)
    base_score = score_csv_row(base)
    incoming_score = score_csv_row(incoming)
    preferred = incoming if incoming_score > base_score else base
    other = base if preferred is incoming else incoming

    for key in set(base) | set(incoming):
        preferred_value = norm(preferred.get(key))
        other_value = norm(other.get(key))
        merged[key] = preferred_value or other_value

    if low(base.get("Disqualified")) == "yes" or low(incoming.get("Disqualified")) == "yes":
        merged["Disqualified"] = "yes"
    if low(base.get("Status")) == "disqualified" or low(incoming.get("Status")) == "disqualified":
        merged["Status"] = "disqualified"
    return merged


def load_json(path: Path) -> object:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8-sig", errors="ignore"))


def parse_profile_markdown(path: Path) -> dict[str, object]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    lines = raw.splitlines()
    title = ""
    kv: dict[str, str] = {}
    sections: dict[str, str] = {}
    current_section: str | None = None
    current_lines: list[str] = []
    in_frontmatter = True

    for line in lines:
        if not title and line.startswith("# "):
            title = line[2:].strip()
            continue

        if in_frontmatter and line.startswith("## "):
            in_frontmatter = False
            current_section = line[3:].strip()
            current_lines = []
            continue

        if in_frontmatter:
            if ":" in line and not line.startswith("|"):
                key, value = line.split(":", 1)
                kv[key.strip()] = value.strip()
            continue

        if line.startswith("## "):
            if current_section is not None:
                sections[current_section] = "\n".join(current_lines).strip()
            current_section = line[3:].strip()
            current_lines = []
            continue

        current_lines.append(line)

    if current_section is not None:
        sections[current_section] = "\n".join(current_lines).strip()

    distance_raw = kv.get("Distance (zip centroid)", "")
    distance_miles = None
    if distance_raw:
        try:
            distance_miles = float(distance_raw.split()[0])
        except ValueError:
            distance_miles = None

    canonical_sections = canonicalize_sections(sections)
    website_audit = extract_website_audit(sections)
    evidence_backfills = extract_evidence_profile_backfills(path)
    evidence_sidebars = extract_evidence_sidebars_from_raw(raw)
    narrative_contact = extract_contact_information_from_narrative(sections)
    narrative_next_steps = extract_next_steps_from_narrative(sections)
    narrative_audit_backfills = extract_narrative_audit_backfills(sections)
    business_narrative = extract_business_narrative_fields(sections)
    disqualification_rationale = extract_disqualification_rationale(kv, sections)
    narrative_online_presence = extract_online_presence_from_disqualification_context(sections)
    narrative_website_presence = extract_website_presence_from_disqualification_context(sections)
    narrative_lead_metadata = extract_lead_metadata_from_disqualification_context(sections)
    extracted_address = extract_address_from_profile(lines, kv, sections)

    snapshot_text = canonical_sections.get("snapshot", "")
    if not norm(canonical_sections.get("business_overview")) and is_substantive_section(snapshot_text):
        canonical_sections["business_overview"] = snapshot_text.strip()

    contact_dm_text = canonical_sections.get("contact_decision_makers", "")
    if not norm(canonical_sections.get("contact_information")) and is_substantive_section(contact_dm_text):
        canonical_sections["contact_information"] = contact_dm_text.strip()
    elif not norm(canonical_sections.get("contact_information")) and is_substantive_section(narrative_contact):
        canonical_sections["contact_information"] = narrative_contact.strip()

    if not norm(canonical_sections.get("online_presence")) and is_substantive_section(narrative_online_presence):
        canonical_sections["online_presence"] = narrative_online_presence.strip()
    if not norm(canonical_sections.get("website_presence")) and is_substantive_section(narrative_website_presence):
        canonical_sections["website_presence"] = narrative_website_presence.strip()
    if not norm(canonical_sections.get("lead_metadata")) and is_substantive_section(narrative_lead_metadata):
        canonical_sections["lead_metadata"] = narrative_lead_metadata.strip()

    if not norm(canonical_sections.get("performance_tech")) and is_substantive_section(website_audit):
        canonical_sections["performance_tech"] = website_audit.strip()

    for field_name in ("audit_highlights", "security_trust", "ux_conversion"):
        fallback_value = evidence_backfills.get(field_name)
        if not norm(canonical_sections.get(field_name)) and is_substantive_section(fallback_value):
            canonical_sections[field_name] = fallback_value.strip()
            continue
        narrative_value = narrative_audit_backfills.get(field_name)
        if not norm(canonical_sections.get(field_name)) and is_substantive_section(narrative_value):
            canonical_sections[field_name] = narrative_value.strip()

    if not norm(canonical_sections.get("next_steps")) and is_substantive_section(narrative_next_steps):
        canonical_sections["next_steps"] = narrative_next_steps.strip()

    if is_substantive_section(evidence_sidebars):
        merged_evidence: list[str] = []
        seen_evidence_blocks: set[str] = set()
        for chunk in (canonical_sections.get("evidence", ""), evidence_sidebars):
            value = norm(chunk)
            if value and value not in seen_evidence_blocks:
                seen_evidence_blocks.add(value)
                merged_evidence.append(value)
        canonical_sections["evidence"] = "\n\n".join(merged_evidence).strip()

    return {
        "title": title,
        "kv": kv,
        "sections": sections,
        "canonical_sections": canonical_sections,
        "raw_markdown": raw,
        "address": extracted_address,
        "naics": kv.get("NAICS", ""),
        "distance_miles": distance_miles,
        "decision_maker": kv.get("Decision maker", ""),
        "last_updated": kv.get("Last updated", ""),
        "snapshot": canonical_sections.get("snapshot", ""),
        "observations": canonical_sections.get("observations", ""),
        "business_overview": canonical_sections.get("business_overview", ""),
        "service_offerings": business_narrative.get("service_offerings", ""),
        "target_customers": business_narrative.get("target_customers", ""),
        "differentiators": business_narrative.get("differentiators", ""),
        "contact_decision_makers": canonical_sections.get("contact_decision_makers", ""),
        "online_presence": canonical_sections.get("online_presence", ""),
        "market_position": canonical_sections.get("market_position", ""),
        "opportunity_assessment": canonical_sections.get("opportunity_assessment", ""),
        "disqualification_rationale": disqualification_rationale,
        "lead_metadata": canonical_sections.get("lead_metadata", ""),
        "website_presence": canonical_sections.get("website_presence", ""),
        "audit_highlights": canonical_sections.get("audit_highlights", ""),
        "security_trust": canonical_sections.get("security_trust", ""),
        "ux_conversion": canonical_sections.get("ux_conversion", ""),
        "performance_tech": canonical_sections.get("performance_tech", ""),
        "google_business_profile": canonical_sections.get("google_business_profile", ""),
        "social_presence": canonical_sections.get("social_presence", ""),
        "sources": canonical_sections.get("sources", ""),
        "contact_information": canonical_sections.get("contact_information", ""),
        "outreach_section": canonical_sections.get("outreach_section", ""),
        "outreach_angle": canonical_sections.get("outreach_angle", ""),
        "website_audit": website_audit,
        "next_steps": canonical_sections.get("next_steps", ""),
        "evidence": canonical_sections.get("evidence", ""),
        "outreach_log_md": canonical_sections.get("outreach_log", ""),
    }


def canonicalize_sections(sections: dict[str, str]) -> dict[str, str]:
    canonical: dict[str, str] = {}
    for key, aliases in SECTION_ALIASES.items():
        values: list[str] = []
        seen: set[str] = set()

        for name in aliases:
            value = norm(sections.get(name))
            if value and value not in seen:
                values.append(value)
                seen.add(value)

        for name, body in sections.items():
            value = norm(body)
            if not value or value in seen:
                continue
            if any(pattern.match(name) for pattern in SECTION_PATTERNS.get(key, ())):
                values.append(value)
                seen.add(value)

        if values:
            canonical[key] = "\n\n".join(values).strip()
    return canonical


def is_substantive_section(value: str | None) -> bool:
    text = norm(value)
    if not text:
        return False
    first_lines = "\n".join(text.splitlines()[:4]).strip()
    if not first_lines:
        return False
    lowered = low(first_lines)
    if "pending review" in lowered and "pending research" in lowered:
        return False
    if "auto-restored from explicit worklist profile path" in lowered:
        return False
    return not any(pattern.match(first_lines) for pattern in LOW_INFORMATION_SECTION_PATTERNS)


def extract_website_audit(sections: dict[str, str]) -> str:
    chunks: list[str] = []
    for name, body in sections.items():
        clean_name = norm(name)
        if not clean_name or not norm(body):
            continue
        lower_name = clean_name.lower()
        if (
            lower_name.startswith("website audit")
            or lower_name.startswith("security headers")
            or lower_name.startswith("deep security audit")
            or lower_name.startswith("lighthouse")
        ):
            chunks.append(body.strip())
    # Some profiles keep domain-audit detail in Notes; preserve quick access if obvious.
    notes_body = sections.get("Notes", "")
    if "Domain Audit" in notes_body and norm(notes_body):
        chunks.append(notes_body.strip())
    return "\n\n".join(dict.fromkeys(chunks)).strip()


def read_text_if_exists(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def compact_nonempty_lines(text: str, *, limit: int = 8) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        clean = norm(raw_line.lstrip("\ufeff").strip())
        if not clean:
            continue
        if clean in seen:
            continue
        seen.add(clean)
        lines.append(clean)
        if len(lines) >= limit:
            break
    return "\n".join(lines).strip()


def unique_compact_lines(lines: Iterable[str], *, limit: int = 6) -> str:
    values: list[str] = []
    seen: set[str] = set()
    for line in lines:
        clean = clean_markdown_inline(line)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        values.append(clean)
        if len(values) >= limit:
            break
    return "\n".join(values).strip()


def summarize_deep_audit_markdown(text: str) -> str:
    clean = norm(text)
    if not clean:
        return ""
    lines = clean.splitlines()
    target_headers = {
        "Critical Issues": [],
        "High Issues": [],
        "Medium Issues": [],
        "Low Issues": [],
    }
    current_header = ""
    for raw_line in lines:
        line = norm(raw_line.lstrip("\ufeff"))
        if not line:
            continue
        if line.startswith("### "):
            header = line[4:].strip()
            current_header = ""
            for candidate in target_headers:
                if header.lower().startswith(candidate.lower()):
                    current_header = candidate
                    break
            continue
        if current_header and line.startswith("- "):
            target_headers[current_header].append(line[2:].strip())

    summary_lines: list[str] = []
    for header, items in target_headers.items():
        if not items:
            continue
        meaningful = [item for item in items if norm(item)]
        if not meaningful:
            continue
        if all(low(item) == "none detected" for item in meaningful):
            summary_lines.append(f"{header}: None detected")
            continue
        for item in meaningful[:3]:
            summary_lines.append(f"{header}: {item}")
    if summary_lines:
        return "\n".join(summary_lines).strip()
    return compact_nonempty_lines(clean, limit=10)


def extract_evidence_sidebars_from_raw(raw_markdown: str) -> str:
    lines = raw_markdown.splitlines()
    chunks: list[str] = []
    seen_blocks: set[str] = set()
    capture = False
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_lines
        block = "\n".join(line for line in current_lines if norm(line)).strip()
        if block and block not in seen_blocks:
            seen_blocks.add(block)
            chunks.append(block)
        current_lines = []

    for raw_line in lines:
        line = norm(raw_line)
        if re.match(r"^###\s+Evidence(?:\s+saved|\s*\([^)]+\))?\s*$", line, re.IGNORECASE):
            flush()
            capture = True
            continue
        if capture and line.startswith("#"):
            flush()
            capture = False
            continue
        if not capture:
            continue
        if not line:
            if current_lines:
                flush()
            continue
        lowered = low(line)
        if (
            "evidence/" in lowered
            or "evidence\\" in lowered
            or lowered.startswith("- screenshot:")
            or lowered.startswith("- lighthouse json:")
            or lowered.startswith("- headers:")
            or lowered.startswith("- **evidence:**")
        ):
            current_lines.append(line)
    flush()
    return "\n\n".join(chunks).strip()


def latest_matching_file(root: Path, patterns: Iterable[str]) -> Path | None:
    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(root.glob(pattern))
    if not matches:
        return None
    matches.sort(key=lambda item: (item.stat().st_mtime_ns, str(item).lower()), reverse=True)
    return matches[0]


def extract_evidence_profile_backfills(profile_path: Path) -> dict[str, str]:
    evidence_dir = profile_path.parent / "evidence"
    if not evidence_dir.exists():
        return {}

    backfills: dict[str, str] = {}

    deep_audit_file = latest_matching_file(evidence_dir, ("deep-audit-*.md",))
    if deep_audit_file:
        backfills["audit_highlights"] = summarize_deep_audit_markdown(read_text_if_exists(deep_audit_file))

    security_headers_file = latest_matching_file(evidence_dir, ("security-headers-summary.txt",))
    if security_headers_file:
        backfills["security_trust"] = compact_nonempty_lines(read_text_if_exists(security_headers_file), limit=8)

    form_findings_file = latest_matching_file(evidence_dir, ("form-findings.txt",))
    if form_findings_file:
        backfills["ux_conversion"] = compact_nonempty_lines(read_text_if_exists(form_findings_file), limit=8)

    return {key: value for key, value in backfills.items() if norm(value)}


def iter_narrative_section_lines(
    sections: dict[str, str],
    section_names: Iterable[str] = NARRATIVE_SECTION_NAMES,
) -> Iterable[str]:
    seen: set[str] = set()
    for section_name in section_names:
        body = norm(sections.get(section_name))
        if not body:
            continue
        for raw_line in body.splitlines():
            clean = norm(raw_line)
            if not clean or clean in seen:
                continue
            seen.add(clean)
            yield clean


def iter_matching_section_lines(
    sections: dict[str, str],
    section_patterns: Iterable[re.Pattern[str]],
) -> Iterable[str]:
    seen: set[str] = set()
    for name, body in sections.items():
        clean_name = norm(name)
        if not clean_name or not any(pattern.match(clean_name) for pattern in section_patterns):
            continue
        for raw_line in norm(body).splitlines():
            clean = clean_markdown_inline(raw_line)
            if not clean or clean in seen:
                continue
            seen.add(clean)
            yield clean


def parse_markdown_labeled_line(line: str) -> tuple[str, str]:
    text = clean_markdown_inline(line)
    text = re.sub(r"^[-*]\s*", "", text)
    match = MARKDOWN_LABEL_LINE_RE.match(text)
    if not match:
        return "", ""
    return low(match.group(1)), norm(match.group(2))


def normalize_extracted_address_candidate(value: str | None) -> str:
    text = clean_markdown_inline(value)
    if not text:
        return ""
    text = re.sub(
        r"\s+(?=(Phone|Email|Website|Contact form|Social media|NAICS|Distance|Decision maker|Hours|Pastor|Social|USDOT|License):)",
        "\n",
        text,
        flags=re.IGNORECASE,
    ).splitlines()[0].strip(" -|")
    return norm(text)


def is_usable_address_value(value: str | None) -> bool:
    text = normalize_extracted_address_candidate(value)
    lowered = low(text)
    if not text:
        return False
    if lowered in {"unknown", "not found", "n/a", "na", "none", "null"}:
        return False
    if lowered.startswith("unknown ") or lowered.startswith("not found"):
        return False
    return True


UNLABELED_ADDRESS_BULLET_RE = re.compile(
    r"""
    ^[-*]\s+
    (?P<address>
        \d{1,6}[\w .#-]*
        \b(?:Street|St|Road|Rd|Avenue|Ave|Drive|Dr|Lane|Ln|Boulevard|Blvd|Way|Court|Ct|Circle|Cir|Highway|Hwy|Parkway|Pkwy|Trail|Trl|Loop|FM|Fm)\b
        .*?,\s*
        [A-Za-z .'-]+,\s*
        [A-Z]{2}\s+\d{5}(?:-\d{4})?
    )
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def extract_unlabeled_address_bullet(line: str | None) -> str:
    text = norm(line)
    if not text:
        return ""
    match = UNLABELED_ADDRESS_BULLET_RE.match(text)
    if not match:
        return ""
    return normalize_extracted_address_candidate(match.group("address"))


def extract_address_from_profile(lines: list[str], kv: dict[str, str], sections: dict[str, str]) -> str:
    direct = normalize_extracted_address_candidate(kv.get("Address", ""))
    if is_usable_address_value(direct):
        return direct

    seen: set[str] = set()
    for line in lines:
        label, value = parse_markdown_labeled_line(line)
        if label != "address":
            continue
        candidate = normalize_extracted_address_candidate(value)
        if not is_usable_address_value(candidate):
            continue
        lowered = low(candidate)
        if lowered in seen:
            continue
        seen.add(lowered)
        return candidate

    contact_section = norm(sections.get("Contact Information"))
    if contact_section:
        for line in contact_section.splitlines():
            label, value = parse_markdown_labeled_line(line)
            if label != "address":
                continue
            candidate = normalize_extracted_address_candidate(value)
            if is_usable_address_value(candidate):
                return candidate

    for line in lines:
        candidate = extract_unlabeled_address_bullet(line)
        if is_usable_address_value(candidate):
            return candidate

    return ""


def is_placeholder_narrative_line(line: str | None) -> bool:
    text = norm(line)
    if not text:
        return True
    lowered = low(text)
    if "auto-restored from explicit worklist profile path" in lowered:
        return True
    if "pending review" in lowered or "pending research" in lowered:
        return True
    if "duplicate entity" in lowered or lowered.startswith("same as "):
        return True
    return False


def extract_contact_information_from_narrative(sections: dict[str, str]) -> str:
    contact_lines: list[str] = []
    for line in iter_narrative_section_lines(sections):
        if is_placeholder_narrative_line(line):
            continue
        fragments = [norm(part) for part in re.split(r"[;|]", line) if norm(part)]
        positive_fragments: list[str] = []
        for fragment in fragments or [line]:
            lowered = low(fragment)
            has_email = bool(EMAIL_RE.search(fragment))
            has_phone = bool(PHONE_RE.search(fragment))
            positive_contact_form = "contact form" in lowered and any(
                marker in lowered for marker in ("verified", "available", "exists", "exposes", "live")
            )
            if any(marker in lowered for marker in NEGATIVE_CONTACT_MARKERS):
                continue
            if "opencorporates.com" in lowered or "filing reference" in lowered or "filing-style reference" in lowered:
                continue
            if has_email or has_phone or positive_contact_form:
                positive_fragments.append(fragment)
        if positive_fragments:
            contact_lines.append(" - ".join(dict.fromkeys(positive_fragments)))
        if len(contact_lines) >= 6:
            break
    return "\n".join(contact_lines).strip()


def extract_next_steps_from_narrative(sections: dict[str, str]) -> str:
    action_lines: list[str] = []
    for line in iter_narrative_section_lines(sections, ("Notes", "Observations", "Research Notes")):
        if is_placeholder_narrative_line(line):
            continue
        match = ACTION_LINE_RE.match(line)
        if not match:
            continue
        action = norm(match.group(1))
        if not action or is_placeholder_narrative_line(action):
            continue
        action_lines.append(action)
        if len(action_lines) >= 4:
            break
    return "\n".join(action_lines).strip()


def extract_narrative_audit_backfills(sections: dict[str, str]) -> dict[str, str]:
    severity_lines: list[str] = []
    security_lines: list[str] = []
    ux_lines: list[str] = []

    for line in iter_narrative_section_lines(sections):
        if is_placeholder_narrative_line(line):
            continue
        lowered = low(line)

        severity_match = SEVERITY_LINE_RE.match(line)
        if severity_match:
            label = severity_match.group(1)
            detail = norm(severity_match.group(2))
            if detail and not is_placeholder_narrative_line(detail):
                formatted = f"{label}: {detail}"
                severity_lines.append(formatted)
                if any(keyword in lowered for keyword in SECURITY_TRUST_KEYWORDS):
                    security_lines.append(formatted)
                if any(keyword in lowered for keyword in UX_CONVERSION_KEYWORDS):
                    ux_lines.append(formatted)
            continue

        if any(keyword in lowered for keyword in SECURITY_TRUST_KEYWORDS):
            security_lines.append(line)
        if any(keyword in lowered for keyword in UX_CONVERSION_KEYWORDS):
            ux_lines.append(line)

    return {
        "audit_highlights": "\n".join(dict.fromkeys(severity_lines[:6])).strip(),
        "security_trust": "\n".join(dict.fromkeys(security_lines[:6])).strip(),
        "ux_conversion": "\n".join(dict.fromkeys(ux_lines[:6])).strip(),
    }


def extract_business_narrative_fields(sections: dict[str, str]) -> dict[str, str]:
    service_offerings: list[str] = []
    target_customers: list[str] = []
    differentiators: list[str] = []

    for line in iter_matching_section_lines(sections, BUSINESS_NARRATIVE_SECTION_PATTERNS):
        if is_placeholder_narrative_line(line):
            continue
        lowered = low(line)
        label, value = parse_markdown_labeled_line(line)
        target_label_handled = False
        differentiator_label_handled = False

        if label in SERVICE_OFFERING_LABELS and value:
            service_offerings.append(value)
        if label in TARGET_CUSTOMER_LABELS and value:
            target_customers.append(value)
            target_label_handled = True
        if label in DIFFERENTIATOR_LABELS and value:
            differentiators.append(value)
            differentiator_label_handled = True

        if not target_label_handled and any(keyword in lowered for keyword in TARGET_CUSTOMER_KEYWORDS):
            target_customers.append(value if label == "description" and value else line)
        if not differentiator_label_handled and any(keyword in lowered for keyword in DIFFERENTIATOR_KEYWORDS):
            differentiators.append(line)

    return {
        "service_offerings": unique_compact_lines(service_offerings, limit=5),
        "target_customers": unique_compact_lines(target_customers, limit=5),
        "differentiators": unique_compact_lines(differentiators, limit=5),
    }


def normalize_disqualification_line(line: str) -> str:
    text = clean_markdown_inline(line)
    if not text:
        return ""
    text = re.sub(
        r"^[-*]?\s*disqualified\s*\(\d{4}-\d{2}-\d{2}\):\s*",
        "Disqualified: ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"^[-*]?\s*\*\*\d{4}-\d{2}-\d{2}\*\*:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^[-*]?\s*\d{4}-\d{2}-\d{2}:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"^[-*]?\s*contact search completed on \d{4}-\d{2}-\d{2}:\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"^[-*]?\s*triage disqualification\s*\([^)]+\):\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"^[-*]?\s*no-contact exhausted disqualification:\s*\d{4}-\d{2}-\d{2}\.?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"moved to `?leads/disqualified/?`?.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\(as marked\)\.?\s*$", "", text, flags=re.IGNORECASE)
    text = text.strip(" -:|")
    return norm(text)


def extract_disqualification_rationale(kv: dict[str, str], sections: dict[str, str]) -> str:
    status_lc = low(kv.get("Status"))
    rationale_lines: list[str] = []

    for section_name in ("Exclusion Reason", "Duplicate Note", "Recommendation", "Disqualification"):
        body = norm(sections.get(section_name))
        if not is_substantive_section(body):
            continue
        for line in body.splitlines():
            clean = normalize_disqualification_line(line)
            if not clean or clean == "---":
                continue
            rationale_lines.append(clean)

    if status_lc in {"disqualified", "excluded"}:
        for section_name in ("Property LLC?", "Property LLC Check", "Chain with >3 locations?", "Chain Check", "Relationship to Lead"):
            body = norm(sections.get(section_name))
            if not is_substantive_section(body):
                continue
            for line in body.splitlines():
                clean = normalize_disqualification_line(line)
                lowered = low(clean)
                if not clean:
                    continue
                if lowered.startswith("no ") or lowered.startswith("no -") or lowered.startswith("no evidence"):
                    continue
                if any(marker in lowered for marker in POSITIVE_DISQUALIFICATION_MARKERS):
                    rationale_lines.append(clean)

        for section_name in ("Snapshot", "Observations", "Evidence"):
            body = norm(sections.get(section_name))
            if not is_substantive_section(body):
                continue
            for line in body.splitlines():
                clean = normalize_disqualification_line(line)
                lowered = low(clean)
                if not clean:
                    continue
                if lowered.startswith("disqualified:"):
                    rationale_lines.append(clean)
                    continue
                if (
                    "no verified official website, directory listing, official social, contact form, or phone-only contact found"
                    in lowered
                ):
                    rationale_lines.append(clean)

        notes_body = norm(sections.get("Notes"))
        if notes_body:
            for line in notes_body.splitlines():
                clean = normalize_disqualification_line(line)
                lowered = low(clean)
                if not clean:
                    continue
                if any(marker in lowered for marker in DISQUALIFICATION_NOTE_MARKERS):
                    rationale_lines.append(clean)
                    continue
                if "duplicate entity in worklist" in lowered:
                    rationale_lines.append(clean)
                    continue
                if lowered.startswith("same as ") and "leads/profiles/" in lowered:
                    rationale_lines.append(clean)

    return unique_compact_lines(rationale_lines, limit=8)


def extract_online_presence_from_disqualification_context(sections: dict[str, str]) -> str:
    online_presence_lines: list[str] = []
    found_conservative_rejection_markers: set[str] = set()

    for section_name in (
        "Online presence",
        "Online Presence",
        "Website presence",
        "Website Presence",
        "Snapshot",
        "Observations",
        "Notes",
        "Evidence",
        "Disqualification",
        "Disqualification Note",
    ):
        body = norm(sections.get(section_name))
        if not is_substantive_section(body):
            continue
        for line in body.splitlines():
            clean = normalize_disqualification_line(line)
            if not clean or is_placeholder_narrative_line(clean):
                continue
            for sentence in re.split(r"(?<=[.!?;])\s+", clean):
                candidate = norm(sentence)
                candidate = re.sub(r"^disqualified:\s*", "", candidate, flags=re.IGNORECASE)
                candidate = re.sub(r"\(checked \d{4}-\d{2}-\d{2}\)\.?$", "", candidate, flags=re.IGNORECASE)
                candidate = candidate.strip(" -.;")
                lowered = low(candidate)
                if not candidate:
                    continue
                for marker in CONSERVATIVE_MATCH_REJECTION_MARKERS:
                    if marker in lowered:
                        found_conservative_rejection_markers.add(marker)
                if any(marker in lowered for marker in ONLINE_PRESENCE_FACT_MARKERS):
                    online_presence_lines.append(candidate)

    if (
        not online_presence_lines
        and "no qualified candidate from web search results" in found_conservative_rejection_markers
        and "no conservative business match found during research" in found_conservative_rejection_markers
    ):
        online_presence_lines.append(
            "No qualified business match was confirmed during the conservative web research pass."
        )

    return unique_compact_lines(online_presence_lines, limit=4)


def extract_website_presence_from_disqualification_context(sections: dict[str, str]) -> str:
    website_presence_lines: list[str] = []

    for section_name in (
        "Website presence",
        "Website Presence",
        "Online presence",
        "Online Presence",
        "Snapshot",
        "Observations",
        "Notes",
        "Evidence",
        "Disqualification",
        "Disqualification Note",
    ):
        body = norm(sections.get(section_name))
        if not is_substantive_section(body):
            continue
        is_direct_disqualification_section = section_name in {"Disqualification", "Disqualification Note"}
        is_presence_section = section_name in {
            "Website presence",
            "Website Presence",
            "Online presence",
            "Online Presence",
        }
        for line in body.splitlines():
            clean = normalize_disqualification_line(line)
            if not clean or is_placeholder_narrative_line(clean):
                continue
            raw_line = norm(line)
            line_has_explicit_disqualification = "disqualified:" in low(raw_line)
            if not (is_direct_disqualification_section or is_presence_section or line_has_explicit_disqualification):
                continue
            for sentence in re.split(r"(?<=[.!?;])\s+", clean):
                candidate = norm(sentence)
                candidate = re.sub(r"^disqualified:\s*", "", candidate, flags=re.IGNORECASE)
                candidate = re.sub(r"\(checked \d{4}-\d{2}-\d{2}\)\.?$", "", candidate, flags=re.IGNORECASE)
                candidate = candidate.strip(" -.;")
                lowered = low(candidate)
                if not candidate:
                    continue
                if any(marker in lowered for marker in WEBSITE_PRESENCE_FACT_MARKERS):
                    website_presence_lines.append(candidate)

    return unique_compact_lines(website_presence_lines, limit=4)


def extract_lead_metadata_from_disqualification_context(sections: dict[str, str]) -> str:
    lead_metadata_lines: list[str] = []

    for section_name in ("Lead metadata", "Snapshot", "Observations", "Notes"):
        body = norm(sections.get(section_name))
        if not is_substantive_section(body):
            continue
        for line in body.splitlines():
            clean = normalize_disqualification_line(line)
            lowered = low(clean)
            if not clean or is_placeholder_narrative_line(clean):
                continue
            if any(marker in lowered for marker in LEAD_METADATA_FACT_MARKERS):
                lead_metadata_lines.append(clean)

    return unique_compact_lines(lead_metadata_lines, limit=3)


def parse_outreach_log_table(markdown: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not norm(markdown):
        return rows
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if stripped.startswith("| ---"):
            continue
        parts = [part.strip() for part in stripped.strip("|").split("|")]
        if len(parts) != 4:
            continue
        if parts[0].lower() == "date":
            continue
        if all(part.strip("- ") == "" for part in parts):
            continue
        rows.append(
            {
                "date": parts[0],
                "channel": parts[1],
                "status": parts[2],
                "notes": parts[3],
            }
        )
    return rows


def log_progress(message: str, started_at: float) -> None:
    elapsed = time.perf_counter() - started_at
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[leadops {stamp} +{elapsed:0.1f}s] {message}", flush=True)


def clean_lead_name_for_tokens(name: str) -> str:
    value = norm(name)
    value = re.sub(r"^\d+\s*[-_]\s*", "", value)
    value = value.replace("-", " ")
    value = value.replace("_", " ")
    return value


def tokenize_text(value: str | None) -> set[str]:
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", low(value))
        if len(token) >= 3 and token not in ENTITY_STOPWORDS and not token.isdigit()
    }
    return tokens


def domain_core(domain: str | None) -> str:
    host = normalize_domain(domain)
    if not host:
        return ""
    parts = [part for part in host.split(".") if part]
    if len(parts) >= 2:
        return parts[-2]
    return parts[0] if parts else ""


def domain_tokens(domain: str | None) -> set[str]:
    core = domain_core(domain)
    return tokenize_text(core)


def email_local_tokens(email: str | None) -> set[str]:
    value = normalize_email(email)
    if "@" not in value:
        return set()
    local = value.split("@", 1)[0]
    return tokenize_text(local.replace(".", " ").replace("_", " ").replace("-", " "))


def compact_entity_text(value: str | None) -> str:
    words = []
    for token in re.findall(r"[a-z0-9]+", low(value)):
        if token in ENTITY_STOPWORDS or token.isdigit():
            continue
        words.append(token)
    return "".join(words)


def summarize_entity_match(
    lead_tokens: set[str],
    website_tokens_set: set[str],
    email_domain_tokens_set: set[str],
    email_local_tokens_set: set[str],
    email_domain: str,
    website_domain: str,
    lead_compact: str,
    website_compact: str,
    email_domain_compact: str,
    email_local_compact: str,
) -> tuple[int, str, str]:
    score = 0
    reasons: list[str] = []
    generic_email = email_domain in GENERIC_EMAIL_DOMAINS
    website_overlap = lead_tokens & website_tokens_set
    email_domain_overlap = lead_tokens & email_domain_tokens_set
    email_local_overlap = lead_tokens & email_local_tokens_set

    if website_domain:
        score += 15
        reasons.append("has_website_domain")
    if email_domain:
        score += 10
        reasons.append("has_email_domain")
    if website_domain and email_domain and website_domain == email_domain and not generic_email:
        score += 30
        reasons.append("email_domain_matches_website")
    elif generic_email and website_domain:
        score += 8
        reasons.append("generic_email_with_business_website")

    if website_overlap:
        score += min(30, 12 * len(website_overlap))
        reasons.append("lead_name_matches_website")
    elif lead_compact and website_compact and (lead_compact == website_compact or lead_compact in website_compact or website_compact in lead_compact):
        score += 25
        reasons.append("lead_name_compact_matches_website")
    elif website_domain:
        score -= 18
        reasons.append("lead_name_mismatch_website")

    if email_domain_overlap and not generic_email:
        score += min(20, 10 * len(email_domain_overlap))
        reasons.append("lead_name_matches_email_domain")
    elif lead_compact and email_domain_compact and not generic_email and (
        lead_compact == email_domain_compact or lead_compact in email_domain_compact or email_domain_compact in lead_compact
    ):
        score += 18
        reasons.append("lead_name_compact_matches_email_domain")
    elif email_domain and not generic_email and email_domain != website_domain:
        score -= 12
        reasons.append("lead_name_mismatch_email_domain")

    if email_local_overlap:
        score += min(12, 4 * len(email_local_overlap))
        reasons.append("lead_name_matches_email_local")
    elif lead_compact and email_local_compact and (
        lead_compact == email_local_compact or lead_compact in email_local_compact or email_local_compact in lead_compact
    ):
        score += 8
        reasons.append("lead_name_compact_matches_email_local")

    if generic_email and not website_domain:
        score -= 20
        reasons.append("generic_email_without_website")

    score = max(0, min(score, 100))
    if score >= 75:
        bucket = "high"
    elif score >= 55:
        bucket = "medium"
    elif score >= 35:
        bucket = "low"
    else:
        bucket = "mismatch"
    return score, bucket, ", ".join(reasons)


def load_leads(index_csv: Path) -> list[LeadRow]:
    merged_rows: dict[int, dict[str, str]] = {}
    with index_csv.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            lead_id_raw = norm(row.get("LeadID"))
            if not lead_id_raw.isdigit():
                continue
            lead_id = int(lead_id_raw)
            clean_row = {k: norm(v) for k, v in row.items()}
            if lead_id in merged_rows:
                merged_rows[lead_id] = merge_csv_rows(merged_rows[lead_id], clean_row)
            else:
                merged_rows[lead_id] = clean_row

    leads: list[LeadRow] = []
    for lead_id in sorted(merged_rows):
        row = merged_rows[lead_id]
        email = normalize_email(row.get("Email"))
        website = norm(row.get("Website"))
        profile_path = norm(row.get("ProfilePath"))
        name = norm(row.get("Name"))
        if looks_like_batch_label(name):
            derived_name = derive_name_from_profile_path(profile_path)
            if derived_name:
                name = derived_name
        status = normalize_index_status(row.get("Status"), fallback=("disqualified" if low(row.get("Disqualified")) == "yes" else ""))
        outreach_status = normalize_index_outreach_status(row.get("OutreachStatus"))
        leads.append(
                LeadRow(
                    lead_id=lead_id,
                    name=name,
                    batch=norm(row.get("Batch")),
                    status=status,
                    outreach_status=outreach_status,
                    contact_path=norm(row.get("ContactPath")),
                    contact_search=norm(row.get("ContactSearch")),
                    email=email,
                    email_domain=email.split("@", 1)[1] if "@" in email else "",
                    phone=norm(row.get("Phone")),
                    website=website,
                    website_domain=normalize_domain(website),
                    contact_form=norm(row.get("ContactForm")),
                    social_media=norm(row.get("SocialMedia")),
                    website_status=norm(row.get("WebsiteStatus")),
                    social_checked=norm(row.get("SocialChecked")),
                    source=norm(row.get("Source")),
                    disqualified=1 if low(row.get("Disqualified")) == "yes" or low(row.get("Status")) == "disqualified" else 0,
                    updated=norm(row.get("Updated")),
                    profile_path=profile_path,
                    raw_index_json=json.dumps(row, ensure_ascii=True, sort_keys=True),
                )
            )
    return leads


def build_unique_email_map(leads: Iterable[LeadRow]) -> dict[str, int]:
    grouped: dict[str, set[int]] = {}
    for lead in leads:
        if lead.email:
            grouped.setdefault(lead.email, set()).add(lead.lead_id)
    return {email: next(iter(lead_ids)) for email, lead_ids in grouped.items() if len(lead_ids) == 1}


def build_unique_name_map(leads: Iterable[LeadRow]) -> dict[str, int]:
    grouped: dict[str, set[int]] = {}
    for lead in leads:
        if lead.name:
            grouped.setdefault(low(lead.name), set()).add(lead.lead_id)
    return {name: next(iter(lead_ids)) for name, lead_ids in grouped.items() if len(lead_ids) == 1}


def build_profile_file_map() -> dict[int, Path]:
    mapping: dict[int, Path] = {}
    for root in (REPO_ROOT / "leads" / "profiles", REPO_ROOT / "leads" / "disqualified"):
        if not root.exists():
            continue
        for path in root.rglob("profile.md"):
            match = LEAD_ID_PREFIX_RE.match(path.parent.name)
            if not match:
                continue
            lead_id = int(match.group(1))
            if lead_id not in mapping:
                mapping[lead_id] = path
    return mapping


def sha1_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()


def sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def profile_corpus_snapshot() -> dict[str, object]:
    entries: list[str] = []
    file_count = 0
    total_size = 0
    latest_mtime_ns = 0
    for root in (REPO_ROOT / "leads" / "profiles", REPO_ROOT / "leads" / "disqualified"):
        if not root.exists():
            continue
        for path in root.rglob("profile.md"):
            stat = path.stat()
            rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
            entries.append(f"{rel}|{stat.st_mtime_ns}|{stat.st_size}")
            file_count += 1
            total_size += int(stat.st_size)
            latest_mtime_ns = max(latest_mtime_ns, int(stat.st_mtime_ns))
    entries.sort()
    return {
        "fingerprint": sha1_text("\n".join(entries)),
        "size_bytes": total_size,
        "mtime": datetime.fromtimestamp(latest_mtime_ns / 1_000_000_000).isoformat(timespec="seconds") if latest_mtime_ns else "",
        "row_count": file_count,
    }


def snapshot_file_source(source_key: str, source_group: str, path: Path, fingerprint_kind: str = "sha1_file") -> dict[str, object]:
    exists_flag = int(path.exists())
    size_bytes = int(path.stat().st_size) if path.exists() else 0
    mtime = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds") if path.exists() else ""
    fingerprint = sha1_file(path) if path.exists() else ""
    return {
        "source_key": source_key,
        "source_group": source_group,
        "path": str(path.relative_to(REPO_ROOT)) if path.exists() or path.is_absolute() else str(path),
        "fingerprint_kind": fingerprint_kind,
        "exists_flag": exists_flag,
        "fingerprint": fingerprint,
        "size_bytes": size_bytes,
        "mtime": mtime,
        "row_count": 1 if exists_flag else 0,
    }


def collect_source_snapshots() -> list[dict[str, object]]:
    latest_mailbox_snapshot = find_latest_mailbox_snapshot()
    snapshots = [
        snapshot_file_source("index_csv", "core", INDEX_CSV),
        {
            "source_key": "profiles_corpus",
            "source_group": "core",
            "path": "leads/profiles/**/profile.md",
            "fingerprint_kind": "profile_corpus_stat_hash",
            "exists_flag": 1,
            **profile_corpus_snapshot(),
        },
        snapshot_file_source("contact_log_md", "outreach", CONTACT_LOG_MD),
        snapshot_file_source("opt_out_log_md", "outreach", OPT_OUT_LOG_MD),
        snapshot_file_source("sent_items_json", "mailbox", SENT_ITEMS_JSON),
        snapshot_file_source("delivered_items_json", "mailbox", DELIVERED_ITEMS_JSON),
        snapshot_file_source("drafts_json", "mailbox", DRAFTS_JSON),
        snapshot_file_source("drafts_revised_json", "mailbox", DRAFTS_REVISED_JSON),
        snapshot_file_source("missing_fields_md", "derived", MISSING_FIELDS_MD),
        snapshot_file_source("deep_audit_queue_json", "audit", DEEP_AUDIT_QUEUE_JSON),
        snapshot_file_source("bounce_suppression_json", "outreach", BOUNCE_SUPPRESSION_JSON),
        snapshot_file_source("all_bounced_emails_json", "outreach", ALL_BOUNCED_EMAILS_JSON),
        snapshot_file_source("bounced_emails_json", "outreach", BOUNCED_EMAILS_JSON),
        snapshot_file_source("bounce_followup_worklist_json", "outreach", BOUNCE_FOLLOWUP_WORKLIST_JSON),
        snapshot_file_source("dba_candidates_json", "overrides", DBA_CANDIDATES_JSON),
        snapshot_file_source("contact_path_snippets_txt", "overrides", CONTACT_PATH_SNIPPETS_TXT),
        snapshot_file_source("send_suppressions_json", "overrides", SEND_SUPPRESSIONS_JSON),
        snapshot_file_source("entity_aliases_json", "overrides", ENTITY_ALIASES_JSON),
        snapshot_file_source("entity_promotions_json", "overrides", ENTITY_PROMOTIONS_JSON),
        snapshot_file_source("review_decisions_csv", "overrides", REVIEW_DECISIONS_CSV),
    ]
    if latest_mailbox_snapshot:
        snapshots.append(snapshot_file_source("mailbox_snapshot_json", "mailbox", latest_mailbox_snapshot))
    else:
        snapshots.append(
            {
                "source_key": "mailbox_snapshot_json",
                "source_group": "mailbox",
                "path": "tmp/hostinger_mailbox_counts_*.json",
                "fingerprint_kind": "sha1_file",
                "exists_flag": 0,
                "fingerprint": "",
                "size_bytes": 0,
                "mtime": "",
                "row_count": 0,
            }
        )
    return snapshots


def load_prior_source_registry(conn: sqlite3.Connection) -> dict[str, dict[str, object]]:
    rows = {}
    for row in conn.execute(
        """
        SELECT source_key, source_group, path, fingerprint_kind, last_seen_exists,
               last_fingerprint, last_size_bytes, last_mtime, last_row_count, last_import_run_id
        FROM leadops_source_registry
        """
    ):
        rows[str(row[0])] = {
            "source_group": row[1],
            "path": row[2],
            "fingerprint_kind": row[3],
            "exists_flag": int(row[4] or 0),
            "fingerprint": norm(row[5]),
            "size_bytes": int(row[6] or 0),
            "mtime": norm(row[7]),
            "row_count": int(row[8] or 0),
            "last_import_run_id": row[9],
        }
    return rows


def compare_source_snapshots(
    current: list[dict[str, object]],
    prior: dict[str, dict[str, object]],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    comparisons: list[dict[str, object]] = []
    changed_keys: list[str] = []
    changed_groups: set[str] = set()
    for item in current:
        source_key = str(item["source_key"])
        previous = prior.get(source_key)
        if previous is None:
            change_status = "new"
            change_reason = "first_seen"
        elif int(previous["exists_flag"]) != int(item["exists_flag"]):
            change_status = "changed"
            change_reason = "exists_flag_changed"
        elif norm(previous["fingerprint"]) != norm(item["fingerprint"]):
            change_status = "changed"
            change_reason = "fingerprint_changed"
        else:
            change_status = "unchanged"
            change_reason = ""
        if change_status != "unchanged":
            changed_keys.append(source_key)
            changed_groups.add(str(item["source_group"]))
        comparisons.append({**item, "change_status": change_status, "change_reason": change_reason})
    summary = {
        "changed_source_keys": changed_keys,
        "changed_groups": sorted(changed_groups),
        "changed_count": len(changed_keys),
    }
    return comparisons, summary


def determine_refresh_mode(changed_groups: set[str], *, deep_index: bool) -> str:
    if deep_index:
        return "deep_index"
    if not changed_groups:
        return "noop"
    if changed_groups.issubset({"derived"}):
        return "derived_only"
    if changed_groups.issubset({"audit"}):
        return "audit_only"
    if changed_groups.issubset({"mailbox", "outreach"}):
        return "outreach_only"
    if changed_groups.issubset({"overrides"}):
        return "override_only"
    return "full"


def persist_source_registry_and_run_sources(
    conn: sqlite3.Connection,
    run_id: int,
    comparisons: list[dict[str, object]],
) -> None:
    if comparisons:
        conn.executemany(
            """
            INSERT INTO leadops_import_run_sources (
                run_id, source_key, path, exists_flag, fingerprint, size_bytes, mtime, row_count, change_status, change_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    row["source_key"],
                    row["path"],
                    row["exists_flag"],
                    row["fingerprint"],
                    row["size_bytes"],
                    row["mtime"],
                    row["row_count"],
                    row["change_status"],
                    row["change_reason"],
                )
                for row in comparisons
            ],
        )
        conn.executemany(
            """
            INSERT INTO leadops_source_registry (
                source_key, source_group, path, fingerprint_kind, last_seen_exists, last_fingerprint,
                last_size_bytes, last_mtime, last_row_count, last_import_run_id, last_changed_at, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET
                source_group=excluded.source_group,
                path=excluded.path,
                fingerprint_kind=excluded.fingerprint_kind,
                last_seen_exists=excluded.last_seen_exists,
                last_fingerprint=excluded.last_fingerprint,
                last_size_bytes=excluded.last_size_bytes,
                last_mtime=excluded.last_mtime,
                last_row_count=excluded.last_row_count,
                last_import_run_id=excluded.last_import_run_id,
                last_changed_at=excluded.last_changed_at,
                notes=excluded.notes
            """,
            [
                (
                    row["source_key"],
                    row["source_group"],
                    row["path"],
                    row["fingerprint_kind"],
                    row["exists_flag"],
                    row["fingerprint"],
                    row["size_bytes"],
                    row["mtime"],
                    row["row_count"],
                    run_id,
                    datetime.now().isoformat(timespec="seconds") if row["change_status"] != "unchanged" else "",
                    "",
                )
                for row in comparisons
            ],
        )


def parse_missing_fields_report(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith("#")
            or stripped.startswith("Generated:")
            or stripped.startswith("Total:")
        ):
            continue
        parts = [part.strip() for part in stripped.split("|")]
        if len(parts) != 4:
            continue
        lead_id_raw, name, missing_raw, path_raw = parts
        if not lead_id_raw.isdigit():
            continue
        missing_csv = missing_raw.removeprefix("missing:").strip()
        rel_path = path_raw.removeprefix("path:").strip()
        fields = [field.strip() for field in missing_csv.split(",") if field.strip()]
        rows.append(
            {
                "lead_id": int(lead_id_raw),
                "name": name,
                "missing_fields": fields,
                "path": rel_path,
            }
        )
    return rows


def parse_send_suppressions(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    rows = payload.get("suppressed_leads", [])
    if not isinstance(rows, list):
        return []
    parsed: list[dict[str, object]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        lead_id = item.get("lead_id")
        if not isinstance(lead_id, int):
            continue
        parsed.append(
            {
                "lead_id": lead_id,
                "reason": norm(str(item.get("reason", ""))),
            }
        )
    return parsed


def parse_entity_aliases(path: Path) -> dict[int, list[str]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    rows = payload.get("entity_aliases", [])
    if not isinstance(rows, list):
        return {}
    parsed: dict[int, list[str]] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        lead_id = item.get("lead_id")
        aliases = item.get("aliases", [])
        if not isinstance(lead_id, int) or not isinstance(aliases, list):
            continue
        clean_aliases = [norm(alias) for alias in aliases if norm(alias)]
        if clean_aliases:
            parsed[lead_id] = clean_aliases
    return parsed


def parse_entity_promotions(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    rows = payload.get("promoted_leads", [])
    if not isinstance(rows, list):
        return []
    parsed: list[dict[str, object]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        lead_id = item.get("lead_id")
        if not isinstance(lead_id, int):
            continue
        reason = norm(item.get("reason"))
        if reason:
            parsed.append({"lead_id": lead_id, "reason": reason})
    return parsed


def parse_review_decisions(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    parsed: list[dict[str, object]] = []
    with path.open(newline="", encoding="utf-8", errors="ignore") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            lead_id_raw = norm(row.get("lead_id"))
            if not lead_id_raw.isdigit():
                continue
            decision = low(row.get("decision"))
            reason = norm(row.get("reason"))
            source_file = norm(row.get("source_file"))
            if not decision:
                continue
            parsed.append(
                {
                    "lead_id": int(lead_id_raw),
                    "decision": decision,
                    "reason": reason,
                    "source_file": source_file,
                }
            )
    return parsed


def iter_diamond_audit_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.glob("diamond-audit-*.json"))


def normalize_issue_type(value: str | None) -> str:
    raw = low(value)
    if not raw or raw == "none - site functional":
        return "none"
    if "hijacked" in raw or "repurposed" in raw:
        return "domain_hijacked"
    if "wrong website" in raw or "directory mapping" in raw:
        return "wrong_website"
    if "email" in raw and ("corrupt" in raw or "bad" in raw or "invalid" in raw):
        return "bad_email"
    if "security" in raw:
        return "security_issue"
    if "performance" in raw:
        return "performance_issue"
    if "ux" in raw or "conversion" in raw:
        return "ux_issue"
    if "form" in raw:
        return "contact_form_issue"
    return re.sub(r"[^a-z0-9]+", "_", raw).strip("_") or "other"


def classify_finding_type(issue_type: str | None, diamond_worthy: object) -> str:
    normalized = normalize_issue_type(issue_type)
    if normalized in {"wrong_website", "bad_email"}:
        return "data_quality_issue"
    if bool(diamond_worthy):
        return "audit_issue"
    if normalized == "none":
        return "none"
    return "audit_issue"


def severity_from_issue(issue_type: str | None, diamond_worthy: object) -> str:
    normalized = normalize_issue_type(issue_type)
    if normalized == "none":
        return "none"
    if normalized == "domain_hijacked":
        return "high"
    if bool(diamond_worthy):
        return "medium"
    if normalized in {"wrong_website", "bad_email"}:
        return "low"
    return "medium"


def parse_diamond_audit_exports(root: Path) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    run_rows: list[dict[str, object]] = []
    finding_rows: list[dict[str, object]] = []
    for path in iter_diamond_audit_files(root):
        payload = load_json(path)
        if not isinstance(payload, dict):
            continue
        audit_info = payload.get("auditInfo", {}) if isinstance(payload.get("auditInfo"), dict) else {}
        summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
        run_rows.append(
            {
                "source_file": str(path.relative_to(REPO_ROOT)),
                "source_kind": "diamond_range_audit",
                "audit_date": parse_isoish_datetime(audit_info.get("auditDate")),
                "lead_range": norm(audit_info.get("range")),
                "criteria_raw": json.dumps(audit_info.get("criteria", []), ensure_ascii=True, sort_keys=True),
                "dedupe_list_path": norm(audit_info.get("dedupeList")),
                "summary_json": json.dumps(summary, ensure_ascii=True, sort_keys=True),
                "recommendation": norm(summary.get("recommendation")),
                "raw_payload": json.dumps(payload, ensure_ascii=True, sort_keys=True),
                "findings": [
                    ("diamond", item) for item in payload.get("diamondLeads", []) if isinstance(item, dict)
                ] + [
                    ("non_diamond", item) for item in payload.get("nonDiamondLeads", []) if isinstance(item, dict)
                ],
            }
        )
    return run_rows, finding_rows


def parse_deep_audit_queue(path: Path) -> list[dict[str, object]]:
    payload = load_json(path)
    if not isinstance(payload, list):
        return []
    rows: list[dict[str, object]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        lead_ref = norm(item.get("lead_id"))
        match = LEAD_ID_PREFIX_RE.match(lead_ref)
        lead_id = int(match.group(1)) if match else None
        if not lead_id:
            continue
        rows.append(
            {
                "lead_id": lead_id,
                "task_type": "needs_deep_audit",
                "status": "pending",
                "priority": "high",
                "source_file": str(path.relative_to(REPO_ROOT)),
                "website": norm(item.get("website")),
                "email": norm(item.get("email")),
                "task_range": norm(item.get("range")),
                "raw_payload": json.dumps(item, ensure_ascii=True, sort_keys=True),
            }
        )
    return rows


def parse_bounce_suppression(path: Path) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    payload = load_json(path)
    if not isinstance(payload, dict):
        return [], []
    recipients: list[dict[str, object]] = []
    domains: list[dict[str, object]] = []
    for item in payload.get("hard_suppress_recipients", []):
        if not isinstance(item, dict):
            continue
        recipients.append(
            {
                "target_type": "recipient",
                "target_value": normalize_email(item.get("email")),
                "action": norm(item.get("action")),
                "kind": norm(item.get("kind")),
                "reason": norm(item.get("reason")),
                "first_seen": parse_isoish_datetime(item.get("first_seen")),
                "last_seen": parse_isoish_datetime(item.get("last_seen")),
                "event_count": int(item.get("count") or 0),
                "reason_counts_json": "",
                "source_file": str(path.relative_to(REPO_ROOT)),
                "raw_payload": json.dumps(item, ensure_ascii=True, sort_keys=True),
            }
        )
    for item in payload.get("domain_caution", []):
        if not isinstance(item, dict):
            continue
        domains.append(
            {
                "target_type": "domain",
                "target_value": low(item.get("domain")),
                "action": norm(item.get("action")),
                "kind": "domain_caution",
                "reason": norm(item.get("reason")),
                "first_seen": "",
                "last_seen": "",
                "event_count": int(item.get("total_bounces") or 0),
                "reason_counts_json": json.dumps(item.get("reason_counts", {}), ensure_ascii=True, sort_keys=True),
                "source_file": str(path.relative_to(REPO_ROOT)),
                "raw_payload": json.dumps(item, ensure_ascii=True, sort_keys=True),
            }
        )
    return recipients, domains


def parse_bounced_email_logs(*paths: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in paths:
        payload = load_json(path)
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            recipient = normalize_email(item.get("recipient") or item.get("email"))
            if not recipient:
                continue
            rows.append(
                {
                    "recipient": recipient,
                    "subject": norm(item.get("subject")),
                    "event_at": parse_isoish_datetime(item.get("date") or item.get("when")),
                    "mailbox": norm(item.get("mailbox")),
                    "bounce_type": norm(item.get("bounce_type") or item.get("status")),
                    "smtp_status": norm(item.get("smtp_status")),
                    "source_file": str(path.relative_to(REPO_ROOT)),
                    "source_kind": path.name,
                    "raw_payload": json.dumps(item, ensure_ascii=True, sort_keys=True),
                }
            )
    return rows


def parse_draft_provenance(*paths: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in paths:
        payload = load_json(path)
        items = payload.get("value") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            continue
        variant = "revised" if path.name == "drafts-revised.json" else "batch_index"
        for item in items:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "uid": norm(item.get("uid")),
                    "recipient": normalize_email(item.get("to")),
                    "subject": norm(item.get("subject")),
                    "draft_date": parse_isoish_datetime(item.get("date")),
                    "from_addr": normalize_email(item.get("from")),
                    "href": norm(item.get("href")),
                    "body_text": norm(item.get("body")),
                    "body_source_text": norm(item.get("text")),
                    "draft_variant": variant,
                    "source_file": str(path.relative_to(REPO_ROOT)),
                    "raw_payload": json.dumps(item, ensure_ascii=True, sort_keys=True),
                }
            )
    return rows


def parse_bounce_followup_queue(path: Path) -> list[dict[str, object]]:
    payload = load_json(path)
    if not isinstance(payload, list):
        return []
    rows: list[dict[str, object]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        recipient = normalize_email(item.get("email"))
        rows.append(
            {
                "recipient": recipient,
                "company": norm(item.get("company")),
                "website": norm(item.get("website")),
                "bounce_date": parse_isoish_datetime(item.get("bounce_date")),
                "contact_form_url": norm(item.get("contact_form_url")),
                "status": norm(item.get("status")),
                "source_file": str(path.relative_to(REPO_ROOT)),
                "raw_payload": json.dumps(item, ensure_ascii=True, sort_keys=True),
            }
        )
    return rows


def parse_dba_candidates(path: Path) -> list[dict[str, object]]:
    payload = load_json(path)
    if not isinstance(payload, list):
        return []
    rows: list[dict[str, object]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        slug = norm(item.get("Slug"))
        match = LEAD_ID_PREFIX_RE.match(slug)
        lead_id = int(match.group(1)) if match else None
        if not lead_id:
            continue
        rows.append(
            {
                "lead_id": lead_id,
                "name": norm(item.get("Name")),
                "phone": norm(item.get("Phone")),
                "website": norm(item.get("Website")),
                "address": norm(item.get("Address")),
                "source_file": str(path.relative_to(REPO_ROOT)),
                "raw_payload": json.dumps(item, ensure_ascii=True, sort_keys=True),
            }
        )
    return rows


def parse_contact_path_snippets(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    blocks = re.split(r"\n===\s*", text)
    rows: list[dict[str, object]] = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        header = lines[0]
        match = re.match(r"(\d+)\s+-\s+(.+?)(?:\s*===)?$", header)
        if not match:
            continue
        lead_id = int(match.group(1))
        rows.append(
            {
                "lead_id": lead_id,
                "lines": lines[1:],
                "source_file": str(path.relative_to(REPO_ROOT)),
            }
        )
    return rows


def lead_id_from_path(path: Path) -> int | None:
    candidate = None
    for part in path.parts:
        match = LEAD_ID_PREFIX_RE.match(part)
        if match:
            candidate = int(match.group(1))
    return candidate


def classify_evidence_artifact(path: Path) -> tuple[str, str]:
    lower_name = path.name.lower()
    lower_ext = path.suffix.lower()
    if lower_name == "security-headers-summary.txt":
        return "security", "security_headers_summary"
    if lower_name == "headers.txt" or lower_name.endswith("-headers.txt"):
        return "security", "headers_capture"
    if lower_name == "dns-records.txt":
        return "security", "dns_records"
    if lower_name == "email-auth.txt":
        return "security", "email_auth"
    if lower_name == "tls-cert.json":
        return "security", "tls_certificate"
    if lower_name == "cookies-summary.txt":
        return "security", "cookies_summary"
    if lower_name == "mixed-content.txt":
        return "security", "mixed_content"
    if lower_name == "http-redirect.txt":
        return "security", "http_redirect"
    if lower_name == "probe-status.txt":
        return "security", "probe_status"
    if lower_name == "form-findings.txt":
        return "contact_path", "form_findings"
    if lower_name.startswith("console") and lower_ext == ".txt":
        return "runtime", "console_log"
    if lower_name.startswith("lighthouse") and lower_ext == ".json":
        return "performance", "lighthouse_report"
    if "deep-audit" in lower_name and lower_ext == ".md":
        return "audit", "deep_audit_note"
    if lower_ext in {".png", ".jpg", ".jpeg", ".webp"}:
        return "visual", "screenshot"
    if lower_ext == ".html":
        return "capture", "html_capture"
    if lower_ext == ".md":
        return "audit", "markdown_note"
    if lower_ext == ".json":
        return "data", "json_artifact"
    if lower_ext == ".txt":
        return "text", "text_artifact"
    return "other", "file_artifact"


def extract_text_for_search(path: Path, *, max_chars: int = 20000) -> str:
    if path.suffix.lower() not in {".txt", ".md", ".html", ".json"}:
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    if path.suffix.lower() == ".html":
        text = re.sub(r"(?is)<script.*?</script>", " ", text)
        text = re.sub(r"(?is)<style.*?</style>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = unescape(text)
    elif path.suffix.lower() == ".json":
        try:
            payload = json.loads(text)
            text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        except Exception:
            pass
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def iter_evidence_artifacts(root: Path) -> Iterable[dict[str, object]]:
    if not root.exists():
        return []
    rows: list[dict[str, object]] = []
    for evidence_dir in root.rglob("evidence"):
        if not evidence_dir.is_dir():
            continue
        lead_id = lead_id_from_path(evidence_dir)
        if not lead_id:
            continue
        for path in evidence_dir.rglob("*"):
            if not path.is_file():
                continue
            stat = path.stat()
            artifact_group, artifact_kind = classify_evidence_artifact(path)
            relative_path = path.relative_to(REPO_ROOT)
            mime_family = (mimetypes.guess_type(path.name)[0] or "").split("/", 1)[0]
            text_extract = extract_text_for_search(path)
            rows.append(
                {
                    "lead_id": lead_id,
                    "relative_path": str(relative_path),
                    "artifact_group": artifact_group,
                    "artifact_kind": artifact_kind,
                    "file_ext": path.suffix.lower(),
                    "mime_family": mime_family,
                    "size_bytes": int(stat.st_size),
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    "text_extract": text_extract,
                    "source_file": str(relative_path),
                    "raw_payload": json.dumps(
                        {
                            "name": path.name,
                            "relative_path": str(relative_path),
                            "artifact_group": artifact_group,
                            "artifact_kind": artifact_kind,
                        },
                        ensure_ascii=True,
                        sort_keys=True,
                    ),
                }
            )
    return rows


def iter_evidence_file_records(root: Path) -> Iterable[dict[str, object]]:
    if not root.exists():
        return []
    rows: list[dict[str, object]] = []
    for evidence_dir in root.rglob("evidence"):
        if not evidence_dir.is_dir():
            continue
        lead_id = lead_id_from_path(evidence_dir)
        if not lead_id:
            continue
        for path in evidence_dir.rglob("*"):
            if not path.is_file():
                continue
            stat = path.stat()
            artifact_group, artifact_kind = classify_evidence_artifact(path)
            relative_path = path.relative_to(REPO_ROOT)
            mime_family = (mimetypes.guess_type(path.name)[0] or "").split("/", 1)[0]
            rows.append(
                {
                    "lead_id": lead_id,
                    "path": path,
                    "relative_path": str(relative_path),
                    "artifact_group": artifact_group,
                    "artifact_kind": artifact_kind,
                    "file_ext": path.suffix.lower(),
                    "mime_family": mime_family,
                    "size_bytes": int(stat.st_size),
                    "file_mtime_ns": int(stat.st_mtime_ns),
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                }
            )
    return rows


def iter_all_evidence_file_records(roots: Iterable[Path]) -> Iterable[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for root in roots:
        rows.extend(iter_evidence_file_records(root))
    return rows


def derive_business_cluster_key(email: str | None, email_domain: str | None, website: str | None, website_domain: str | None) -> tuple[str, str]:
    site_domain = normalize_domain(website_domain or website)
    mail_domain = norm(email_domain)
    normalized_email = normalize_email(email)
    email_is_valid = bool(EMAIL_RE.fullmatch(normalized_email))
    if (
        site_domain
        and "(" not in site_domain
        and "directory" not in site_domain
        and "listing" not in site_domain
        and is_trustworthy_cluster_domain(site_domain)
    ):
        return ("website_domain", site_domain)
    if (
        email_is_valid
        and
        mail_domain
        and mail_domain not in GENERIC_EMAIL_DOMAINS
        and "(" not in mail_domain
        and is_trustworthy_cluster_domain(mail_domain)
    ):
        return ("email_domain", mail_domain)
    if email_is_valid:
        return ("email", normalized_email)
    return ("lead_id", "")


def choose_cluster_canonical_member(members: list[dict[str, object]]) -> int:
    def score(member: dict[str, object]) -> tuple[int, int, int, int, str]:
        status = low(member.get("status"))
        name = norm(member.get("name"))
        return (
            1 if status == "ready" else 0,
            1 if low(member.get("outreach_status")) == "uncontacted" else 0,
            0 if name.startswith("Lead Profile:") else 1,
            len(name),
            name,
        )

    best = max(members, key=score)
    return int(best["lead_id"])


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS leadops_import_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            source_summary_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS leadops_source_registry (
            source_key TEXT PRIMARY KEY,
            source_group TEXT NOT NULL,
            path TEXT NOT NULL,
            fingerprint_kind TEXT NOT NULL,
            last_seen_exists INTEGER NOT NULL DEFAULT 1,
            last_fingerprint TEXT,
            last_size_bytes INTEGER,
            last_mtime TEXT,
            last_row_count INTEGER,
            last_import_run_id INTEGER,
            last_changed_at TEXT,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS leadops_import_run_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            source_key TEXT NOT NULL,
            path TEXT NOT NULL,
            exists_flag INTEGER NOT NULL,
            fingerprint TEXT,
            size_bytes INTEGER,
            mtime TEXT,
            row_count INTEGER,
            change_status TEXT NOT NULL,
            change_reason TEXT,
            FOREIGN KEY (run_id) REFERENCES leadops_import_runs(id)
        );

        CREATE TABLE IF NOT EXISTS leadops_import_checkpoints (
            checkpoint_key TEXT PRIMARY KEY,
            checkpoint_value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS leadops_profile_import_state (
            lead_id INTEGER PRIMARY KEY,
            profile_path TEXT,
            file_mtime_ns INTEGER,
            file_size INTEGER,
            content_sha1 TEXT,
            parse_version TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            missing_profile INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_leads (
            lead_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            batch TEXT,
            status TEXT,
            index_status TEXT,
            outreach_status TEXT,
            index_outreach_status TEXT,
            reconciled_status_reason TEXT,
            last_outreach_event_at TEXT,
            last_outreach_channel TEXT,
            contact_path TEXT,
            contact_search TEXT,
            email TEXT,
            email_domain TEXT,
            phone TEXT,
            website TEXT,
            website_domain TEXT,
            contact_form TEXT,
            social_media TEXT,
            website_status TEXT,
            social_checked TEXT,
            source TEXT,
            disqualified INTEGER NOT NULL DEFAULT 0,
            updated TEXT,
            profile_path TEXT,
            raw_index_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS leadops_profiles (
            lead_id INTEGER PRIMARY KEY,
            title TEXT,
            address TEXT,
            naics TEXT,
            distance_miles REAL,
            decision_maker TEXT,
            last_updated TEXT,
            snapshot TEXT,
            observations TEXT,
            business_overview TEXT,
            service_offerings TEXT,
            target_customers TEXT,
            differentiators TEXT,
            contact_decision_makers TEXT,
            online_presence TEXT,
            market_position TEXT,
            opportunity_assessment TEXT,
            disqualification_rationale TEXT,
            lead_metadata TEXT,
            website_presence TEXT,
            audit_highlights TEXT,
            security_trust TEXT,
            ux_conversion TEXT,
            performance_tech TEXT,
            google_business_profile TEXT,
            social_presence TEXT,
            sources TEXT,
            contact_information TEXT,
            outreach_section TEXT,
            outreach_angle TEXT,
            website_audit TEXT,
            next_steps TEXT,
            evidence TEXT,
            outreach_log_md TEXT,
            raw_markdown TEXT NOT NULL,
            kv_json TEXT NOT NULL,
            sections_json TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            contact_type TEXT NOT NULL,
            value TEXT NOT NULL,
            normalized_value TEXT,
            label TEXT,
            is_primary INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_missing_fields (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            name TEXT,
            missing_fields TEXT NOT NULL,
            missing_field_count INTEGER NOT NULL DEFAULT 0,
            path TEXT,
            source TEXT NOT NULL,
            raw_payload TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_send_suppressions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            source TEXT NOT NULL,
            raw_payload TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_entity_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            alias TEXT NOT NULL,
            alias_tokens_json TEXT NOT NULL,
            alias_compact TEXT NOT NULL,
            source TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_entity_promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            source TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_entity_match (
            lead_id INTEGER PRIMARY KEY,
            match_score INTEGER NOT NULL DEFAULT 0,
            confidence_bucket TEXT NOT NULL,
            lead_tokens_json TEXT NOT NULL,
            website_tokens_json TEXT NOT NULL,
            email_domain_tokens_json TEXT NOT NULL,
            email_local_tokens_json TEXT NOT NULL,
            rationale TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_entity_clusters (
            cluster_id TEXT PRIMARY KEY,
            cluster_basis TEXT NOT NULL,
            cluster_key TEXT NOT NULL,
            canonical_lead_id INTEGER,
            member_count INTEGER NOT NULL DEFAULT 0,
            member_lead_ids_json TEXT NOT NULL,
            member_names_json TEXT NOT NULL,
            source TEXT NOT NULL,
            FOREIGN KEY (canonical_lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_entity_cluster_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cluster_id TEXT NOT NULL,
            lead_id INTEGER NOT NULL,
            is_canonical INTEGER NOT NULL DEFAULT 0,
            member_basis TEXT NOT NULL,
            source TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_review_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT,
            source_file TEXT,
            source TEXT NOT NULL,
            raw_payload TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_outreach_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            event_date TEXT,
            lead_name TEXT,
            batch TEXT,
            channel TEXT,
            status TEXT,
            subject TEXT,
            recipient TEXT,
            notes TEXT,
            source TEXT NOT NULL,
            raw_payload TEXT,
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_profile_outreach_log_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            event_date TEXT,
            channel TEXT,
            status TEXT,
            recipient TEXT,
            subject TEXT,
            notes TEXT,
            raw_payload TEXT,
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_drafts (
            uid TEXT PRIMARY KEY,
            lead_id INTEGER,
            recipient TEXT,
            subject TEXT,
            draft_date TEXT,
            mailbox_href TEXT,
            body_text TEXT,
            raw_json TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_opt_outs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            opt_out_date TEXT,
            lead_ref TEXT,
            recipient TEXT,
            reply_from TEXT,
            subject TEXT,
            notes TEXT,
            source TEXT NOT NULL,
            raw_payload TEXT,
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_mailbox_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_at TEXT NOT NULL,
            mailbox_name TEXT NOT NULL,
            item_count INTEGER NOT NULL,
            source TEXT NOT NULL,
            raw_payload TEXT
        );

        CREATE TABLE IF NOT EXISTS leadops_audit_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            audit_date TEXT,
            lead_range TEXT,
            criteria_raw TEXT,
            dedupe_list_path TEXT,
            summary_json TEXT,
            recommendation TEXT,
            raw_payload TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS leadops_audit_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            lead_id INTEGER,
            lead_name_snapshot TEXT,
            email_snapshot TEXT,
            website_snapshot TEXT,
            issue_type_raw TEXT,
            issue_type_norm TEXT,
            issue_description TEXT,
            diamond_worthy INTEGER NOT NULL DEFAULT 0,
            dedupe_status TEXT,
            dedupe_note TEXT,
            verified_live INTEGER,
            verification_method TEXT,
            evidence_path TEXT,
            note TEXT,
            finding_class TEXT,
            severity TEXT,
            source_file TEXT NOT NULL,
            raw_payload TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES leadops_audit_runs(id),
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_research_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            task_type TEXT NOT NULL,
            status TEXT NOT NULL,
            priority TEXT,
            source_file TEXT NOT NULL,
            website TEXT,
            email TEXT,
            batch_range TEXT,
            raw_payload TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_bounce_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            recipient TEXT NOT NULL,
            normalized_recipient TEXT NOT NULL,
            subject TEXT,
            event_at TEXT,
            mailbox TEXT,
            bounce_type TEXT,
            smtp_status TEXT,
            source_file TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            raw_payload TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_suppression_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_type TEXT NOT NULL,
            target_value TEXT NOT NULL,
            action TEXT,
            kind TEXT,
            reason TEXT,
            first_seen TEXT,
            last_seen TEXT,
            event_count INTEGER NOT NULL DEFAULT 0,
            reason_counts_json TEXT,
            source_file TEXT NOT NULL,
            raw_payload TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS leadops_draft_provenance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT,
            lead_id INTEGER,
            recipient TEXT,
            subject TEXT,
            draft_date TEXT,
            from_addr TEXT,
            href TEXT,
            body_text TEXT,
            body_source_text TEXT,
            draft_variant TEXT NOT NULL,
            source_file TEXT NOT NULL,
            raw_payload TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_bounce_followup_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient TEXT,
            company TEXT,
            website TEXT,
            bounce_date TEXT,
            contact_form_url TEXT,
            status TEXT,
            source_file TEXT NOT NULL,
            raw_payload TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS leadops_business_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            fact_type TEXT NOT NULL,
            fact_value TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_file TEXT NOT NULL,
            confidence TEXT,
            verified_at TEXT,
            raw_payload TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_evidence_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            relative_path TEXT NOT NULL,
            artifact_group TEXT NOT NULL,
            artifact_kind TEXT NOT NULL,
            file_ext TEXT,
            mime_family TEXT,
            size_bytes INTEGER,
            modified_at TEXT,
            text_extract TEXT,
            source_file TEXT NOT NULL,
            raw_payload TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_deep_index_file_state (
            relative_path TEXT PRIMARY KEY,
            lead_id INTEGER,
            file_mtime_ns INTEGER NOT NULL,
            file_size INTEGER NOT NULL,
            content_sha1 TEXT NOT NULL,
            parse_version TEXT NOT NULL,
            artifact_group TEXT NOT NULL,
            artifact_kind TEXT NOT NULL,
            last_indexed_at TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_entity_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            src_lead_id INTEGER NOT NULL,
            dst_lead_id INTEGER,
            edge_type TEXT NOT NULL,
            edge_value TEXT,
            confidence TEXT,
            source_kind TEXT NOT NULL,
            source_file TEXT NOT NULL,
            raw_payload TEXT NOT NULL,
            FOREIGN KEY (src_lead_id) REFERENCES leadops_leads(lead_id),
            FOREIGN KEY (dst_lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE TABLE IF NOT EXISTS leadops_search_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            doc_type TEXT NOT NULL,
            title TEXT,
            source_path TEXT NOT NULL,
            body_text TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS leadops_search_fts USING fts5(
            title,
            body_text,
            lead_id UNINDEXED,
            doc_type UNINDEXED,
            source_path UNINDEXED,
            source_kind UNINDEXED,
            tokenize='porter unicode61'
        );

        CREATE TABLE IF NOT EXISTS leadops_vector_index_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id INTEGER NOT NULL,
            lead_id INTEGER,
            doc_type TEXT NOT NULL,
            source_path TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            embedding_status TEXT NOT NULL DEFAULT 'pending',
            embedding_model TEXT NOT NULL DEFAULT '',
            embedded_at TEXT,
            FOREIGN KEY (doc_id) REFERENCES leadops_search_documents(id),
            FOREIGN KEY (lead_id) REFERENCES leadops_leads(lead_id)
        );

        CREATE INDEX IF NOT EXISTS idx_leadops_leads_outreach_status ON leadops_leads(outreach_status);
        CREATE INDEX IF NOT EXISTS idx_leadops_leads_status ON leadops_leads(status);
        CREATE INDEX IF NOT EXISTS idx_leadops_leads_disqualified ON leadops_leads(disqualified);
        CREATE INDEX IF NOT EXISTS idx_leadops_leads_email ON leadops_leads(email);
        CREATE INDEX IF NOT EXISTS idx_leadops_leads_website_domain ON leadops_leads(website_domain);
        CREATE INDEX IF NOT EXISTS idx_leadops_contacts_norm ON leadops_contacts(normalized_value);
        CREATE INDEX IF NOT EXISTS idx_leadops_drafts_recipient ON leadops_drafts(recipient);
        CREATE INDEX IF NOT EXISTS idx_leadops_drafts_lead_id ON leadops_drafts(lead_id);
        CREATE INDEX IF NOT EXISTS idx_leadops_events_lead_id ON leadops_outreach_events(lead_id);
        CREATE INDEX IF NOT EXISTS idx_leadops_events_recipient ON leadops_outreach_events(recipient);
        CREATE INDEX IF NOT EXISTS idx_leadops_events_date ON leadops_outreach_events(event_date);
        CREATE INDEX IF NOT EXISTS idx_leadops_profile_events_lead_id ON leadops_profile_outreach_log_events(lead_id);
        CREATE INDEX IF NOT EXISTS idx_leadops_opt_outs_lead_id ON leadops_opt_outs(lead_id);
        CREATE INDEX IF NOT EXISTS idx_leadops_opt_outs_recipient ON leadops_opt_outs(recipient);
        CREATE INDEX IF NOT EXISTS idx_leadops_mailbox_snapshots_mailbox ON leadops_mailbox_snapshots(mailbox_name, snapshot_at);
        CREATE INDEX IF NOT EXISTS idx_leadops_entity_aliases_lead_id ON leadops_entity_aliases(lead_id);
        CREATE INDEX IF NOT EXISTS idx_leadops_entity_promotions_lead_id ON leadops_entity_promotions(lead_id);
        CREATE INDEX IF NOT EXISTS idx_leadops_entity_match_bucket ON leadops_entity_match(confidence_bucket, match_score);
        CREATE INDEX IF NOT EXISTS idx_leadops_entity_cluster_members_lead_id ON leadops_entity_cluster_members(lead_id);
        CREATE INDEX IF NOT EXISTS idx_leadops_entity_cluster_members_cluster_id ON leadops_entity_cluster_members(cluster_id);
        CREATE INDEX IF NOT EXISTS idx_leadops_review_decisions_lead_id ON leadops_review_decisions(lead_id);
        CREATE INDEX IF NOT EXISTS idx_leadops_review_decisions_decision ON leadops_review_decisions(decision);
        CREATE INDEX IF NOT EXISTS idx_leadops_audit_findings_lead_id ON leadops_audit_findings(lead_id);
        CREATE INDEX IF NOT EXISTS idx_leadops_research_tasks_lead_id ON leadops_research_tasks(lead_id);
        CREATE INDEX IF NOT EXISTS idx_leadops_bounce_events_recipient ON leadops_bounce_events(normalized_recipient);
        CREATE INDEX IF NOT EXISTS idx_leadops_suppression_registry_target ON leadops_suppression_registry(target_type, target_value);
        CREATE INDEX IF NOT EXISTS idx_leadops_business_facts_lead_id ON leadops_business_facts(lead_id);
        CREATE INDEX IF NOT EXISTS idx_leadops_business_facts_type ON leadops_business_facts(fact_type);
        CREATE INDEX IF NOT EXISTS idx_leadops_evidence_artifacts_lead_id ON leadops_evidence_artifacts(lead_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_leadops_evidence_artifacts_relative_path ON leadops_evidence_artifacts(relative_path);
        CREATE INDEX IF NOT EXISTS idx_leadops_evidence_artifacts_kind ON leadops_evidence_artifacts(artifact_kind);
        CREATE INDEX IF NOT EXISTS idx_leadops_deep_index_file_state_lead_id ON leadops_deep_index_file_state(lead_id);
        CREATE INDEX IF NOT EXISTS idx_leadops_entity_edges_src ON leadops_entity_edges(src_lead_id);
        CREATE INDEX IF NOT EXISTS idx_leadops_entity_edges_dst ON leadops_entity_edges(dst_lead_id);
        CREATE INDEX IF NOT EXISTS idx_leadops_entity_edges_type ON leadops_entity_edges(edge_type);
        CREATE INDEX IF NOT EXISTS idx_leadops_search_documents_lead_id ON leadops_search_documents(lead_id);
        CREATE INDEX IF NOT EXISTS idx_leadops_search_documents_type ON leadops_search_documents(doc_type);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_leadops_search_documents_source_path_doc_type ON leadops_search_documents(source_path, doc_type);
        CREATE INDEX IF NOT EXISTS idx_leadops_vector_index_queue_status ON leadops_vector_index_queue(embedding_status);
        CREATE INDEX IF NOT EXISTS idx_leadops_vector_index_queue_model ON leadops_vector_index_queue(embedding_model);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_leadops_vector_index_queue_doc_model ON leadops_vector_index_queue(doc_id, embedding_model);
        CREATE INDEX IF NOT EXISTS idx_leadops_import_run_sources_run_id ON leadops_import_run_sources(run_id);
        CREATE INDEX IF NOT EXISTS idx_leadops_import_run_sources_source_key ON leadops_import_run_sources(source_key);

        DROP VIEW IF EXISTS leadops_v_latest_mailbox_snapshot;
        CREATE VIEW leadops_v_latest_mailbox_snapshot AS
        SELECT snapshot_at, mailbox_name, item_count, source
        FROM leadops_mailbox_snapshots
        WHERE snapshot_at = (SELECT MAX(snapshot_at) FROM leadops_mailbox_snapshots);

        DROP VIEW IF EXISTS leadops_v_last_successful_run;
        CREATE VIEW leadops_v_last_successful_run AS
        SELECT *
        FROM leadops_import_runs
        WHERE completed_at IS NOT NULL
        ORDER BY id DESC
        LIMIT 1;

        DROP VIEW IF EXISTS leadops_v_source_freshness;
        CREATE VIEW leadops_v_source_freshness AS
        SELECT
            sr.source_key,
            sr.source_group,
            sr.path,
            sr.fingerprint_kind,
            sr.last_seen_exists,
            sr.last_fingerprint,
            sr.last_size_bytes,
            sr.last_mtime,
            sr.last_row_count,
            sr.last_import_run_id,
            sr.last_changed_at
        FROM leadops_source_registry sr
        ORDER BY
            sr.source_group ASC,
            sr.source_key ASC;

        DROP VIEW IF EXISTS leadops_v_outbound_summary;
        CREATE VIEW leadops_v_outbound_summary AS
        SELECT
            (SELECT MAX(snapshot_at) FROM leadops_mailbox_snapshots) AS latest_snapshot_at,
            (SELECT item_count FROM leadops_v_latest_mailbox_snapshot WHERE mailbox_name = 'INBOX.Sent' LIMIT 1) AS raw_mailbox_sent_count,
            (SELECT item_count FROM leadops_v_latest_mailbox_snapshot WHERE mailbox_name = 'INBOX.Drafts' LIMIT 1) AS raw_mailbox_drafts_count,
            (SELECT COUNT(*) FROM leadops_outreach_events WHERE source = 'sent-items.json') AS deduped_sent_export_count,
            (SELECT COUNT(*) FROM leadops_drafts) AS deduped_drafts_export_count,
            (SELECT COUNT(*) FROM leadops_outreach_events WHERE source = 'contact-log.md' AND status = 'sent') AS contact_log_sent_count,
            (SELECT COUNT(*) FROM leadops_outreach_events WHERE source = 'delivered-emails.json') AS delivered_export_count,
            (SELECT COUNT(*) FROM leadops_opt_outs) AS opt_out_count;

        DROP VIEW IF EXISTS leadops_v_enrichment_summary;
        CREATE VIEW leadops_v_enrichment_summary AS
        SELECT
            l.lead_id,
            l.name,
            l.status,
            l.outreach_status,
            CASE
                WHEN p.lead_id IS NOT NULL THEN 1
                ELSE 0
            END AS is_profiled,
            CASE
                WHEN COALESCE(l.email, '') <> '' OR COALESCE(l.phone, '') <> '' OR COALESCE(l.contact_form, '') <> ''
                THEN 1
                ELSE 0
            END AS has_contact_method,
            CASE
                WHEN (
                    COALESCE(l.email, '') <> '' OR COALESCE(l.phone, '') <> '' OR COALESCE(l.contact_form, '') <> ''
                ) AND (
                    COALESCE(l.contact_search, '') <> '' OR
                    COALESCE(l.social_checked, '') <> '' OR
                    COALESCE(p.contact_information, '') <> '' OR
                    COALESCE(p.contact_decision_makers, '') <> ''
                )
                THEN 1
                ELSE 0
            END AS is_contact_verified,
            CASE WHEN p.lead_id IS NOT NULL THEN 1 ELSE 0 END AS has_profile,
            CASE WHEN COALESCE(p.snapshot, '') <> '' THEN 1 ELSE 0 END AS has_snapshot,
            CASE WHEN COALESCE(p.observations, '') <> '' THEN 1 ELSE 0 END AS has_observations,
            CASE WHEN COALESCE(p.business_overview, '') <> '' THEN 1 ELSE 0 END AS has_business_overview,
            CASE WHEN COALESCE(p.lead_metadata, '') <> '' THEN 1 ELSE 0 END AS has_lead_metadata,
            CASE WHEN COALESCE(p.website_presence, '') <> '' THEN 1 ELSE 0 END AS has_website_presence,
            CASE WHEN COALESCE(p.audit_highlights, '') <> '' THEN 1 ELSE 0 END AS has_audit_highlights,
            CASE WHEN COALESCE(p.security_trust, '') <> '' THEN 1 ELSE 0 END AS has_security_trust,
            CASE WHEN COALESCE(p.ux_conversion, '') <> '' THEN 1 ELSE 0 END AS has_ux_conversion,
            CASE WHEN COALESCE(p.performance_tech, '') <> '' THEN 1 ELSE 0 END AS has_performance_tech,
            CASE WHEN COALESCE(p.google_business_profile, '') <> '' THEN 1 ELSE 0 END AS has_google_business_profile,
            CASE WHEN COALESCE(p.online_presence, '') <> '' THEN 1 ELSE 0 END AS has_online_presence,
            CASE WHEN COALESCE(p.social_presence, '') <> '' THEN 1 ELSE 0 END AS has_social_presence,
            CASE WHEN COALESCE(p.opportunity_assessment, '') <> '' OR COALESCE(p.outreach_angle, '') <> '' THEN 1 ELSE 0 END AS has_opportunity_context,
            CASE WHEN COALESCE(p.website_audit, '') <> '' THEN 1 ELSE 0 END AS has_audit,
            CASE WHEN EXISTS (SELECT 1 FROM leadops_profile_outreach_log_events pe WHERE pe.lead_id = l.lead_id) THEN 1 ELSE 0 END AS has_profile_outreach_history,
            CASE
                WHEN lower(COALESCE(l.status, '')) IN ('research', 'ready', 'complete', 'draft-prepared', 'contacted', 'disqualified') THEN 1
                WHEN p.lead_id IS NOT NULL AND (
                    COALESCE(p.snapshot, '') <> '' OR
                    COALESCE(p.observations, '') <> '' OR
                    COALESCE(p.website_audit, '') <> ''
                ) THEN 1
                ELSE 0
            END AS is_researched,
            CASE
                WHEN COALESCE(p.website_audit, '') <> '' OR
                     COALESCE(p.audit_highlights, '') <> '' OR
                     COALESCE(p.security_trust, '') <> '' OR
                     COALESCE(p.ux_conversion, '') <> '' OR
                     COALESCE(p.performance_tech, '') <> ''
                THEN 1
                ELSE 0
            END AS is_audited,
            CASE
                WHEN l.disqualified = 0
                 AND COALESCE(l.email, '') <> ''
                 AND lower(COALESCE(l.outreach_status, '')) = 'uncontacted'
                 AND NOT EXISTS (
                    SELECT 1 FROM leadops_opt_outs o
                    WHERE o.lead_id = l.lead_id OR (COALESCE(o.recipient, '') <> '' AND o.recipient = l.email)
                 )
                 AND (
                    COALESCE(p.opportunity_assessment, '') <> '' OR
                    COALESCE(p.outreach_angle, '') <> '' OR
                    COALESCE(p.website_audit, '') <> '' OR
                    COALESCE(p.audit_highlights, '') <> ''
                 )
                THEN 1
                ELSE 0
            END AS is_outreach_ready
        FROM leadops_leads l
        LEFT JOIN leadops_profiles p ON p.lead_id = l.lead_id;

        DROP VIEW IF EXISTS leadops_v_outreach_reconciliation_summary;
        CREATE VIEW leadops_v_outreach_reconciliation_summary AS
        SELECT
            COALESCE(index_outreach_status, '') AS index_outreach_status,
            COALESCE(outreach_status, '') AS reconciled_outreach_status,
            COUNT(*) AS lead_count
        FROM leadops_leads
        GROUP BY COALESCE(index_outreach_status, ''), COALESCE(outreach_status, '');

        DROP VIEW IF EXISTS leadops_v_outreach_state;
        CREATE VIEW leadops_v_outreach_state AS
        WITH event_rollup AS (
            SELECT
                l.lead_id,
                COUNT(e.id) AS total_event_rows,
                COUNT(CASE WHEN lower(COALESCE(e.channel, '')) = 'email' THEN 1 END) AS email_event_rows,
                COUNT(CASE WHEN lower(COALESCE(e.channel, '')) = 'contact form' THEN 1 END) AS contact_form_event_rows,
                COUNT(CASE WHEN lower(COALESCE(e.channel, '')) = 'social dm' THEN 1 END) AS social_dm_event_rows,
                COUNT(CASE WHEN lower(COALESCE(e.channel, '')) = 'phone' THEN 1 END) AS phone_event_rows,
                COUNT(CASE WHEN lower(COALESCE(e.channel, '')) = 'email' AND lower(COALESCE(e.status, '')) IN ('sent', 'delivered') THEN 1 END) AS email_contacted_rows,
                COUNT(CASE WHEN lower(COALESCE(e.channel, '')) = 'email' AND lower(COALESCE(e.status, '')) = 'delivered' THEN 1 END) AS email_delivered_rows,
                COUNT(CASE WHEN lower(COALESCE(e.channel, '')) = 'email' AND lower(COALESCE(e.status, '')) = 'bounced' THEN 1 END) AS email_bounced_rows,
                COUNT(CASE WHEN lower(COALESCE(e.channel, '')) = 'email' AND lower(COALESCE(e.status, '')) = 'replied' THEN 1 END) AS email_replied_rows,
                COUNT(CASE WHEN lower(COALESCE(e.channel, '')) = 'contact form' AND lower(COALESCE(e.status, '')) IN ('sent', 'delivered', 'replied') THEN 1 END) AS contact_form_contacted_rows,
                COUNT(CASE WHEN lower(COALESCE(e.channel, '')) = 'social dm' AND lower(COALESCE(e.status, '')) IN ('sent', 'delivered', 'replied') THEN 1 END) AS social_dm_contacted_rows,
                COUNT(CASE WHEN lower(COALESCE(e.channel, '')) = 'phone' AND lower(COALESCE(e.status, '')) IN ('sent', 'delivered', 'replied', 'phone-only', 'attempted') THEN 1 END) AS phone_contacted_rows,
                MAX(CASE WHEN lower(COALESCE(e.channel, '')) = 'email' AND lower(COALESCE(e.status, '')) IN ('sent', 'delivered', 'replied', 'bounced', 'opt-out', 'opt_out') THEN COALESCE(e.event_date, '') ELSE '' END) AS last_email_event_at,
                MAX(CASE WHEN lower(COALESCE(e.channel, '')) = 'contact form' AND lower(COALESCE(e.status, '')) IN ('sent', 'delivered', 'replied') THEN COALESCE(e.event_date, '') ELSE '' END) AS last_contact_form_event_at,
                MAX(CASE WHEN lower(COALESCE(e.channel, '')) = 'social dm' AND lower(COALESCE(e.status, '')) IN ('sent', 'delivered', 'replied') THEN COALESCE(e.event_date, '') ELSE '' END) AS last_social_dm_event_at,
                MAX(CASE WHEN lower(COALESCE(e.channel, '')) = 'phone' AND lower(COALESCE(e.status, '')) IN ('sent', 'delivered', 'replied', 'phone-only', 'attempted') THEN COALESCE(e.event_date, '') ELSE '' END) AS last_phone_event_at
            FROM leadops_leads l
            LEFT JOIN leadops_outreach_events e
              ON e.lead_id = l.lead_id
            GROUP BY l.lead_id
        ),
        draft_rollup AS (
            SELECT
                lead_id,
                COUNT(*) AS draft_rows,
                MAX(COALESCE(draft_date, '')) AS last_draft_at
            FROM leadops_drafts
            WHERE lead_id IS NOT NULL
            GROUP BY lead_id
        ),
        opt_out_rollup AS (
            SELECT
                lead_id,
                COUNT(*) AS opt_out_rows,
                MAX(COALESCE(opt_out_date, '')) AS last_opt_out_at
            FROM leadops_opt_outs
            WHERE lead_id IS NOT NULL
            GROUP BY lead_id
        )
        SELECT
            l.lead_id,
            l.name,
            l.batch,
            l.status,
            l.index_outreach_status,
            l.outreach_status,
            l.reconciled_status_reason,
            l.last_outreach_event_at,
            l.last_outreach_channel,
            COALESCE(er.total_event_rows, 0) AS total_event_rows,
            COALESCE(er.email_event_rows, 0) AS email_event_rows,
            COALESCE(er.contact_form_event_rows, 0) AS contact_form_event_rows,
            COALESCE(er.social_dm_event_rows, 0) AS social_dm_event_rows,
            COALESCE(er.phone_event_rows, 0) AS phone_event_rows,
            COALESCE(dr.draft_rows, 0) AS draft_rows,
            COALESCE(oroll.opt_out_rows, 0) AS opt_out_rows,
            COALESCE(er.email_contacted_rows, 0) AS email_contacted_rows,
            COALESCE(er.email_delivered_rows, 0) AS email_delivered_rows,
            COALESCE(er.email_bounced_rows, 0) AS email_bounced_rows,
            COALESCE(er.email_replied_rows, 0) AS email_replied_rows,
            COALESCE(er.contact_form_contacted_rows, 0) AS contact_form_contacted_rows,
            COALESCE(er.social_dm_contacted_rows, 0) AS social_dm_contacted_rows,
            COALESCE(er.phone_contacted_rows, 0) AS phone_contacted_rows,
            CASE WHEN COALESCE(dr.draft_rows, 0) > 0 THEN 1 ELSE 0 END AS has_draft,
            CASE WHEN COALESCE(er.email_contacted_rows, 0) > 0 THEN 1 ELSE 0 END AS has_email_contact,
            CASE WHEN COALESCE(er.contact_form_contacted_rows, 0) > 0 THEN 1 ELSE 0 END AS has_contact_form_contact,
            CASE WHEN COALESCE(er.social_dm_contacted_rows, 0) > 0 THEN 1 ELSE 0 END AS has_social_dm_contact,
            CASE WHEN COALESCE(er.phone_contacted_rows, 0) > 0 THEN 1 ELSE 0 END AS has_phone_contact,
            CASE WHEN COALESCE(er.email_bounced_rows, 0) > 0 THEN 1 ELSE 0 END AS has_bounce,
            CASE WHEN COALESCE(er.email_replied_rows, 0) > 0 THEN 1 ELSE 0 END AS has_reply,
            CASE WHEN COALESCE(oroll.opt_out_rows, 0) > 0 THEN 1 ELSE 0 END AS has_opt_out,
            CASE
                WHEN COALESCE(oroll.last_opt_out_at, '') >= COALESCE(dr.last_draft_at, '')
                 AND COALESCE(oroll.last_opt_out_at, '') >= COALESCE(er.last_email_event_at, '')
                 AND COALESCE(oroll.last_opt_out_at, '') >= COALESCE(er.last_contact_form_event_at, '')
                 AND COALESCE(oroll.last_opt_out_at, '') >= COALESCE(er.last_social_dm_event_at, '')
                 AND COALESCE(oroll.last_opt_out_at, '') >= COALESCE(er.last_phone_event_at, '')
                THEN COALESCE(oroll.last_opt_out_at, '')
                WHEN COALESCE(dr.last_draft_at, '') >= COALESCE(er.last_email_event_at, '')
                 AND COALESCE(dr.last_draft_at, '') >= COALESCE(er.last_contact_form_event_at, '')
                 AND COALESCE(dr.last_draft_at, '') >= COALESCE(er.last_social_dm_event_at, '')
                 AND COALESCE(dr.last_draft_at, '') >= COALESCE(er.last_phone_event_at, '')
                THEN COALESCE(dr.last_draft_at, '')
                WHEN COALESCE(er.last_email_event_at, '') >= COALESCE(er.last_contact_form_event_at, '')
                 AND COALESCE(er.last_email_event_at, '') >= COALESCE(er.last_social_dm_event_at, '')
                 AND COALESCE(er.last_email_event_at, '') >= COALESCE(er.last_phone_event_at, '')
                THEN COALESCE(er.last_email_event_at, '')
                WHEN COALESCE(er.last_contact_form_event_at, '') >= COALESCE(er.last_social_dm_event_at, '')
                 AND COALESCE(er.last_contact_form_event_at, '') >= COALESCE(er.last_phone_event_at, '')
                THEN COALESCE(er.last_contact_form_event_at, '')
                WHEN COALESCE(er.last_social_dm_event_at, '') >= COALESCE(er.last_phone_event_at, '')
                THEN COALESCE(er.last_social_dm_event_at, '')
                ELSE COALESCE(er.last_phone_event_at, '')
            END AS last_contact_or_draft_at
        FROM leadops_leads l
        LEFT JOIN event_rollup er
          ON er.lead_id = l.lead_id
        LEFT JOIN draft_rollup dr
          ON dr.lead_id = l.lead_id
        LEFT JOIN opt_out_rollup oroll
          ON oroll.lead_id = l.lead_id;

        DROP VIEW IF EXISTS leadops_v_email_outreach_summary;
        CREATE VIEW leadops_v_email_outreach_summary AS
        SELECT
            COUNT(*) AS total_event_rows,
            COUNT(DISTINCT lead_id) AS distinct_leads,
            COUNT(DISTINCT lower(COALESCE(recipient, ''))) FILTER (WHERE COALESCE(recipient, '') <> '') AS distinct_recipients,
            COUNT(*) FILTER (WHERE source = 'sent-items.json') AS sent_export_rows,
            COUNT(*) FILTER (WHERE source = 'delivered-emails.json') AS delivered_export_rows,
            COUNT(*) FILTER (WHERE source = 'contact-log.md' AND lower(COALESCE(channel, '')) = 'email' AND lower(COALESCE(status, '')) = 'sent') AS contact_log_email_sent_rows,
            COUNT(*) FILTER (WHERE source = 'contact-log.md' AND lower(COALESCE(channel, '')) = 'contact form' AND lower(COALESCE(status, '')) = 'sent') AS contact_log_contact_form_sent_rows,
            COUNT(*) FILTER (WHERE lower(COALESCE(channel, '')) = 'email' AND lower(COALESCE(status, '')) = 'bounced') AS email_bounced_rows,
            COUNT(*) FILTER (WHERE lower(COALESCE(channel, '')) = 'email' AND lower(COALESCE(status, '')) = 'replied') AS email_replied_rows,
            COUNT(*) FILTER (WHERE lower(COALESCE(channel, '')) = 'email' AND lower(COALESCE(status, '')) IN ('sent', 'delivered')) AS broad_email_contact_rows
        FROM leadops_outreach_events;

        DROP VIEW IF EXISTS leadops_v_outreach_contact_state;
        CREATE VIEW leadops_v_outreach_contact_state AS
        SELECT
            os.lead_id,
            os.name,
            os.batch,
            os.status,
            os.index_outreach_status,
            os.outreach_status,
            os.reconciled_status_reason,
            os.has_draft,
            os.has_email_contact,
            os.has_contact_form_contact,
            os.has_social_dm_contact,
            os.has_phone_contact,
            os.has_bounce,
            os.has_reply,
            os.has_opt_out,
            os.email_contacted_rows,
            os.contact_form_contacted_rows,
            os.social_dm_contacted_rows,
            os.phone_contacted_rows,
            os.email_bounced_rows,
            os.email_replied_rows,
            os.opt_out_rows,
            os.draft_rows,
            CASE
                WHEN os.has_opt_out = 1 THEN 'opt-out'
                WHEN os.has_reply = 1 THEN 'replied'
                WHEN os.has_bounce = 1 THEN 'bounced'
                WHEN os.has_email_contact = 1 THEN 'contacted'
                WHEN os.has_draft = 1 THEN 'drafted'
                ELSE 'uncontacted'
            END AS email_lane_status,
            CASE
                WHEN os.contact_form_contacted_rows > 0 THEN 'contacted'
                ELSE 'uncontacted'
            END AS contact_form_status,
            CASE
                WHEN os.has_opt_out = 1 THEN 'opt-out'
                WHEN os.has_reply = 1 THEN 'replied'
                WHEN os.has_bounce = 1 THEN 'bounced'
                WHEN os.has_email_contact = 1
                  OR os.has_contact_form_contact = 1
                  OR os.has_social_dm_contact = 1
                  OR os.has_phone_contact = 1
                THEN 'contacted'
                WHEN os.has_draft = 1 THEN 'drafted'
                ELSE 'uncontacted'
            END AS overall_contact_state,
            CASE
                WHEN os.has_opt_out = 1 OR os.has_reply = 1 OR os.has_bounce = 1 THEN 0
                WHEN os.has_email_contact = 1
                  OR os.has_contact_form_contact = 1
                  OR os.has_social_dm_contact = 1
                  OR os.has_phone_contact = 1
                THEN 1
                ELSE 0
            END AS followup_eligible,
            TRIM(
                CASE WHEN os.has_email_contact = 1 THEN 'email; ' ELSE '' END ||
                CASE WHEN os.has_contact_form_contact = 1 THEN 'contact_form; ' ELSE '' END ||
                CASE WHEN os.has_social_dm_contact = 1 THEN 'social_dm; ' ELSE '' END ||
                CASE WHEN os.has_phone_contact = 1 THEN 'phone; ' ELSE '' END
            , '; ') AS contacted_channels
        FROM leadops_v_outreach_state os;

        DROP VIEW IF EXISTS leadops_v_outreach_contact_state_summary;
        CREATE VIEW leadops_v_outreach_contact_state_summary AS
        SELECT
            email_lane_status,
            contact_form_status,
            overall_contact_state,
            COUNT(*) AS lead_count,
            SUM(followup_eligible) AS followup_eligible_count
        FROM leadops_v_outreach_contact_state
        GROUP BY
            email_lane_status,
            contact_form_status,
            overall_contact_state
        ORDER BY
            lead_count DESC,
            overall_contact_state ASC,
            email_lane_status ASC,
            contact_form_status ASC;

        DROP VIEW IF EXISTS leadops_v_outreach_status_audit;
        CREATE VIEW leadops_v_outreach_status_audit AS
        SELECT
            os.lead_id,
            os.name,
            os.index_outreach_status,
            os.outreach_status,
            os.reconciled_status_reason,
            os.email_contacted_rows,
            os.contact_form_contacted_rows,
            os.social_dm_contacted_rows,
            os.phone_contacted_rows,
            os.email_bounced_rows,
            os.email_replied_rows,
            os.opt_out_rows,
            os.draft_rows,
            cs.email_lane_status,
            cs.contact_form_status,
            cs.overall_contact_state,
            cs.contacted_channels,
            CASE
                WHEN os.outreach_status = 'uncontacted'
                 AND (os.email_contacted_rows > 0 OR os.contact_form_contacted_rows > 0 OR os.social_dm_contacted_rows > 0 OR os.phone_contacted_rows > 0)
                THEN 'status_uncontacted_but_has_contact_events'
                WHEN os.outreach_status = 'drafted'
                 AND (os.email_contacted_rows > 0 OR os.contact_form_contacted_rows > 0)
                THEN 'status_drafted_but_has_contact_events'
                WHEN os.outreach_status = 'sent'
                 AND os.email_contacted_rows = 0
                 AND os.contact_form_contacted_rows = 0
                THEN 'status_sent_without_contact_event'
                WHEN os.outreach_status = 'bounced'
                 AND os.email_bounced_rows = 0
                THEN 'status_bounced_without_bounce_event'
                WHEN os.outreach_status = 'replied'
                 AND os.email_replied_rows = 0
                THEN 'status_replied_without_reply_event'
                WHEN os.outreach_status = 'opt-out'
                 AND os.opt_out_rows = 0
                THEN 'status_opt_out_without_opt_out_row'
                WHEN os.outreach_status <> cs.email_lane_status
                 AND os.outreach_status IN ('uncontacted', 'drafted', 'sent', 'bounced', 'replied', 'opt-out')
                THEN 'lead_outreach_status_differs_from_email_lane_state'
                ELSE NULL
            END AS audit_issue
        FROM leadops_v_outreach_state os
        JOIN leadops_v_outreach_contact_state cs
          ON cs.lead_id = os.lead_id
        WHERE audit_issue IS NOT NULL;

        DROP VIEW IF EXISTS leadops_v_sendable_next;
        CREATE VIEW leadops_v_sendable_next AS
        WITH sendable_base AS (
            SELECT
                l.lead_id,
                l.name,
                l.batch,
                l.status,
                l.index_status,
                l.outreach_status,
                l.index_outreach_status,
                l.contact_path,
                l.contact_search,
                l.email,
                l.email_domain,
                l.phone,
                l.website,
                l.website_domain,
                l.contact_form,
                l.source,
                l.updated,
                l.profile_path,
                l.reconciled_status_reason,
                l.last_outreach_event_at,
                l.last_outreach_channel,
                cs.email_lane_status,
                cs.contact_form_status,
                cs.overall_contact_state,
                cs.contacted_channels,
                p.decision_maker,
                p.snapshot,
                p.observations,
                p.outreach_angle,
                p.website_audit,
                e.is_contact_verified,
                e.is_audited,
                e.is_outreach_ready,
                CASE
                    WHEN COALESCE(p.audit_highlights, '') <> '' THEN 'audit_highlights'
                    WHEN COALESCE(p.website_audit, '') <> '' THEN 'website_audit'
                    WHEN COALESCE(p.outreach_angle, '') <> '' THEN 'outreach_angle'
                    WHEN COALESCE(p.opportunity_assessment, '') <> '' THEN 'opportunity_assessment'
                    ELSE 'profile_context'
                END AS primary_send_hook,
                CASE
                    WHEN e.is_audited = 1 AND e.is_contact_verified = 1 THEN 4
                    WHEN e.is_audited = 1 THEN 3
                    WHEN e.is_contact_verified = 1 THEN 2
                    ELSE 1
                END AS send_priority
            FROM leadops_leads l
            JOIN leadops_v_enrichment_summary e
              ON e.lead_id = l.lead_id
            LEFT JOIN leadops_v_outreach_contact_state cs
              ON cs.lead_id = l.lead_id
            LEFT JOIN leadops_profiles p
              ON p.lead_id = l.lead_id
            WHERE l.disqualified = 0
              AND COALESCE(cs.overall_contact_state, 'uncontacted') = 'uncontacted'
              AND COALESCE(l.email, '') <> ''
              AND lower(COALESCE(l.email, '')) NOT LIKE '%@domain.com'
              AND lower(COALESCE(l.email, '')) NOT LIKE '%@example.com'
              AND NOT EXISTS (
                  SELECT 1
                  FROM leadops_opt_outs o
                  WHERE o.lead_id = l.lead_id
                     OR (COALESCE(o.recipient, '') <> '' AND o.recipient = l.email)
              )
              AND e.is_outreach_ready = 1
        ),
        ranked_sendable AS (
            SELECT
                sb.*,
                COUNT(*) OVER (PARTITION BY sb.email) AS email_lead_count,
                ROW_NUMBER() OVER (
                    PARTITION BY sb.email
                    ORDER BY sb.send_priority DESC, COALESCE(sb.updated, '') DESC, sb.lead_id ASC
                ) AS email_rank
            FROM sendable_base sb
        )
        SELECT *
        FROM ranked_sendable
        WHERE email_rank = 1
        ORDER BY
            send_priority DESC,
            COALESCE(updated, '') DESC,
            lead_id ASC;

        DROP VIEW IF EXISTS leadops_v_sendable_next_strict;
        CREATE VIEW leadops_v_sendable_next_strict AS
        SELECT *
        FROM leadops_v_sendable_next
        WHERE email_lead_count = 1
          AND lower(COALESCE(email, '')) LIKE '%@%'
          AND COALESCE(email, '') NOT LIKE '%|%'
          AND lower(COALESCE(email, '')) NOT LIKE '%@raymondjames.com'
          AND lower(COALESCE(email, '')) NOT LIKE '%@hilton.com'
          AND lower(COALESCE(email, '')) NOT LIKE '%@ewingos.com'
          AND lower(COALESCE(email, '')) NOT LIKE '%@woccu.org'
          AND lower(COALESCE(email, '')) NOT LIKE '%@farmersagent.com';

        DROP VIEW IF EXISTS leadops_v_missing_profile_imports;
        CREATE VIEW leadops_v_missing_profile_imports AS
        SELECT
            l.lead_id,
            l.name,
            l.batch,
            l.status,
            l.outreach_status,
            l.email,
            l.website,
            l.updated,
            l.profile_path,
            CASE
                WHEN COALESCE(l.profile_path, '') = '' THEN 'missing_profile_path'
                WHEN COALESCE(l.profile_path, '') <> '' THEN 'profile_not_imported'
                ELSE 'unknown'
            END AS missing_profile_reason
        FROM leadops_leads l
        LEFT JOIN leadops_profiles p
          ON p.lead_id = l.lead_id
        WHERE p.lead_id IS NULL
        ORDER BY
            COALESCE(l.updated, '') DESC,
            l.lead_id ASC;

        DROP VIEW IF EXISTS leadops_v_needs_research;
        CREATE VIEW leadops_v_needs_research AS
        SELECT
            l.lead_id,
            l.name,
            l.batch,
            l.status,
            l.outreach_status,
            l.email,
            l.website,
            l.updated,
            mf.missing_fields,
            mf.missing_field_count,
            mf.path AS missing_fields_path,
            e.is_researched,
            e.is_audited,
            e.is_outreach_ready,
            CASE
                WHEN lower(COALESCE(l.status, '')) IN ('research', 'new', 'in-progress') THEN 'status_queue'
                WHEN COALESCE(mf.missing_field_count, 0) > 0 THEN 'missing_fields'
                WHEN COALESCE(e.is_researched, 0) = 1 AND COALESCE(e.is_outreach_ready, 0) = 0 THEN 'enrichment_gap'
                ELSE 'other'
            END AS research_reason
        FROM leadops_leads l
        LEFT JOIN leadops_missing_fields mf
          ON mf.lead_id = l.lead_id
        LEFT JOIN leadops_v_enrichment_summary e
          ON e.lead_id = l.lead_id
        WHERE l.disqualified = 0
          AND (
              lower(COALESCE(l.status, '')) IN ('research', 'new', 'in-progress')
              OR COALESCE(mf.missing_field_count, 0) > 0
              OR (COALESCE(e.is_researched, 0) = 1 AND COALESCE(e.is_outreach_ready, 0) = 0)
          )
        ORDER BY
            COALESCE(mf.missing_field_count, 0) DESC,
            COALESCE(l.updated, '') DESC,
            l.lead_id ASC;

        DROP VIEW IF EXISTS leadops_v_research_priority;
        CREATE VIEW leadops_v_research_priority AS
        SELECT
            l.lead_id,
            l.name,
            l.batch,
            l.status,
            l.outreach_status,
            l.email,
            l.website,
            l.updated,
            COALESCE(mf.missing_fields, '') AS missing_fields,
            COALESCE(mf.missing_field_count, 0) AS missing_field_count,
            e.is_contact_verified,
            e.is_audited,
            e.is_outreach_ready,
            CASE
                WHEN COALESCE(mf.missing_field_count, 0) > 0 THEN 'missing_fields'
                WHEN lower(COALESCE(l.status, '')) IN ('research', 'new', 'in-progress') THEN 'status_queue'
                WHEN COALESCE(e.is_contact_verified, 0) = 1
                 AND COALESCE(e.is_audited, 0) = 1
                 AND COALESCE(e.is_outreach_ready, 0) = 0 THEN 'near_outreach_ready'
                WHEN COALESCE(e.is_contact_verified, 0) = 1
                 AND COALESCE(e.is_outreach_ready, 0) = 0 THEN 'contact_verified_gap'
                ELSE 'general_enrichment'
            END AS priority_bucket,
            CASE
                WHEN COALESCE(mf.missing_field_count, 0) >= 4 THEN 100
                WHEN COALESCE(mf.missing_field_count, 0) = 3 THEN 95
                WHEN COALESCE(mf.missing_field_count, 0) = 2 THEN 90
                WHEN COALESCE(mf.missing_field_count, 0) = 1 THEN 85
                WHEN lower(COALESCE(l.status, '')) = 'research' THEN 80
                WHEN lower(COALESCE(l.status, '')) = 'new' THEN 75
                WHEN lower(COALESCE(l.status, '')) = 'in-progress' THEN 70
                WHEN COALESCE(e.is_contact_verified, 0) = 1 AND COALESCE(e.is_audited, 0) = 1 AND COALESCE(e.is_outreach_ready, 0) = 0 THEN 60
                WHEN COALESCE(e.is_contact_verified, 0) = 1 AND COALESCE(e.is_outreach_ready, 0) = 0 THEN 50
                ELSE 10
            END AS priority_score
        FROM leadops_leads l
        LEFT JOIN leadops_missing_fields mf
          ON mf.lead_id = l.lead_id
        LEFT JOIN leadops_v_enrichment_summary e
          ON e.lead_id = l.lead_id
        WHERE l.disqualified = 0
          AND (
              COALESCE(mf.missing_field_count, 0) > 0
              OR lower(COALESCE(l.status, '')) IN ('research', 'new', 'in-progress')
              OR (COALESCE(e.is_researched, 0) = 1 AND COALESCE(e.is_outreach_ready, 0) = 0)
          )
        ORDER BY
            priority_score DESC,
            COALESCE(mf.missing_field_count, 0) DESC,
            COALESCE(l.updated, '') DESC,
            l.lead_id ASC;

        DROP VIEW IF EXISTS leadops_v_business_operating_status;
        CREATE VIEW leadops_v_business_operating_status AS
        WITH base AS (
            SELECT
                l.lead_id,
                l.name,
                l.status,
                l.outreach_status,
                l.disqualified,
                lower(COALESCE(l.website_status, '')) AS website_status_lc,
                lower(COALESCE(p.raw_markdown, '')) AS raw_markdown_lc
            FROM leadops_leads l
            LEFT JOIN leadops_profiles p
              ON p.lead_id = l.lead_id
        )
        SELECT
            b.lead_id,
            CASE
                WHEN b.raw_markdown_lc LIKE '%permanently closed%'
                  OR b.raw_markdown_lc LIKE '%closed business%'
                  OR b.raw_markdown_lc LIKE '%business is closed%'
                  OR b.raw_markdown_lc LIKE '%ceased operations%'
                  OR b.website_status_lc LIKE '%permanently closed%'
                THEN 'permanently_closed'
                WHEN b.raw_markdown_lc LIKE '%temporarily closed%'
                  OR b.website_status_lc LIKE '%temporarily closed%'
                THEN 'temporarily_closed'
                ELSE 'active'
            END AS business_operating_status,
            CASE
                WHEN b.raw_markdown_lc LIKE '%permanently closed%'
                THEN 'profile_markdown: permanently closed'
                WHEN b.raw_markdown_lc LIKE '%closed business%'
                THEN 'profile_markdown: closed business'
                WHEN b.raw_markdown_lc LIKE '%business is closed%'
                THEN 'profile_markdown: business is closed'
                WHEN b.raw_markdown_lc LIKE '%ceased operations%'
                THEN 'profile_markdown: ceased operations'
                WHEN b.website_status_lc LIKE '%permanently closed%'
                THEN 'lead.website_status: permanently closed'
                WHEN b.raw_markdown_lc LIKE '%temporarily closed%'
                THEN 'profile_markdown: temporarily closed'
                WHEN b.website_status_lc LIKE '%temporarily closed%'
                THEN 'lead.website_status: temporarily closed'
                ELSE ''
            END AS business_operating_reason,
            CASE
                WHEN b.raw_markdown_lc LIKE '%permanently closed%'
                  OR b.raw_markdown_lc LIKE '%closed business%'
                  OR b.raw_markdown_lc LIKE '%business is closed%'
                  OR b.raw_markdown_lc LIKE '%temporarily closed%'
                THEN 'profile_markdown'
                WHEN b.website_status_lc LIKE '%closed%'
                THEN 'lead.website_status'
                ELSE ''
            END AS business_operating_source
        FROM base b;

        DROP VIEW IF EXISTS leadops_v_send_now;
        CREATE VIEW leadops_v_send_now AS
        SELECT
            s.lead_id,
            s.name,
            s.batch,
            s.status,
            s.outreach_status,
            s.email,
            s.email_domain,
            s.phone,
            s.website,
            s.website_domain,
            s.contact_form,
            s.primary_send_hook,
            s.send_priority,
            bos.business_operating_status,
            bos.business_operating_reason,
            bos.business_operating_source,
            COALESCE(ac.audience_family, 'commercial') AS audience_family,
            COALESCE(ac.audience_type, 'business') AS audience_type,
            COALESCE(ac.audience_subtype, 'general_business') AS audience_subtype,
            COALESCE(ac.outreach_voice, 'small_business_neighborly') AS outreach_voice,
            em.match_score AS entity_match_score,
            em.confidence_bucket AS entity_match_confidence,
            em.rationale AS entity_match_rationale,
            CASE WHEN ep.lead_id IS NOT NULL THEN 1 ELSE 0 END AS entity_match_promoted,
            COALESCE(ep.reason, '') AS entity_match_promotion_reason,
            s.reconciled_status_reason,
            s.last_outreach_event_at,
            CASE
                WHEN COALESCE(s.email, '') <> '' THEN 'draft_email'
                WHEN COALESCE(s.contact_form, '') <> '' THEN 'submit_contact_form'
                ELSE 'review_contact_path'
            END AS next_action
        FROM leadops_v_sendable_next_strict s
        JOIN leadops_entity_match em
          ON em.lead_id = s.lead_id
        LEFT JOIN leadops_v_business_operating_status bos
          ON bos.lead_id = s.lead_id
        LEFT JOIN leadops_v_audience_classification ac
          ON ac.lead_id = s.lead_id
        LEFT JOIN leadops_entity_promotions ep
          ON ep.lead_id = s.lead_id
        WHERE COALESCE(s.email, '') NOT LIKE '%;%'
          AND COALESCE(s.email, '') NOT LIKE '%,%'
          AND COALESCE(s.email, '') NOT LIKE '%(%'
          AND lower(COALESCE(s.email, '')) NOT LIKE '%to be verified%'
          AND lower(COALESCE(s.email, '')) NOT LIKE '%@exemplo.com%'
          AND lower(COALESCE(s.email, '')) NOT LIKE '%@washburn.edu%'
          AND lower(COALESCE(s.email, '')) NOT LIKE '%@spanishdict.com%'
          AND lower(COALESCE(s.email, '')) NOT LIKE '%@tegelerchevrolet.com%'
          AND lower(COALESCE(s.website, '')) NOT LIKE '%tiktok.com%'
          AND lower(COALESCE(s.website, '')) NOT LIKE '%facebook.com%'
          AND lower(COALESCE(s.website, '')) NOT LIKE '%instagram.com%'
          AND lower(COALESCE(s.website, '')) NOT LIKE '%linkedin.com%'
          AND lower(COALESCE(s.website, '')) NOT LIKE '%bbb.org%'
          AND lower(COALESCE(s.website, '')) NOT LIKE '%adrforum.com%'
          AND lower(COALESCE(s.website, '')) NOT LIKE '%psychologytoday.com%'
          AND lower(COALESCE(s.website, '')) NOT LIKE '%har.com/%'
          AND lower(COALESCE(s.website, '')) NOT LIKE '%autostoday.com%'
          AND lower(COALESCE(s.website, '')) NOT LIKE '%washburn.edu%'
          AND lower(COALESCE(s.website, '')) NOT LIKE '%spanishdict.com%'
          AND lower(COALESCE(s.website, '')) NOT LIKE '%unverified%'
          AND (
              COALESCE(s.website_domain, '') = ''
              OR COALESCE(s.email_domain, '') = COALESCE(s.website_domain, '')
              OR COALESCE(s.email_domain, '') IN (
                  'gmail.com', 'outlook.com', 'hotmail.com', 'yahoo.com', 'aol.com',
                  'icloud.com', 'me.com', 'mac.com', 'live.com', 'msn.com',
                  'protonmail.com', 'pm.me'
              )
          )
          AND NOT EXISTS (
              SELECT 1
              FROM leadops_send_suppressions ss
              WHERE ss.lead_id = s.lead_id
          )
          AND COALESCE(bos.business_operating_status, 'active') = 'active'
          AND COALESCE(ac.audience_type, 'business') <> 'big_brand'
          AND (
              em.confidence_bucket IN ('high', 'medium')
              OR ep.lead_id IS NOT NULL
          )
        ORDER BY
            s.send_priority DESC,
            COALESCE(s.updated, '') DESC,
            s.lead_id ASC;

        DROP VIEW IF EXISTS leadops_v_send_now_reviewed;
        CREATE VIEW leadops_v_send_now_reviewed AS
        SELECT
            s.*
        FROM leadops_v_send_now s
        WHERE COALESCE(s.website, '') <> ''
          AND lower(COALESCE(s.website, '')) NOT LIKE '%(%'
          AND lower(COALESCE(s.website, '')) NOT LIKE '%directory%'
          AND lower(COALESCE(s.website, '')) NOT LIKE '%listing only%'
          AND lower(COALESCE(s.website, '')) NOT LIKE '%facebook only%'
          AND (
              COALESCE(s.entity_match_confidence, '') = 'high'
              OR COALESCE(s.entity_match_promoted, 0) = 1
          )
          AND NOT EXISTS (
              SELECT 1
              FROM leadops_v_send_now_contact_variant_risk vr
              WHERE vr.lead_id = s.lead_id
          )
          AND (
              COALESCE(s.email_domain, '') = COALESCE(s.website_domain, '')
              OR (
                  COALESCE(s.email_domain, '') IN (
                      'gmail.com', 'outlook.com', 'hotmail.com', 'yahoo.com', 'aol.com',
                      'icloud.com', 'me.com', 'mac.com', 'live.com', 'msn.com',
                      'protonmail.com', 'pm.me'
                  )
                  AND COALESCE(s.website_domain, '') <> ''
              )
          )
        ORDER BY
            s.send_priority DESC,
            COALESCE(s.last_outreach_event_at, '') DESC,
            s.lead_id ASC;

        DROP VIEW IF EXISTS leadops_v_send_now_mailbox_safe;
        CREATE VIEW leadops_v_send_now_mailbox_safe AS
        SELECT
            s.*
        FROM leadops_v_send_now s
        WHERE NOT EXISTS (
                SELECT 1
                FROM leadops_drafts d
                WHERE lower(trim(COALESCE(d.recipient, ''))) = lower(trim(COALESCE(s.email, '')))
            )
          AND NOT EXISTS (
                SELECT 1
                FROM leadops_outreach_events e
                WHERE lower(trim(COALESCE(e.recipient, ''))) = lower(trim(COALESCE(s.email, '')))
                  AND lower(COALESCE(e.status, '')) IN ('sent', 'delivered', 'replied', 'opt-out', 'opt_out', 'bounced')
            )
          AND NOT EXISTS (
                SELECT 1
                FROM leadops_opt_outs o
                WHERE lower(trim(COALESCE(o.recipient, ''))) = lower(trim(COALESCE(s.email, '')))
                   OR (
                        COALESCE(o.reply_from, '') <> ''
                        AND lower(trim(COALESCE(o.reply_from, ''))) = lower(trim(COALESCE(s.email, '')))
                   )
            )
          AND NOT EXISTS (
                SELECT 1
                FROM leadops_v_send_now_contact_variant_risk vr
                WHERE vr.lead_id = s.lead_id
            )
          AND NOT EXISTS (
                SELECT 1
                FROM leadops_v_latest_review_decision rd
                WHERE rd.lead_id = s.lead_id
                  AND (
                        rd.decision = 'already_contacted'
                        OR rd.decision = 'keep_held'
                        OR rd.decision = 'keep_distinct_target'
                        OR rd.decision LIKE 'hold%'
                        OR rd.decision LIKE 'exclude%'
                  )
            )
        ORDER BY
            s.send_priority DESC,
            COALESCE(s.last_outreach_event_at, '') DESC,
            s.lead_id ASC;

        DROP VIEW IF EXISTS leadops_v_send_now_reviewed_mailbox_safe;
        CREATE VIEW leadops_v_send_now_reviewed_mailbox_safe AS
        SELECT
            s.*
        FROM leadops_v_send_now_reviewed s
        WHERE NOT EXISTS (
                SELECT 1
                FROM leadops_drafts d
                WHERE lower(trim(COALESCE(d.recipient, ''))) = lower(trim(COALESCE(s.email, '')))
            )
          AND NOT EXISTS (
                SELECT 1
                FROM leadops_outreach_events e
                WHERE lower(trim(COALESCE(e.recipient, ''))) = lower(trim(COALESCE(s.email, '')))
                  AND lower(COALESCE(e.status, '')) IN ('sent', 'delivered', 'replied', 'opt-out', 'opt_out', 'bounced')
            )
          AND NOT EXISTS (
                SELECT 1
                FROM leadops_opt_outs o
                WHERE lower(trim(COALESCE(o.recipient, ''))) = lower(trim(COALESCE(s.email, '')))
                   OR (
                        COALESCE(o.reply_from, '') <> ''
                        AND lower(trim(COALESCE(o.reply_from, ''))) = lower(trim(COALESCE(s.email, '')))
                   )
            )
          AND NOT EXISTS (
                SELECT 1
                FROM leadops_v_latest_review_decision rd
                WHERE rd.lead_id = s.lead_id
                  AND (
                        rd.decision = 'already_contacted'
                        OR rd.decision = 'keep_held'
                        OR rd.decision = 'keep_distinct_target'
                        OR rd.decision LIKE 'hold%'
                        OR rd.decision LIKE 'exclude%'
                  )
            )
        ORDER BY
            s.send_priority DESC,
            COALESCE(s.last_outreach_event_at, '') DESC,
            s.lead_id ASC;

        DROP VIEW IF EXISTS leadops_v_research_now;
        CREATE VIEW leadops_v_research_now AS
        WITH base_research AS (
            SELECT
                r.lead_id,
                r.name,
                r.batch,
                r.status,
                r.outreach_status,
                r.email,
                r.website,
                r.updated,
                r.priority_bucket,
                r.priority_score,
                r.missing_fields,
                r.missing_field_count,
                COALESCE(bos.business_operating_status, 'active') AS business_operating_status,
                COALESCE(bos.business_operating_reason, '') AS business_operating_reason,
                COALESCE(bos.business_operating_source, '') AS business_operating_source,
                COALESCE(ac.audience_family, 'commercial') AS audience_family,
                COALESCE(ac.audience_type, 'business') AS audience_type,
                COALESCE(ac.audience_subtype, 'general_business') AS audience_subtype,
                COALESCE(ac.outreach_voice, 'small_business_neighborly') AS outreach_voice,
                COALESCE(em.match_score, 0) AS entity_match_score,
                COALESCE(em.confidence_bucket, '') AS entity_match_confidence,
                CASE
                    WHEN instr(lower(COALESCE(r.missing_fields, '')), 'contact search') > 0 THEN 'needs_contact_search'
                    WHEN instr(lower(COALESCE(r.missing_fields, '')), 'outreach status') > 0 THEN 'needs_status_cleanup'
                    WHEN instr(lower(COALESCE(r.missing_fields, '')), 'batch') > 0 THEN 'needs_batch_metadata_cleanup'
                    WHEN COALESCE(r.email, '') = '' AND COALESCE(r.website, '') <> '' THEN 'needs_verified_email'
                    WHEN COALESCE(r.email, '') = '' AND COALESCE(r.website, '') = '' THEN 'needs_contact_path'
                    WHEN COALESCE(em.confidence_bucket, '') IN ('low', 'mismatch') AND COALESCE(r.email, '') <> '' AND COALESCE(r.website, '') <> '' THEN 'needs_entity_review'
                    WHEN r.priority_bucket = 'near_outreach_ready' THEN 'needs_audit_or_outreach_angle'
                    WHEN r.priority_bucket = 'contact_verified_gap' THEN 'needs_enrichment'
                    WHEN r.priority_bucket = 'status_queue' THEN 'needs_status_review'
                    ELSE 'needs_manual_review'
                END AS next_action
            FROM leadops_v_research_priority r
            LEFT JOIN leadops_entity_match em
              ON em.lead_id = r.lead_id
            LEFT JOIN leadops_v_business_operating_status bos
              ON bos.lead_id = r.lead_id
            LEFT JOIN leadops_v_audience_classification ac
              ON ac.lead_id = r.lead_id
            WHERE COALESCE(bos.business_operating_status, 'active') = 'active'
        ),
        entity_review AS (
            SELECT
                s.lead_id,
                s.name,
                s.batch,
                s.status,
                s.outreach_status,
                s.email,
                s.website,
                s.updated,
                'entity_review' AS priority_bucket,
                65 AS priority_score,
                '' AS missing_fields,
                0 AS missing_field_count,
                COALESCE(bos.business_operating_status, 'active') AS business_operating_status,
                COALESCE(bos.business_operating_reason, '') AS business_operating_reason,
                COALESCE(bos.business_operating_source, '') AS business_operating_source,
                COALESCE(ac.audience_family, 'commercial') AS audience_family,
                COALESCE(ac.audience_type, 'business') AS audience_type,
                COALESCE(ac.audience_subtype, 'general_business') AS audience_subtype,
                COALESCE(ac.outreach_voice, 'small_business_neighborly') AS outreach_voice,
                em.match_score AS entity_match_score,
                em.confidence_bucket AS entity_match_confidence,
                'needs_entity_review' AS next_action
            FROM leadops_v_sendable_next_strict s
            JOIN leadops_entity_match em
              ON em.lead_id = s.lead_id
            LEFT JOIN leadops_v_business_operating_status bos
              ON bos.lead_id = s.lead_id
            LEFT JOIN leadops_v_audience_classification ac
              ON ac.lead_id = s.lead_id
            LEFT JOIN leadops_entity_promotions ep
              ON ep.lead_id = s.lead_id
            WHERE em.confidence_bucket IN ('low', 'mismatch')
              AND COALESCE(bos.business_operating_status, 'active') = 'active'
              AND ep.lead_id IS NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM leadops_send_suppressions ss
                  WHERE ss.lead_id = s.lead_id
              )
        )
        SELECT *
        FROM (
            SELECT * FROM base_research
            UNION ALL
            SELECT er.*
            FROM entity_review er
            WHERE NOT EXISTS (
                SELECT 1
                FROM base_research br
                WHERE br.lead_id = er.lead_id
            )
        )
        ORDER BY
            priority_score DESC,
            COALESCE(updated, '') DESC,
            lead_id ASC;

        DROP VIEW IF EXISTS leadops_v_disqualification_reason_classification;
        CREATE VIEW leadops_v_disqualification_reason_classification AS
        SELECT
            l.lead_id,
            COALESCE(p.disqualification_rationale, '') AS reason_detail,
            CASE
                WHEN lower(COALESCE(p.disqualification_rationale, '')) LIKE '%marked as disqualified in source data.%'
                  AND lower(COALESCE(p.disqualification_rationale, '')) LIKE '%reason: review needed%'
                THEN 'review_needed_hold'
                WHEN lower(COALESCE(p.disqualification_rationale, '')) LIKE 'triage disqualification (%'
                  AND lower(COALESCE(p.disqualification_rationale, '')) LIKE '%no public presence%'
                  AND lower(COALESCE(p.disqualification_rationale, '')) LIKE '%not a fit%'
                  AND lower(COALESCE(p.disqualification_rationale, '')) LIKE '%duplicate%'
                THEN 'ambiguous_triage_hold'
                WHEN lower(COALESCE(p.disqualification_rationale, '')) LIKE '%no public presence%'
                  AND lower(COALESCE(p.disqualification_rationale, '')) LIKE '%not a fit%'
                  AND lower(COALESCE(p.disqualification_rationale, '')) LIKE '%duplicate%'
                THEN 'ambiguous_triage_hold'
                WHEN lower(COALESCE(p.disqualification_rationale, '')) LIKE '%closed business:%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%business appears defunct/closed%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%appears closed%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%defunct%'
                THEN 'closed_business'
                WHEN lower(COALESCE(p.disqualification_rationale, '')) LIKE '%not a montgomery county, tx business%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%location mismatch%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%appears to be maryland based%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%kansas city, ks%'
                THEN 'exclude_out_of_area'
                WHEN lower(COALESCE(p.disqualification_rationale, '')) LIKE '%user request%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%unclear identity%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%entity mismatch%'
                THEN 'identity_review_hold'
                WHEN lower(COALESCE(p.disqualification_rationale, '')) LIKE '%same location as%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%related entity%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%duplicate entity%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%duplicate of lead %'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%same as lead %'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%canonical: lead %'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%consolidate outreach with lead %'
                  OR lower(COALESCE(p.sections_json, '')) LIKE '%duplicate entity / redundant lead record%'
                THEN 'exclude_cluster'
                WHEN lower(COALESCE(p.disqualification_rationale, '')) LIKE '%property llc%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%property holding%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%property ownership%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%property rental%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%investment holding%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%holding company%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%non-operational holding/shell%'
                THEN 'exclude_property_holding'
                WHEN lower(COALESCE(p.disqualification_rationale, '')) LIKE '%fortune 500%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%publicly-traded%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%corporate account%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%enterprise level%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%global corporation%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%coca-cola%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%major corporate headquarters%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%girl scouts of san jacinto council property%'
                THEN 'exclude_big_brand'
                WHEN lower(COALESCE(p.disqualification_rationale, '')) LIKE '%chain%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%franchise%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%multiple locations%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%locations nationwide%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%locations across%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%national restaurant chain%'
                THEN 'exclude_chain'
                WHEN lower(COALESCE(p.disqualification_rationale, '')) LIKE '%phone-only after re-check%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%no reliable public non-phone contact path verified%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%no public email/phone/website/form/social found%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%no website, google business profile, social, or contact method found after sweeps%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%no verified official website, directory listing, official social, contact form, or phone-only contact found%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%no verified phone and no reliable outreach path%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%no valid phone or other reliable outreach path%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%verified official business website or direct contact path is confirmed%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%no-website queue%'
                  OR lower(COALESCE(p.raw_markdown, '')) LIKE '%no-contact exhausted disqualification%'
                THEN 'exclude_no_public_contact_path'
                WHEN lower(COALESCE(p.disqualification_rationale, '')) LIKE '%unable to verify business%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%can''t verify business exists%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%no online presence%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%no website found%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%no web presence found for numeric lead id%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%no web presence found for numeric entity or corresponding named entity%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%no discoverable business activity%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%no verified operating business found%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%no verified business operations found%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%no operating business found%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%no verifiable commercial presence%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%may no longer be in business%'
                THEN 'exclude_unverifiable_business'
                WHEN lower(COALESCE(p.disqualification_rationale, '')) LIKE '%wrong target%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%live site looked fully hardened%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%corporate/marketplace fit%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%brand-controlled%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%parent domain%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%independent local site%'
                  OR lower(COALESCE(p.disqualification_rationale, '')) LIKE '%financial profile%'
                THEN 'exclude_not_fit'
                ELSE NULL
            END AS reason_bucket,
            CASE
                WHEN trim(COALESCE(p.disqualification_rationale, '')) <> '' THEN 'profile_rationale'
                WHEN lower(COALESCE(p.raw_markdown, '')) LIKE '%no-contact exhausted disqualification%' THEN 'profile_notes'
                WHEN lower(COALESCE(p.sections_json, '')) LIKE '%phone-only after re-check%' THEN 'profile_disqualification_section'
                ELSE ''
            END AS reason_source
        FROM leadops_leads l
        LEFT JOIN leadops_profiles p
          ON p.lead_id = l.lead_id
        WHERE l.disqualified = 1;

        DROP VIEW IF EXISTS leadops_v_blank_disqualification_audit;
        CREATE VIEW leadops_v_blank_disqualification_audit AS
        WITH base AS (
            SELECT
                l.lead_id,
                l.name,
                l.batch,
                l.status,
                l.outreach_status,
                l.contact_path,
                l.contact_search,
                l.email,
                l.website,
                l.profile_path,
                COALESCE(bos.business_operating_status, 'active') AS business_operating_status,
                COALESCE(bos.business_operating_reason, '') AS business_operating_reason,
                COALESCE(lrd.decision, '') AS review_decision,
                COALESCE(lrd.reason, '') AS review_reason,
                COALESCE(ac.audience_family, 'commercial') AS audience_family,
                COALESCE(ac.audience_type, 'business') AS audience_type,
                COALESCE(ac.audience_subtype, 'general_business') AS audience_subtype,
                lower(COALESCE(p.raw_markdown, '')) AS raw_markdown_lc
            FROM leadops_leads l
            LEFT JOIN leadops_profiles p
              ON p.lead_id = l.lead_id
            LEFT JOIN leadops_v_business_operating_status bos
              ON bos.lead_id = l.lead_id
            LEFT JOIN leadops_v_latest_review_decision lrd
              ON lrd.lead_id = l.lead_id
            LEFT JOIN leadops_v_audience_classification ac
              ON ac.lead_id = l.lead_id
            WHERE l.disqualified = 1
              AND COALESCE(trim(p.disqualification_rationale), '') = ''
        )
        SELECT
            b.lead_id,
            b.name,
            b.batch,
            b.status,
            b.outreach_status,
            b.contact_path,
            b.contact_search,
            b.email,
            b.website,
            b.profile_path,
            b.business_operating_status,
            b.business_operating_reason,
            b.review_decision,
            b.review_reason,
            b.audience_family,
            b.audience_type,
            b.audience_subtype,
            CASE
                WHEN b.business_operating_status <> 'active' THEN 'justified_from_other_signal'
                WHEN lower(COALESCE(b.review_decision, '')) = 'exclude'
                  OR lower(COALESCE(b.review_decision, '')) LIKE 'exclude_%'
                THEN 'justified_from_other_signal'
                WHEN lower(COALESCE(b.review_decision, '')) = 'hold'
                  OR lower(COALESCE(b.review_decision, '')) LIKE 'hold_%'
                THEN 'justified_from_other_signal'
                WHEN b.audience_subtype = 'big_brand' THEN 'justified_from_other_signal'
                WHEN b.raw_markdown_lc LIKE '%synced from worklist%'
                  AND b.raw_markdown_lc LIKE '%skip -%'
                THEN 'justified_from_other_signal'
                WHEN lower(COALESCE(b.status, '')) = 'disqualified'
                  AND lower(COALESCE(b.profile_path, '')) LIKE 'leads/disqualified/%'
                  AND b.raw_markdown_lc LIKE '%registered-entities-excluded-skipped%'
                THEN 'justified_from_other_signal'
                WHEN b.raw_markdown_lc LIKE '%## snapshot%'
                  AND b.raw_markdown_lc LIKE '%disqualified:%'
                THEN 'recoverable_from_profile_text'
                ELSE 'underexplained_generic'
            END AS blank_disqualification_lane,
            CASE
                WHEN b.business_operating_status <> 'active' THEN 'business_operating_status'
                WHEN lower(COALESCE(b.review_decision, '')) = 'exclude'
                  OR lower(COALESCE(b.review_decision, '')) LIKE 'exclude_%'
                THEN 'review_decision_exclude'
                WHEN lower(COALESCE(b.review_decision, '')) = 'hold'
                  OR lower(COALESCE(b.review_decision, '')) LIKE 'hold_%'
                THEN 'review_decision_hold'
                WHEN b.audience_subtype = 'big_brand' THEN 'audience_big_brand'
                WHEN b.raw_markdown_lc LIKE '%synced from worklist%'
                  AND b.raw_markdown_lc LIKE '%skip -%'
                THEN 'profile_notes_worklist_skip'
                WHEN lower(COALESCE(b.status, '')) = 'disqualified'
                  AND lower(COALESCE(b.profile_path, '')) LIKE 'leads/disqualified/%'
                  AND b.raw_markdown_lc LIKE '%registered-entities-excluded-skipped%'
                THEN 'profile_registry_excluded_skipped'
                WHEN b.raw_markdown_lc LIKE '%## snapshot%'
                  AND b.raw_markdown_lc LIKE '%disqualified:%'
                THEN 'profile_snapshot_disqualified'
                ELSE 'no_strong_existing_signal'
            END AS blank_disqualification_basis,
            CASE WHEN b.raw_markdown_lc LIKE '%## snapshot%' THEN 1 ELSE 0 END AS has_snapshot_section,
            CASE WHEN b.raw_markdown_lc LIKE '%## snapshot%'
                      AND b.raw_markdown_lc LIKE '%disqualified:%' THEN 1 ELSE 0 END AS has_snapshot_disqualified_line,
            CASE WHEN b.raw_markdown_lc LIKE '%## notes%' THEN 1 ELSE 0 END AS has_notes_section,
            CASE WHEN b.raw_markdown_lc LIKE '%## evidence%' THEN 1 ELSE 0 END AS has_evidence_section,
            CASE WHEN b.raw_markdown_lc LIKE '%no website%'
                      OR b.raw_markdown_lc LIKE '%no public%'
                      OR b.raw_markdown_lc LIKE '%no verified%' THEN 1 ELSE 0 END AS has_no_public_signal,
            CASE WHEN b.raw_markdown_lc LIKE '%duplicate%' THEN 1 ELSE 0 END AS has_duplicate_signal,
            CASE WHEN b.raw_markdown_lc LIKE '%property%'
                      OR b.raw_markdown_lc LIKE '%holding%' THEN 1 ELSE 0 END AS has_property_signal,
            CASE WHEN b.raw_markdown_lc LIKE '%phone-only%' THEN 1 ELSE 0 END AS has_phone_only_signal
        FROM base b;

        DROP VIEW IF EXISTS leadops_v_blank_disqualification_measurement;
        CREATE VIEW leadops_v_blank_disqualification_measurement AS
        WITH cluster_context AS (
            SELECT
                ecm.lead_id,
                COUNT(DISTINCT ecs.cluster_id) AS duplicate_cluster_count,
                group_concat(DISTINCT ecs.cluster_basis) AS duplicate_cluster_bases,
                group_concat(DISTINCT ecs.cluster_key) AS duplicate_cluster_keys,
                MAX(
                    CASE
                        WHEN COALESCE(cl.disqualified, 0) = 0
                        THEN 1 ELSE 0
                    END
                ) AS has_nondisqualified_canonical,
                MAX(
                    CASE
                        WHEN COALESCE(cl.disqualified, 0) = 0
                         AND (
                                trim(COALESCE(cp.online_presence, '')) <> ''
                                OR trim(COALESCE(cp.website_presence, '')) <> ''
                                OR trim(COALESCE(cp.lead_metadata, '')) <> ''
                             )
                        THEN 1 ELSE 0
                    END
                ) AS has_nondisqualified_canonical_with_profile_facts
            FROM leadops_entity_cluster_members ecm
            JOIN leadops_entity_clusters ecs
              ON ecs.cluster_id = ecm.cluster_id
             AND ecs.member_count > 1
            JOIN leadops_leads cl
              ON cl.lead_id = ecs.canonical_lead_id
            LEFT JOIN leadops_profiles cp
              ON cp.lead_id = ecs.canonical_lead_id
            GROUP BY
                ecm.lead_id
        )
        SELECT
            b.lead_id,
            b.name,
            b.batch,
            b.status,
            b.outreach_status,
            b.profile_path,
            b.blank_disqualification_lane,
            b.blank_disqualification_basis,
            b.business_operating_status,
            b.business_operating_reason,
            b.review_decision,
            b.review_reason,
            b.audience_family,
            b.audience_type,
            b.audience_subtype,
            CASE WHEN trim(COALESCE(p.online_presence, '')) <> '' THEN 1 ELSE 0 END AS has_online_presence_representation,
            CASE WHEN trim(COALESCE(p.website_presence, '')) <> '' THEN 1 ELSE 0 END AS has_website_presence_representation,
            CASE WHEN trim(COALESCE(p.lead_metadata, '')) <> '' THEN 1 ELSE 0 END AS has_lead_metadata_representation,
            CASE WHEN COALESCE(b.business_operating_status, 'active') <> 'active' THEN 1 ELSE 0 END AS has_nonactive_operating_status_representation,
            CASE
                WHEN trim(COALESCE(b.review_decision, '')) <> ''
                 AND (
                        lower(COALESCE(b.review_decision, '')) = 'exclude'
                        OR lower(COALESCE(b.review_decision, '')) LIKE 'exclude_%'
                        OR lower(COALESCE(b.review_decision, '')) = 'hold'
                        OR lower(COALESCE(b.review_decision, '')) LIKE 'hold_%'
                     )
                THEN 1 ELSE 0
            END AS has_review_decision_representation,
            (
                CASE WHEN trim(COALESCE(p.online_presence, '')) <> '' THEN 1 ELSE 0 END
                + CASE WHEN trim(COALESCE(p.website_presence, '')) <> '' THEN 1 ELSE 0 END
                + CASE WHEN trim(COALESCE(p.lead_metadata, '')) <> '' THEN 1 ELSE 0 END
                + CASE WHEN COALESCE(b.business_operating_status, 'active') <> 'active' THEN 1 ELSE 0 END
                + CASE
                    WHEN COALESCE(b.blank_disqualification_basis, '') = 'profile_registry_excluded_skipped'
                    THEN 1 ELSE 0
                  END
                + CASE
                    WHEN trim(COALESCE(b.review_decision, '')) <> ''
                     AND (
                            lower(COALESCE(b.review_decision, '')) = 'exclude'
                            OR lower(COALESCE(b.review_decision, '')) LIKE 'exclude_%'
                            OR lower(COALESCE(b.review_decision, '')) = 'hold'
                            OR lower(COALESCE(b.review_decision, '')) LIKE 'hold_%'
                         )
                    THEN 1 ELSE 0
                  END
            ) AS normalized_representation_surface_count,
            trim(
                replace(
                    replace(
                        CASE WHEN trim(COALESCE(p.online_presence, '')) <> '' THEN ',online_presence' ELSE '' END
                        || CASE WHEN trim(COALESCE(p.website_presence, '')) <> '' THEN ',website_presence' ELSE '' END
                        || CASE WHEN trim(COALESCE(p.lead_metadata, '')) <> '' THEN ',lead_metadata' ELSE '' END
                        || CASE WHEN COALESCE(b.business_operating_status, 'active') <> 'active' THEN ',operating_status' ELSE '' END
                        || CASE
                            WHEN COALESCE(b.blank_disqualification_basis, '') = 'profile_registry_excluded_skipped'
                            THEN ',profile_disqualification_signal' ELSE ''
                           END
                        || CASE
                            WHEN trim(COALESCE(b.review_decision, '')) <> ''
                             AND (
                                    lower(COALESCE(b.review_decision, '')) = 'exclude'
                                    OR lower(COALESCE(b.review_decision, '')) LIKE 'exclude_%'
                                    OR lower(COALESCE(b.review_decision, '')) = 'hold'
                                    OR lower(COALESCE(b.review_decision, '')) LIKE 'hold_%'
                                 )
                            THEN ',review_decision' ELSE ''
                           END,
                        ',,',
                        ','
                    ),
                    ',,',
                    ','
                ),
                ','
            ) AS normalized_representation_sources,
            CASE
                WHEN (
                    CASE WHEN trim(COALESCE(p.online_presence, '')) <> '' THEN 1 ELSE 0 END
                    + CASE WHEN trim(COALESCE(p.website_presence, '')) <> '' THEN 1 ELSE 0 END
                    + CASE WHEN trim(COALESCE(p.lead_metadata, '')) <> '' THEN 1 ELSE 0 END
                    + CASE WHEN COALESCE(b.business_operating_status, 'active') <> 'active' THEN 1 ELSE 0 END
                    + CASE
                        WHEN COALESCE(b.blank_disqualification_basis, '') = 'profile_registry_excluded_skipped'
                        THEN 1 ELSE 0
                      END
                    + CASE
                        WHEN trim(COALESCE(b.review_decision, '')) <> ''
                         AND (
                                lower(COALESCE(b.review_decision, '')) = 'exclude'
                                OR lower(COALESCE(b.review_decision, '')) LIKE 'exclude_%'
                                OR lower(COALESCE(b.review_decision, '')) = 'hold'
                                OR lower(COALESCE(b.review_decision, '')) LIKE 'hold_%'
                             )
                        THEN 1 ELSE 0
                      END
                ) > 0
                THEN 'represented_in_normalized_surface'
                ELSE 'unrepresented_in_normalized_surface'
            END AS normalized_representation_lane,
            CASE WHEN COALESCE(cc.duplicate_cluster_count, 0) > 0 THEN 1 ELSE 0 END AS in_duplicate_cluster,
            COALESCE(cc.duplicate_cluster_count, 0) AS duplicate_cluster_count,
            COALESCE(cc.duplicate_cluster_bases, '') AS duplicate_cluster_bases,
            COALESCE(cc.duplicate_cluster_keys, '') AS duplicate_cluster_keys,
            COALESCE(cc.has_nondisqualified_canonical, 0) AS has_nondisqualified_canonical,
            COALESCE(cc.has_nondisqualified_canonical_with_profile_facts, 0) AS has_nondisqualified_canonical_with_profile_facts,
            CASE
                WHEN b.blank_disqualification_lane = 'justified_from_other_signal'
                THEN 'justified_from_other_signal'
                WHEN (
                    CASE WHEN trim(COALESCE(p.online_presence, '')) <> '' THEN 1 ELSE 0 END
                    + CASE WHEN trim(COALESCE(p.website_presence, '')) <> '' THEN 1 ELSE 0 END
                    + CASE WHEN trim(COALESCE(p.lead_metadata, '')) <> '' THEN 1 ELSE 0 END
                    + CASE WHEN COALESCE(b.business_operating_status, 'active') <> 'active' THEN 1 ELSE 0 END
                    + CASE
                        WHEN COALESCE(b.blank_disqualification_basis, '') = 'profile_registry_excluded_skipped'
                        THEN 1 ELSE 0
                      END
                    + CASE
                        WHEN trim(COALESCE(b.review_decision, '')) <> ''
                         AND (
                                lower(COALESCE(b.review_decision, '')) = 'exclude'
                                OR lower(COALESCE(b.review_decision, '')) LIKE 'exclude_%'
                                OR lower(COALESCE(b.review_decision, '')) = 'hold'
                                OR lower(COALESCE(b.review_decision, '')) LIKE 'hold_%'
                             )
                        THEN 1 ELSE 0
                      END
                ) > 0
                THEN 'represented_but_still_blank_rationale'
                WHEN b.blank_disqualification_lane = 'recoverable_from_profile_text'
                THEN 'recoverable_from_profile_text'
                ELSE 'underexplained_unrepresented'
            END AS measurement_lane
        FROM leadops_v_blank_disqualification_audit b
        LEFT JOIN leadops_profiles p
          ON p.lead_id = b.lead_id
        LEFT JOIN cluster_context cc
          ON cc.lead_id = b.lead_id;

        DROP VIEW IF EXISTS leadops_v_blank_disqualification_measurement_summary;
        CREATE VIEW leadops_v_blank_disqualification_measurement_summary AS
        SELECT
            measurement_lane,
            normalized_representation_lane,
            COUNT(*) AS lead_count,
            SUM(has_online_presence_representation) AS online_presence_count,
            SUM(has_website_presence_representation) AS website_presence_count,
            SUM(has_lead_metadata_representation) AS lead_metadata_count,
            SUM(has_nonactive_operating_status_representation) AS nonactive_operating_status_count,
            SUM(has_review_decision_representation) AS review_decision_count,
            SUM(in_duplicate_cluster) AS duplicate_cluster_count,
            SUM(has_nondisqualified_canonical) AS nondisqualified_canonical_count,
            SUM(has_nondisqualified_canonical_with_profile_facts) AS nondisqualified_canonical_with_profile_facts_count
        FROM leadops_v_blank_disqualification_measurement
        GROUP BY
            measurement_lane,
            normalized_representation_lane
        ORDER BY
            lead_count DESC,
            measurement_lane ASC,
            normalized_representation_lane ASC;

        DROP VIEW IF EXISTS leadops_v_blank_disqualification_worklist_skip_rationale;
        CREATE VIEW leadops_v_blank_disqualification_worklist_skip_rationale AS
        SELECT
            m.lead_id,
            m.name,
            m.batch,
            m.status,
            m.outreach_status,
            a.contact_path,
            m.profile_path,
            m.blank_disqualification_basis,
            m.blank_disqualification_lane,
            m.measurement_lane,
            COALESCE(r.reporting_lane, '') AS reporting_lane,
            COALESCE(r.reporting_family, '') AS reporting_family
        FROM leadops_v_blank_disqualification_measurement m
        JOIN leadops_v_blank_disqualification_audit a
          ON a.lead_id = m.lead_id
        LEFT JOIN leadops_v_blank_disqualification_reporting r
          ON r.lead_id = m.lead_id
        WHERE m.blank_disqualification_basis = 'profile_notes_worklist_skip';

        DROP VIEW IF EXISTS leadops_v_blank_disqualification_worklist_skip_rationale_summary;
        CREATE VIEW leadops_v_blank_disqualification_worklist_skip_rationale_summary AS
        SELECT
            batch,
            contact_path,
            reporting_lane,
            reporting_family,
            COUNT(*) AS lead_count,
            SUM(CASE WHEN profile_path LIKE 'leads/profiles/%' THEN 1 ELSE 0 END) AS profiles_tree_count
        FROM leadops_v_blank_disqualification_worklist_skip_rationale
        GROUP BY
            batch,
            contact_path,
            reporting_lane,
            reporting_family
        ORDER BY
            lead_count DESC,
            batch ASC,
            contact_path ASC,
            reporting_lane ASC;

        DROP VIEW IF EXISTS leadops_v_blank_disqualification_adjudication_priority;
        CREATE VIEW leadops_v_blank_disqualification_adjudication_priority AS
        WITH underexplained AS (
            SELECT
                m.lead_id,
                m.name,
                m.batch,
                m.status,
                m.outreach_status,
                a.profile_path,
                a.contact_path,
                a.contact_search,
                a.email,
                a.website,
                a.has_snapshot_section,
                a.has_notes_section,
                a.has_evidence_section,
                a.has_no_public_signal,
                a.has_property_signal,
                a.has_phone_only_signal,
                a.has_duplicate_signal,
                CASE
                    WHEN a.profile_path LIKE 'leads/disqualified/%' THEN 'disqualified_tree'
                    ELSE 'other_tree'
                END AS path_family,
                CASE
                    WHEN trim(COALESCE(a.email, '')) <> ''
                      OR trim(COALESCE(a.website, '')) <> ''
                      OR lower(trim(COALESCE(a.contact_path, ''))) NOT IN ('', 'unknown')
                    THEN 1 ELSE 0
                END AS has_concrete_contact_or_site_trace,
                CASE
                    WHEN a.has_duplicate_signal = 1 THEN 'duplicate_like'
                    WHEN a.has_phone_only_signal = 1 THEN 'phone_only_like'
                    WHEN a.has_property_signal = 1 AND a.has_no_public_signal = 1 THEN 'property_plus_no_public'
                    WHEN a.has_property_signal = 1 THEN 'property_like'
                    WHEN a.has_no_public_signal = 1 THEN 'no_public_like'
                    ELSE 'no_clear_signal'
                END AS weak_signal_family
            FROM leadops_v_blank_disqualification_measurement m
            JOIN leadops_v_blank_disqualification_audit a
              ON a.lead_id = m.lead_id
            WHERE m.measurement_lane = 'underexplained_unrepresented'
        ),
        batch_counts AS (
            SELECT
                batch,
                COUNT(*) AS batch_underexplained_count
            FROM underexplained
            GROUP BY batch
        )
        SELECT
            u.lead_id,
            u.name,
            u.batch,
            u.status,
            u.outreach_status,
            u.profile_path,
            u.contact_path,
            u.contact_search,
            u.email,
            u.website,
            u.path_family,
            u.weak_signal_family,
            u.has_concrete_contact_or_site_trace,
            u.has_snapshot_section,
            u.has_notes_section,
            u.has_evidence_section,
            u.has_no_public_signal,
            u.has_property_signal,
            u.has_phone_only_signal,
            u.has_duplicate_signal,
            bc.batch_underexplained_count,
            CASE
                WHEN u.has_concrete_contact_or_site_trace = 1
                THEN 1
                WHEN u.weak_signal_family IN ('duplicate_like', 'phone_only_like')
                THEN 2
                WHEN u.path_family = 'other_tree'
                  OR u.weak_signal_family IN ('property_plus_no_public', 'property_like')
                THEN 3
                ELSE 4
            END AS adjudication_priority_rank,
            CASE
                WHEN u.has_concrete_contact_or_site_trace = 1
                THEN 'priority_1_concrete_trace_recheck'
                WHEN u.weak_signal_family IN ('duplicate_like', 'phone_only_like')
                THEN 'priority_2_specific_hint_recheck'
                WHEN u.path_family = 'other_tree'
                  OR u.weak_signal_family IN ('property_plus_no_public', 'property_like')
                THEN 'priority_3_contextual_recheck'
                ELSE 'priority_4_bulk_residue'
            END AS adjudication_priority_lane,
            CASE
                WHEN u.has_concrete_contact_or_site_trace = 1
                THEN 'concrete_contact_or_site_trace'
                WHEN u.weak_signal_family = 'duplicate_like'
                THEN 'duplicate_wording_only'
                WHEN u.weak_signal_family = 'phone_only_like'
                THEN 'phone_only_wording_only'
                WHEN u.path_family = 'other_tree'
                  AND u.weak_signal_family IN ('property_plus_no_public', 'property_like')
                THEN 'other_tree_plus_property_context'
                WHEN u.path_family = 'other_tree'
                THEN 'other_tree_context'
                WHEN u.weak_signal_family = 'property_plus_no_public'
                THEN 'property_plus_no_public_wording'
                WHEN u.weak_signal_family = 'property_like'
                THEN 'property_wording_only'
                WHEN u.weak_signal_family = 'no_public_like'
                THEN 'no_public_wording_only'
                ELSE 'no_clear_signal'
            END AS adjudication_priority_basis
        FROM underexplained u
        LEFT JOIN batch_counts bc
          ON bc.batch = u.batch;

        DROP VIEW IF EXISTS leadops_v_blank_disqualification_adjudication_priority_summary;
        CREATE VIEW leadops_v_blank_disqualification_adjudication_priority_summary AS
        SELECT
            adjudication_priority_rank,
            adjudication_priority_lane,
            adjudication_priority_basis,
            path_family,
            weak_signal_family,
            COUNT(*) AS lead_count,
            SUM(has_concrete_contact_or_site_trace) AS concrete_trace_count,
            SUM(has_notes_section) AS notes_section_count,
            SUM(has_evidence_section) AS evidence_section_count,
            MIN(batch_underexplained_count) AS min_batch_underexplained_count,
            MAX(batch_underexplained_count) AS max_batch_underexplained_count
        FROM leadops_v_blank_disqualification_adjudication_priority
        GROUP BY
            adjudication_priority_rank,
            adjudication_priority_lane,
            adjudication_priority_basis,
            path_family,
            weak_signal_family
        ORDER BY
            adjudication_priority_rank ASC,
            lead_count DESC,
            adjudication_priority_basis ASC;

        DROP VIEW IF EXISTS leadops_v_blank_disqualification_rank1_recheck;
        CREATE VIEW leadops_v_blank_disqualification_rank1_recheck AS
        WITH rank1 AS (
            SELECT
                p.lead_id,
                p.name,
                p.batch,
                p.status,
                p.outreach_status,
                p.profile_path,
                p.contact_path,
                p.contact_search,
                p.email,
                p.website,
                p.path_family,
                p.weak_signal_family,
                p.has_concrete_contact_or_site_trace,
                p.has_snapshot_section,
                p.has_notes_section,
                p.has_evidence_section,
                p.has_no_public_signal,
                p.has_property_signal,
                p.has_phone_only_signal,
                p.has_duplicate_signal,
                p.batch_underexplained_count,
                lp.title,
                lp.business_overview,
                lp.contact_information,
                lp.online_presence,
                lp.social_presence,
                lp.next_steps,
                lp.raw_markdown,
                lower(COALESCE(lp.raw_markdown, '')) AS raw_markdown_lc,
                lower(trim(COALESCE(json_extract(lp.kv_json, '$.Status'), ''))) AS profile_status
            FROM leadops_v_blank_disqualification_adjudication_priority p
            JOIN leadops_profiles lp
              ON lp.lead_id = p.lead_id
            WHERE p.adjudication_priority_rank = 1
        )
        SELECT
            r.lead_id,
            r.name,
            r.batch,
            r.status,
            r.outreach_status,
            r.profile_path,
            r.contact_path,
            r.contact_search,
            r.email,
            r.website,
            r.path_family,
            r.weak_signal_family,
            r.batch_underexplained_count,
            r.profile_status,
            CASE
                WHEN lower(trim(COALESCE(r.contact_path, ''))) IN ('form', 'web_form', 'web form')
                 AND (
                        r.raw_markdown_lc LIKE '%bizapedia.com/contact.aspx%'
                        OR r.raw_markdown_lc LIKE '%wikipedia:contact_us%'
                     )
                THEN 'artifact_contact_path_recheck'
                WHEN r.profile_status = 'ready'
                 AND (
                        trim(COALESCE(r.business_overview, '')) <> ''
                        OR trim(COALESCE(r.contact_information, '')) <> ''
                        OR trim(COALESCE(r.online_presence, '')) <> ''
                        OR trim(COALESCE(r.social_presence, '')) <> ''
                        OR trim(COALESCE(r.next_steps, '')) <> ''
                     )
                THEN 'state_drift_recheck'
                ELSE 'genuine_ambiguous_recheck'
            END AS rank1_recheck_lane,
            CASE
                WHEN lower(trim(COALESCE(r.contact_path, ''))) IN ('form', 'web_form', 'web form')
                 AND r.raw_markdown_lc LIKE '%bizapedia.com/contact.aspx%'
                THEN 'directory_contact_artifact'
                WHEN lower(trim(COALESCE(r.contact_path, ''))) IN ('form', 'web_form', 'web form')
                 AND r.raw_markdown_lc LIKE '%wikipedia:contact_us%'
                THEN 'wikipedia_contact_artifact'
                WHEN r.profile_status = 'ready'
                 AND trim(COALESCE(r.online_presence, '')) <> ''
                THEN 'ready_status_with_online_presence'
                WHEN r.profile_status = 'ready'
                 AND trim(COALESCE(r.contact_information, '')) <> ''
                THEN 'ready_status_with_contact_information'
                WHEN r.profile_status = 'ready'
                 AND trim(COALESCE(r.social_presence, '')) <> ''
                THEN 'ready_status_with_social_presence'
                WHEN r.profile_status = 'ready'
                 AND trim(COALESCE(r.business_overview, '')) <> ''
                THEN 'ready_status_with_business_overview'
                WHEN r.profile_status = 'ready'
                 AND trim(COALESCE(r.next_steps, '')) <> ''
                THEN 'ready_status_with_next_steps'
                ELSE 'still_ambiguous_after_v16'
            END AS rank1_recheck_basis
        FROM rank1 r;

        DROP VIEW IF EXISTS leadops_v_blank_disqualification_rank1_recheck_summary;
        CREATE VIEW leadops_v_blank_disqualification_rank1_recheck_summary AS
        SELECT
            rank1_recheck_lane,
            rank1_recheck_basis,
            contact_path,
            weak_signal_family,
            COUNT(*) AS lead_count,
            MIN(batch_underexplained_count) AS min_batch_underexplained_count,
            MAX(batch_underexplained_count) AS max_batch_underexplained_count
        FROM leadops_v_blank_disqualification_rank1_recheck
        GROUP BY
            rank1_recheck_lane,
            rank1_recheck_basis,
            contact_path,
            weak_signal_family
        ORDER BY
            lead_count DESC,
            rank1_recheck_lane ASC,
            rank1_recheck_basis ASC;

        DROP VIEW IF EXISTS leadops_v_blank_disqualification_state_drift_substantive_review;
        CREATE VIEW leadops_v_blank_disqualification_state_drift_substantive_review AS
        WITH state_drift AS (
            SELECT
                r.lead_id,
                r.name,
                r.batch,
                r.status,
                r.outreach_status,
                r.profile_path,
                r.contact_path,
                r.contact_search,
                r.email,
                r.website,
                r.path_family,
                r.weak_signal_family,
                r.batch_underexplained_count,
                r.profile_status,
                r.rank1_recheck_lane,
                r.rank1_recheck_basis,
                lp.business_overview,
                lp.contact_information,
                lp.online_presence,
                lp.social_presence,
                lp.next_steps,
                lp.raw_markdown,
                lower(COALESCE(lp.raw_markdown, '')) AS raw_markdown_lc,
                COALESCE(lrd.decision, '') AS latest_review_decision
            FROM leadops_v_blank_disqualification_rank1_recheck r
            JOIN leadops_profiles lp
              ON lp.lead_id = r.lead_id
            LEFT JOIN leadops_v_latest_review_decision lrd
              ON lrd.lead_id = r.lead_id
            WHERE r.rank1_recheck_lane = 'state_drift_recheck'
              AND r.profile_status = 'ready'
              AND lower(COALESCE(lrd.decision, '')) <> 'keep_ready'
              AND lower(COALESCE(lrd.decision, '')) NOT LIKE 'hold%'
        ),
        typed AS (
            SELECT
                s.*,
                CASE
                    WHEN s.raw_markdown_lc LIKE '%pending research%'
                     AND s.raw_markdown_lc LIKE '%header anomaly normalization pass%'
                    THEN 'ready_but_pending_research_header_fix'
                    WHEN s.raw_markdown_lc LIKE '%pending research%'
                    THEN 'ready_but_pending_research'
                    ELSE 'ready_with_substantive_profile'
                END AS state_drift_subtype,
                CASE
                    WHEN trim(COALESCE(s.business_overview, '')) <> '' THEN 1 ELSE 0
                END AS has_business_overview,
                CASE
                    WHEN trim(COALESCE(s.contact_information, '')) <> '' THEN 1 ELSE 0
                END AS has_contact_information,
                CASE
                    WHEN trim(COALESCE(s.online_presence, '')) <> '' THEN 1 ELSE 0
                END AS has_online_presence,
                CASE
                    WHEN trim(COALESCE(s.social_presence, '')) <> '' THEN 1 ELSE 0
                END AS has_social_presence,
                CASE
                    WHEN trim(COALESCE(s.next_steps, '')) <> '' THEN 1 ELSE 0
                END AS has_next_steps,
                CASE
                    WHEN s.raw_markdown_lc LIKE '%ambiguous%'
                      OR s.raw_markdown_lc LIKE '%similarly named%'
                      OR s.raw_markdown_lc LIKE '%exact legal-name linkage remains ambiguous%'
                      OR s.raw_markdown_lc LIKE '%no reliable exact lead match%'
                      OR s.raw_markdown_lc LIKE '%multiple % operators%'
                      OR s.raw_markdown_lc LIKE '%multiple % entities%'
                    THEN 1 ELSE 0
                END AS has_identity_ambiguity
            FROM state_drift s
        )
        SELECT
            t.lead_id,
            t.name,
            t.batch,
            t.status,
            t.outreach_status,
            t.profile_path,
            t.contact_path,
            t.contact_search,
            t.email,
            t.website,
            t.path_family,
            t.weak_signal_family,
            t.batch_underexplained_count,
            t.profile_status,
            t.rank1_recheck_lane,
            t.rank1_recheck_basis,
            t.state_drift_subtype,
            t.has_business_overview,
            t.has_contact_information,
            t.has_online_presence,
            t.has_social_presence,
            t.has_next_steps,
            t.has_identity_ambiguity,
            CASE
                WHEN t.state_drift_subtype <> 'ready_with_substantive_profile'
                THEN 4
                WHEN t.has_identity_ambiguity = 0
                 AND (t.has_contact_information = 1 OR t.has_online_presence = 1)
                THEN 1
                WHEN t.has_identity_ambiguity = 0
                THEN 2
                ELSE 3
            END AS substantive_review_priority_rank,
            CASE
                WHEN t.state_drift_subtype <> 'ready_with_substantive_profile'
                THEN 'cleanup_drift_not_substantive_queue'
                WHEN t.has_identity_ambiguity = 0
                 AND (t.has_contact_information = 1 OR t.has_online_presence = 1)
                THEN 'likely_stale_false_positive'
                WHEN t.has_identity_ambiguity = 0
                THEN 'substantive_but_weaker_recheck'
                ELSE 'manual_identity_check'
            END AS substantive_review_lane,
            CASE
                WHEN t.state_drift_subtype = 'ready_but_pending_research_header_fix'
                THEN 'pending_research_and_header_fix_artifact'
                WHEN t.state_drift_subtype = 'ready_but_pending_research'
                THEN 'pending_research_artifact'
                WHEN t.has_identity_ambiguity = 1
                THEN 'identity_ambiguity_phrase_present'
                WHEN t.has_contact_information = 1 AND t.has_online_presence = 1
                THEN 'ready_with_contact_and_online_presence'
                WHEN t.has_contact_information = 1
                THEN 'ready_with_contact_information'
                WHEN t.has_online_presence = 1
                THEN 'ready_with_online_presence'
                WHEN t.has_business_overview = 1
                THEN 'ready_with_business_overview_only'
                WHEN t.has_social_presence = 1
                THEN 'ready_with_social_presence_only'
                WHEN t.has_next_steps = 1
                THEN 'ready_with_next_steps_only'
                ELSE 'ready_with_other_substantive_signal'
            END AS substantive_review_basis
        FROM typed t;

        DROP VIEW IF EXISTS leadops_v_blank_disqualification_state_drift_substantive_review_summary;
        CREATE VIEW leadops_v_blank_disqualification_state_drift_substantive_review_summary AS
        SELECT
            state_drift_subtype,
            substantive_review_priority_rank,
            substantive_review_lane,
            substantive_review_basis,
            contact_path,
            COUNT(*) AS lead_count,
            MIN(batch_underexplained_count) AS min_batch_underexplained_count,
            MAX(batch_underexplained_count) AS max_batch_underexplained_count
        FROM leadops_v_blank_disqualification_state_drift_substantive_review
        GROUP BY
            state_drift_subtype,
            substantive_review_priority_rank,
            substantive_review_lane,
            substantive_review_basis,
            contact_path
        ORDER BY
            substantive_review_priority_rank ASC,
            lead_count DESC,
            substantive_review_lane ASC,
            substantive_review_basis ASC;

        DROP VIEW IF EXISTS leadops_v_blank_disqualification_state_drift_cleanup_queue;
        CREATE VIEW leadops_v_blank_disqualification_state_drift_cleanup_queue AS
        SELECT
            s.lead_id,
            s.name,
            s.batch,
            s.status,
            s.outreach_status,
            s.profile_path,
            s.contact_path,
            s.contact_search,
            s.email,
            s.website,
            s.path_family,
            s.weak_signal_family,
            s.batch_underexplained_count,
            s.profile_status,
            s.state_drift_subtype,
            s.substantive_review_basis,
            CASE
                WHEN s.substantive_review_basis = 'pending_research_artifact'
                THEN 1
                ELSE 3
            END AS cleanup_priority_rank,
            CASE
                WHEN s.substantive_review_basis = 'pending_research_artifact'
                THEN 'unresolved_ready_pending_research_conflict'
                ELSE 'historical_header_fix_artifact'
            END AS cleanup_queue_lane
        FROM leadops_v_blank_disqualification_state_drift_substantive_review s
        WHERE s.substantive_review_lane = 'cleanup_drift_not_substantive_queue';

        DROP VIEW IF EXISTS leadops_v_blank_disqualification_state_drift_cleanup_queue_summary;
        CREATE VIEW leadops_v_blank_disqualification_state_drift_cleanup_queue_summary AS
        SELECT
            cleanup_priority_rank,
            cleanup_queue_lane,
            substantive_review_basis,
            contact_path,
            COUNT(*) AS lead_count,
            MIN(batch_underexplained_count) AS min_batch_underexplained_count,
            MAX(batch_underexplained_count) AS max_batch_underexplained_count
        FROM leadops_v_blank_disqualification_state_drift_cleanup_queue
        GROUP BY
            cleanup_priority_rank,
            cleanup_queue_lane,
            substantive_review_basis,
            contact_path
        ORDER BY
            cleanup_priority_rank ASC,
            lead_count DESC,
            cleanup_queue_lane ASC,
            substantive_review_basis ASC,
            contact_path ASC;

        DROP VIEW IF EXISTS leadops_v_blank_disqualification_active_residue_ready_marker_review;
        CREATE VIEW leadops_v_blank_disqualification_active_residue_ready_marker_review AS
        WITH target AS (
            SELECT
                m.lead_id,
                m.name,
                m.batch,
                m.status,
                m.outreach_status,
                l.contact_path,
                l.email,
                l.website,
                m.profile_path,
                COALESCE(lrd.decision, '') AS latest_review_decision,
                COALESCE(lrd.reason, '') AS latest_review_reason,
                COALESCE(lp.business_overview, '') AS business_overview,
                COALESCE(lp.contact_information, '') AS contact_information,
                lower(COALESCE(lp.raw_markdown, '')) AS raw_markdown_lc
            FROM leadops_v_blank_disqualification_measurement m
            JOIN leadops_leads l
              ON l.lead_id = m.lead_id
            JOIN leadops_profiles lp
              ON lp.lead_id = m.lead_id
            LEFT JOIN leadops_v_latest_review_decision lrd
              ON lrd.lead_id = m.lead_id
            WHERE m.measurement_lane = 'underexplained_unrepresented'
              AND (
                    lower(COALESCE(lp.raw_markdown, '')) LIKE '%**status:** ready%'
                 OR lower(COALESCE(lp.raw_markdown, '')) LIKE '%- **status:** ready%'
                 OR lower(COALESCE(lp.raw_markdown, '')) LIKE '%status: ready%'
                 OR lower(COALESCE(lp.raw_markdown, '')) LIKE '%recheck changed status to ready%'
              )
        )
        SELECT
            t.lead_id,
            t.name,
            t.batch,
            t.status,
            t.outreach_status,
            t.contact_path,
            t.email,
            t.website,
            t.profile_path,
            t.latest_review_decision,
            t.latest_review_reason,
            t.business_overview,
            t.contact_information,
            CASE
                WHEN t.raw_markdown_lc LIKE '%header anomaly normalization pass%'
                  OR t.raw_markdown_lc LIKE '%wikipedia:contact_us%'
                THEN 2
                ELSE 1
            END AS ready_marker_priority_rank,
            CASE
                WHEN t.raw_markdown_lc LIKE '%header anomaly normalization pass%'
                  OR t.raw_markdown_lc LIKE '%wikipedia:contact_us%'
                THEN 'header_anomaly_ready_artifact'
                ELSE 'substantive_ready_profile'
            END AS ready_marker_lane,
            CASE
                WHEN lower(t.latest_review_decision) IN ('keep_ready', 'promote_now', 'promote_from_hold')
                THEN 'resolved_ready_recovery'
                WHEN lower(t.latest_review_decision) = 'hold'
                  OR lower(t.latest_review_decision) LIKE 'hold%'
                  OR lower(t.latest_review_decision) = 'exclude'
                  OR lower(t.latest_review_decision) LIKE 'exclude_%'
                  OR lower(t.latest_review_decision) LIKE 'suppress_%'
                THEN 'resolved_non_actionable'
                ELSE 'unresolved'
            END AS review_resolution_outcome
        FROM target t;

        DROP VIEW IF EXISTS leadops_v_blank_disqualification_active_residue_ready_marker_review_summary;
        CREATE VIEW leadops_v_blank_disqualification_active_residue_ready_marker_review_summary AS
        SELECT
            ready_marker_priority_rank,
            ready_marker_lane,
            review_resolution_outcome,
            contact_path,
            COUNT(*) AS lead_count
        FROM leadops_v_blank_disqualification_active_residue_ready_marker_review
        GROUP BY
            ready_marker_priority_rank,
            ready_marker_lane,
            review_resolution_outcome,
            contact_path
        ORDER BY
            ready_marker_priority_rank ASC,
            lead_count DESC,
            ready_marker_lane ASC,
            review_resolution_outcome ASC,
            contact_path ASC;

        DROP VIEW IF EXISTS leadops_v_blank_disqualification_reporting;
        CREATE VIEW leadops_v_blank_disqualification_reporting AS
        SELECT
            m.*,
            COALESCE(c.cleanup_queue_lane, '') AS cleanup_queue_lane,
            COALESCE(c.cleanup_priority_rank, 0) AS cleanup_priority_rank,
            COALESCE(c.substantive_review_basis, '') AS cleanup_basis,
            COALESCE(rm.ready_marker_lane, '') AS ready_marker_lane,
            COALESCE(rm.review_resolution_outcome, '') AS ready_marker_resolution_outcome,
            CASE
                WHEN COALESCE(c.cleanup_queue_lane, '') = 'historical_header_fix_artifact'
                THEN 'historical_artifact_residue'
                WHEN COALESCE(rm.ready_marker_lane, '') = 'header_anomaly_ready_artifact'
                THEN 'historical_artifact_residue'
                WHEN COALESCE(c.cleanup_queue_lane, '') = 'unresolved_ready_pending_research_conflict'
                THEN 'active_cleanup_conflict'
                WHEN COALESCE(rm.review_resolution_outcome, '') = 'resolved_ready_recovery'
                THEN 'resolved_ready_marker_recovery'
                WHEN m.measurement_lane = 'justified_from_other_signal'
                THEN 'justified_from_other_signal'
                WHEN m.measurement_lane = 'represented_but_still_blank_rationale'
                THEN 'represented_but_still_blank_rationale'
                WHEN m.measurement_lane = 'recoverable_from_profile_text'
                THEN 'recoverable_from_profile_text'
                ELSE 'underexplained_unrepresented'
            END AS reporting_lane,
            CASE
                WHEN COALESCE(c.cleanup_queue_lane, '') = 'historical_header_fix_artifact'
                THEN 'historical_artifact_residue'
                WHEN COALESCE(rm.ready_marker_lane, '') = 'header_anomaly_ready_artifact'
                THEN 'historical_artifact_residue'
                WHEN COALESCE(rm.review_resolution_outcome, '') = 'resolved_ready_recovery'
                THEN 'already_justified'
                WHEN m.measurement_lane = 'justified_from_other_signal'
                THEN 'already_justified'
                ELSE 'active_residue'
            END AS reporting_family
        FROM leadops_v_blank_disqualification_measurement m
        LEFT JOIN leadops_v_blank_disqualification_state_drift_cleanup_queue c
          ON c.lead_id = m.lead_id
        LEFT JOIN leadops_v_blank_disqualification_active_residue_ready_marker_review rm
          ON rm.lead_id = m.lead_id;

        DROP VIEW IF EXISTS leadops_v_blank_disqualification_reporting_summary;
        CREATE VIEW leadops_v_blank_disqualification_reporting_summary AS
        SELECT
            reporting_family,
            reporting_lane,
            COUNT(*) AS lead_count,
            SUM(has_online_presence_representation) AS online_presence_count,
            SUM(has_website_presence_representation) AS website_presence_count,
            SUM(has_lead_metadata_representation) AS lead_metadata_count,
            SUM(has_nonactive_operating_status_representation) AS nonactive_operating_status_count,
            SUM(has_review_decision_representation) AS review_decision_count,
            SUM(in_duplicate_cluster) AS duplicate_cluster_count,
            SUM(has_nondisqualified_canonical) AS nondisqualified_canonical_count,
            SUM(has_nondisqualified_canonical_with_profile_facts) AS nondisqualified_canonical_with_profile_facts_count
        FROM leadops_v_blank_disqualification_reporting
        GROUP BY
            reporting_family,
            reporting_lane
        ORDER BY
            lead_count DESC,
            reporting_family ASC,
            reporting_lane ASC;

        DROP VIEW IF EXISTS leadops_v_blank_disqualification_active_residue_cluster_followup;
        CREATE VIEW leadops_v_blank_disqualification_active_residue_cluster_followup AS
        WITH target AS (
            SELECT
                r.lead_id,
                r.name,
                r.batch,
                r.status,
                r.outreach_status,
                r.profile_path,
                r.duplicate_cluster_count,
                r.duplicate_cluster_bases,
                r.duplicate_cluster_keys,
                r.in_duplicate_cluster,
                r.has_nondisqualified_canonical,
                r.has_nondisqualified_canonical_with_profile_facts,
                COALESCE(lrd.decision, '') AS latest_review_decision
            FROM leadops_v_blank_disqualification_reporting r
            LEFT JOIN leadops_v_latest_review_decision lrd
              ON lrd.lead_id = r.lead_id
            WHERE r.reporting_lane = 'underexplained_unrepresented'
              AND r.in_duplicate_cluster = 1
              AND r.has_nondisqualified_canonical = 1
              AND lower(COALESCE(lrd.decision, '')) NOT LIKE 'hold%'
        )
        SELECT
            t.lead_id,
            t.name,
            t.batch,
            t.status,
            t.outreach_status,
            t.profile_path,
            t.duplicate_cluster_count,
            t.duplicate_cluster_bases,
            t.duplicate_cluster_keys,
            ecs.cluster_id,
            ecs.cluster_basis,
            ecs.canonical_lead_id,
            cl.name AS canonical_name,
            cl.status AS canonical_status,
            cl.outreach_status AS canonical_outreach_status,
            cl.profile_path AS canonical_profile_path,
            CASE
                WHEN trim(COALESCE(cp.online_presence, '')) <> ''
                  OR trim(COALESCE(cp.website_presence, '')) <> ''
                  OR trim(COALESCE(cp.lead_metadata, '')) <> ''
                THEN 1 ELSE 0
            END AS canonical_has_profile_facts,
            CASE
                WHEN ecs.cluster_id = 'website_domain:yes'
                THEN 1
                ELSE 0
            END AS has_suspicious_generic_cluster_key,
            CASE
                WHEN ecs.cluster_id = 'website_domain:yes'
                THEN 1
                WHEN ecs.cluster_basis = 'email'
                THEN 2
                ELSE 3
            END AS cluster_followup_priority_rank,
            CASE
                WHEN ecs.cluster_id = 'website_domain:yes'
                THEN 'suspicious_generic_cluster_key'
                WHEN ecs.cluster_basis = 'email'
                THEN 'email_cluster_followup'
                ELSE 'direct_domain_cluster_followup'
            END AS cluster_followup_lane
        FROM target t
        JOIN leadops_entity_cluster_members ecm
          ON ecm.lead_id = t.lead_id
        JOIN leadops_entity_clusters ecs
          ON ecs.cluster_id = ecm.cluster_id
         AND ecs.member_count > 1
        JOIN leadops_leads cl
          ON cl.lead_id = ecs.canonical_lead_id
         AND COALESCE(cl.disqualified, 0) = 0
        LEFT JOIN leadops_profiles cp
          ON cp.lead_id = ecs.canonical_lead_id;

        DROP VIEW IF EXISTS leadops_v_blank_disqualification_active_residue_cluster_followup_summary;
        CREATE VIEW leadops_v_blank_disqualification_active_residue_cluster_followup_summary AS
        SELECT
            cluster_followup_priority_rank,
            cluster_followup_lane,
            cluster_basis,
            canonical_status,
            canonical_outreach_status,
            COUNT(*) AS lead_count
        FROM leadops_v_blank_disqualification_active_residue_cluster_followup
        GROUP BY
            cluster_followup_priority_rank,
            cluster_followup_lane,
            cluster_basis,
            canonical_status,
            canonical_outreach_status
        ORDER BY
            cluster_followup_priority_rank ASC,
            lead_count DESC,
            cluster_followup_lane ASC,
            cluster_basis ASC;

        DROP VIEW IF EXISTS leadops_v_do_not_work;
        CREATE VIEW leadops_v_do_not_work AS
        SELECT
            l.lead_id,
            l.name,
            l.batch,
            l.status,
            l.outreach_status,
            l.email,
            l.website,
            l.updated,
            COALESCE(bos.business_operating_status, 'active') AS business_operating_status,
            COALESCE(bos.business_operating_reason, '') AS business_operating_reason,
            COALESCE(bos.business_operating_source, '') AS business_operating_source,
            COALESCE(ac.audience_family, 'commercial') AS audience_family,
            COALESCE(ac.audience_type, 'business') AS audience_type,
            COALESCE(ac.audience_subtype, 'general_business') AS audience_subtype,
            COALESCE(ac.outreach_voice, 'small_business_neighborly') AS outreach_voice,
            COALESCE(drc.reason_bucket, '') AS disqualification_reason_bucket,
            COALESCE(
                NULLIF(trim(drc.reason_detail), ''),
                CASE
                    WHEN lower(COALESCE(p.raw_markdown, '')) LIKE '%no-contact exhausted disqualification%'
                    THEN 'No public email/phone/website/form/social found after research pass.'
                    ELSE ''
                END
            ) AS disqualification_reason_detail,
            COALESCE(drc.reason_source, '') AS disqualification_reason_source,
            CASE
                WHEN COALESCE(bos.business_operating_status, 'active') = 'permanently_closed' THEN 'closed_business'
                WHEN COALESCE(bos.business_operating_status, 'active') = 'temporarily_closed' THEN 'temporarily_closed_business'
                WHEN l.disqualified = 1 THEN COALESCE(drc.reason_bucket, 'disqualified')
                WHEN lower(COALESCE(l.outreach_status, '')) = 'opt-out' THEN 'opt_out'
                WHEN lower(COALESCE(l.outreach_status, '')) = 'bounced' THEN 'bounced'
                WHEN lower(COALESCE(l.outreach_status, '')) = 'disqualified' THEN 'outreach_disqualified'
                WHEN lower(COALESCE(l.outreach_status, '')) = 'excluded-chain' THEN 'excluded_chain'
                WHEN lower(COALESCE(lrd.decision, '')) LIKE 'exclude_%' THEN lower(COALESCE(lrd.decision, ''))
                ELSE 'manual_hold'
            END AS exclusion_reason,
            'do_not_contact' AS next_action
        FROM leadops_leads l
        LEFT JOIN leadops_v_business_operating_status bos
          ON bos.lead_id = l.lead_id
        LEFT JOIN leadops_v_audience_classification ac
          ON ac.lead_id = l.lead_id
        LEFT JOIN leadops_v_latest_review_decision lrd
          ON lrd.lead_id = l.lead_id
        LEFT JOIN leadops_profiles p
          ON p.lead_id = l.lead_id
        LEFT JOIN leadops_v_disqualification_reason_classification drc
          ON drc.lead_id = l.lead_id
        WHERE COALESCE(bos.business_operating_status, 'active') IN ('permanently_closed', 'temporarily_closed')
           OR l.disqualified = 1
           OR lower(COALESCE(l.outreach_status, '')) IN ('opt-out', 'bounced', 'disqualified', 'excluded-chain')
           OR lower(COALESCE(lrd.decision, '')) LIKE 'exclude_%'
        ORDER BY
            COALESCE(l.updated, '') DESC,
            l.lead_id ASC;

        DROP VIEW IF EXISTS leadops_v_audit_findings_actionable;
        CREATE VIEW leadops_v_audit_findings_actionable AS
        SELECT
            f.id,
            f.run_id,
            f.lead_id,
            COALESCE(l.name, f.lead_name_snapshot) AS name,
            COALESCE(l.batch, '') AS batch,
            f.issue_type_raw,
            f.issue_type_norm,
            f.issue_description,
            f.diamond_worthy,
            f.finding_class,
            f.severity,
            f.dedupe_status,
            f.dedupe_note,
            f.verified_live,
            f.verification_method,
            f.evidence_path,
            f.note,
            f.source_file,
            ar.audit_date,
            ar.lead_range,
            CASE
                WHEN f.finding_class = 'data_quality_issue' THEN 'repair_lead_data'
                WHEN f.diamond_worthy = 1 THEN 'draft_from_verified_issue'
                WHEN f.severity IN ('high', 'medium') THEN 'review_for_outreach'
                ELSE 'archive_or_ignore'
            END AS next_action
        FROM leadops_audit_findings f
        LEFT JOIN leadops_leads l
          ON l.lead_id = f.lead_id
        LEFT JOIN leadops_audit_runs ar
          ON ar.id = f.run_id
        ORDER BY
            CASE f.severity
                WHEN 'high' THEN 1
                WHEN 'medium' THEN 2
                WHEN 'low' THEN 3
                ELSE 4
            END,
            f.diamond_worthy DESC,
            f.lead_id ASC,
            f.id ASC;

        DROP VIEW IF EXISTS leadops_v_research_tasks_open;
        CREATE VIEW leadops_v_research_tasks_open AS
        SELECT
            rt.id,
            rt.lead_id,
            COALESCE(l.name, '') AS name,
            COALESCE(l.batch, '') AS batch,
            rt.task_type,
            rt.status,
            rt.priority,
            rt.website,
            rt.email,
            rt.batch_range,
            COALESCE(ac.audience_family, 'commercial') AS audience_family,
            COALESCE(ac.audience_type, 'business') AS audience_type,
            COALESCE(ac.audience_subtype, 'general_business') AS audience_subtype,
            COALESCE(ac.outreach_voice, 'small_business_neighborly') AS outreach_voice,
            CASE
                WHEN rt.task_type = 'needs_deep_audit' THEN 'run_deep_audit'
                ELSE 'manual_review'
            END AS next_action,
            rt.source_file
        FROM leadops_research_tasks rt
        LEFT JOIN leadops_leads l
          ON l.lead_id = rt.lead_id
        LEFT JOIN leadops_v_audience_classification ac
          ON ac.lead_id = rt.lead_id
        WHERE lower(COALESCE(rt.status, '')) NOT IN ('done', 'completed', 'closed')
        ORDER BY
            CASE lower(COALESCE(rt.priority, ''))
                WHEN 'high' THEN 1
                WHEN 'medium' THEN 2
                WHEN 'low' THEN 3
                ELSE 4
            END,
            rt.lead_id ASC;

        DROP VIEW IF EXISTS leadops_v_bounce_risk_summary;
        CREATE VIEW leadops_v_bounce_risk_summary AS
        SELECT
            be.lead_id,
            COALESCE(l.name, '') AS name,
            be.normalized_recipient AS recipient,
            COUNT(*) AS bounce_event_count,
            MIN(COALESCE(be.event_at, '')) AS first_bounce_at,
            MAX(COALESCE(be.event_at, '')) AS latest_bounce_at,
            group_concat(DISTINCT COALESCE(be.bounce_type, '')) AS bounce_types,
            group_concat(DISTINCT COALESCE(sr.action, '')) AS suppression_actions,
            group_concat(DISTINCT COALESCE(sr.reason, '')) AS suppression_reasons,
            CASE
                WHEN EXISTS (
                    SELECT 1
                    FROM leadops_suppression_registry sr2
                    WHERE sr2.target_type = 'recipient'
                      AND lower(COALESCE(sr2.target_value, '')) = lower(COALESCE(be.normalized_recipient, ''))
                      AND lower(COALESCE(sr2.action, '')) = 'suppress'
                ) THEN 1
                ELSE 0
            END AS is_hard_suppressed
        FROM leadops_bounce_events be
        LEFT JOIN leadops_leads l
          ON l.lead_id = be.lead_id
        LEFT JOIN leadops_suppression_registry sr
          ON sr.target_type = 'recipient'
         AND lower(COALESCE(sr.target_value, '')) = lower(COALESCE(be.normalized_recipient, ''))
        GROUP BY
            be.lead_id,
            COALESCE(l.name, ''),
            be.normalized_recipient
        ORDER BY
            is_hard_suppressed DESC,
            bounce_event_count DESC,
            latest_bounce_at DESC,
            recipient ASC;

        DROP VIEW IF EXISTS leadops_v_latest_draft_provenance;
        CREATE VIEW leadops_v_latest_draft_provenance AS
        SELECT
            dp.lead_id,
            dp.uid,
            dp.recipient,
            dp.subject,
            dp.draft_date,
            dp.from_addr,
            dp.href,
            dp.body_text,
            dp.body_source_text,
            dp.draft_variant,
            dp.source_file
        FROM leadops_draft_provenance dp
        WHERE dp.id = (
            SELECT MAX(dp2.id)
            FROM leadops_draft_provenance dp2
            WHERE lower(COALESCE(dp2.recipient, '')) = lower(COALESCE(dp.recipient, ''))
              AND lower(COALESCE(dp2.subject, '')) = lower(COALESCE(dp.subject, ''))
        );

        DROP VIEW IF EXISTS leadops_v_business_facts_summary;
        CREATE VIEW leadops_v_business_facts_summary AS
        SELECT
            lead_id,
            fact_type,
            COUNT(*) AS fact_count,
            group_concat(DISTINCT fact_value) AS fact_values,
            group_concat(DISTINCT source_kind) AS source_kinds
        FROM leadops_business_facts
        GROUP BY
            lead_id,
            fact_type
        ORDER BY
            lead_id ASC,
            fact_type ASC;

        DROP VIEW IF EXISTS leadops_v_evidence_artifacts_summary;
        CREATE VIEW leadops_v_evidence_artifacts_summary AS
        SELECT
            lead_id,
            artifact_group,
            artifact_kind,
            COUNT(*) AS artifact_count,
            SUM(COALESCE(size_bytes, 0)) AS total_bytes,
            group_concat(DISTINCT relative_path) AS sample_paths
        FROM leadops_evidence_artifacts
        GROUP BY
            lead_id,
            artifact_group,
            artifact_kind
        ORDER BY
            lead_id ASC,
            artifact_group ASC,
            artifact_kind ASC;

        DROP VIEW IF EXISTS leadops_v_entity_edges_summary;
        CREATE VIEW leadops_v_entity_edges_summary AS
        SELECT
            src_lead_id AS lead_id,
            edge_type,
            COUNT(*) AS edge_count,
            group_concat(DISTINCT COALESCE(edge_value, CAST(dst_lead_id AS TEXT))) AS edge_values,
            group_concat(DISTINCT source_kind) AS source_kinds
        FROM leadops_entity_edges
        GROUP BY
            src_lead_id,
            edge_type
        ORDER BY
            src_lead_id ASC,
            edge_type ASC;

        DROP VIEW IF EXISTS leadops_v_search_documents;
        CREATE VIEW leadops_v_search_documents AS
        SELECT
            id,
            lead_id,
            doc_type,
            title,
            source_path,
            length(body_text) AS body_length,
            source_kind,
            content_hash
        FROM leadops_search_documents
        ORDER BY
            lead_id ASC,
            doc_type ASC,
            source_path ASC;

        DROP VIEW IF EXISTS leadops_v_vector_index_queue_pending;
        CREATE VIEW leadops_v_vector_index_queue_pending AS
        SELECT
            viq.id,
            viq.doc_id,
            viq.lead_id,
            viq.doc_type,
            viq.source_path,
            viq.content_hash,
            viq.embedding_status,
            viq.embedding_model,
            viq.embedded_at
        FROM leadops_vector_index_queue viq
        WHERE lower(COALESCE(viq.embedding_status, 'pending')) <> 'embedded'
        ORDER BY
            viq.lead_id ASC,
            viq.doc_type ASC,
            viq.id ASC;

        DROP VIEW IF EXISTS leadops_v_latest_review_decision;
        CREATE VIEW leadops_v_latest_review_decision AS
        SELECT
            rd.lead_id,
            rd.decision,
            rd.reason,
            rd.source_file,
            rd.source
        FROM leadops_review_decisions rd
        WHERE rd.id = (
            SELECT MAX(rd2.id)
            FROM leadops_review_decisions rd2
            WHERE rd2.lead_id = rd.lead_id
        );

        DROP VIEW IF EXISTS leadops_v_audience_classification;
        CREATE VIEW leadops_v_audience_classification AS
        WITH base AS (
            SELECT
                l.lead_id,
                l.name,
                l.status,
                l.outreach_status,
                l.email,
                l.website,
                l.website_domain,
                l.batch,
                l.profile_path,
                replace(replace(lower(COALESCE(l.name, '')), '-', ' '), '_', ' ') AS name_lc,
                lower(COALESCE(l.website_domain, '')) AS website_domain_lc,
                lower(COALESCE(l.email_domain, '')) AS email_domain_lc,
                lower(COALESCE(p.naics, '')) AS naics_lc,
                replace(replace(lower(
                    COALESCE(p.business_overview, '') || ' ' ||
                    COALESCE(p.service_offerings, '') || ' ' ||
                    COALESCE(p.target_customers, '') || ' ' ||
                    COALESCE(p.differentiators, '') || ' ' ||
                    COALESCE(p.snapshot, '') || ' ' ||
                    COALESCE(p.observations, '') || ' ' ||
                    COALESCE(p.market_position, '') || ' ' ||
                    COALESCE(p.opportunity_assessment, '') || ' ' ||
                    COALESCE(p.disqualification_rationale, '')
                ), '-', ' '), '_', ' ') AS profile_text_lc
            FROM leadops_leads l
            LEFT JOIN leadops_profiles p
              ON p.lead_id = l.lead_id
        )
        SELECT
            b.lead_id,
            b.name,
            b.status,
            b.outreach_status,
            b.email,
            b.website,
            b.website_domain,
            b.batch,
            b.profile_path,
            CASE
                WHEN b.name_lc LIKE '%church%'
                  OR b.name_lc LIKE '%ministry%'
                  OR b.name_lc LIKE '%missions%'
                  OR b.name_lc LIKE '%faith%'
                  OR b.name_lc LIKE '%christ%'
                  OR b.name_lc LIKE '%bible%'
                  OR b.name_lc LIKE '%gospel%'
                  OR b.name_lc LIKE '%methodist%'
                  OR b.name_lc LIKE '%baptist%'
                  OR b.name_lc LIKE '%fellowship%'
                  OR b.name_lc LIKE '%foundation%'
                  OR b.name_lc LIKE '%organization%'
                  OR b.name_lc LIKE '%nonprofit%'
                  OR b.name_lc LIKE '%dream center%'
                  OR b.name_lc LIKE '%art league%'
                  OR b.name_lc LIKE '%auxiliary%'
                  OR b.name_lc LIKE '%vfw%'
                  OR b.name_lc LIKE '%legion%'
                  OR b.profile_text_lc LIKE '%nonprofit%'
                  OR b.profile_text_lc LIKE '%donor%'
                  OR b.profile_text_lc LIKE '%donation%'
                THEN 'mission_style'
                WHEN b.name_lc LIKE '%city of %'
                  OR b.name_lc LIKE '%county%'
                  OR b.name_lc LIKE '%independent school district%'
                  OR b.name_lc LIKE '%economic development corporation%'
                  OR b.name_lc LIKE '%industrial development corporation%'
                  OR b.website_domain_lc LIKE '%.gov'
                  OR b.website_domain_lc LIKE '%.gov.%'
                  OR b.website_domain_lc LIKE '%.us'
                  OR b.website_domain_lc LIKE '%.tx.us'
                THEN 'public_institution'
                WHEN b.website_domain_lc IN (
                    'kongsberg.com',
                    'dxpe.com',
                    'vertexaerospace.com',
                    'northerntool.com',
                    'interstatebatteries.com',
                    'snapon.com',
                    'trueway.com',
                    'westernshamrock.com'
                )
                  OR b.name_lc LIKE '%enterprise%'
                  OR b.name_lc LIKE '%aerospace%'
                THEN 'enterprise'
                WHEN b.name_lc LIKE '%hospital%'
                  OR b.name_lc LIKE '%clinic%'
                  OR b.name_lc LIKE '%therapy%'
                  OR b.name_lc LIKE '%counseling%'
                  OR b.name_lc LIKE '%medical%'
                  OR b.name_lc LIKE '%health%'
                THEN 'care_practice'
                ELSE 'commercial'
            END AS audience_family,
            CASE
                WHEN b.name_lc LIKE '%church%'
                  OR b.name_lc LIKE '%ministry%'
                  OR b.name_lc LIKE '%missions%'
                  OR b.name_lc LIKE '%faith%'
                  OR b.name_lc LIKE '%christ%'
                  OR b.name_lc LIKE '%bible%'
                  OR b.name_lc LIKE '%gospel%'
                  OR b.name_lc LIKE '%methodist%'
                  OR b.name_lc LIKE '%baptist%'
                  OR b.name_lc LIKE '%fellowship%'
                THEN 'ministry'
                WHEN b.name_lc LIKE '%foundation%'
                  OR b.name_lc LIKE '%organization%'
                  OR b.name_lc LIKE '%nonprofit%'
                  OR b.name_lc LIKE '%dream center%'
                  OR b.name_lc LIKE '%art league%'
                  OR b.name_lc LIKE '%auxiliary%'
                  OR b.name_lc LIKE '%vfw%'
                  OR b.name_lc LIKE '%legion%'
                  OR b.name_lc LIKE '%center%'
                  OR b.profile_text_lc LIKE '%nonprofit%'
                  OR b.profile_text_lc LIKE '%donor%'
                  OR b.profile_text_lc LIKE '%donation%'
                THEN 'nonprofit'
                WHEN b.name_lc LIKE '%city of %'
                  OR b.name_lc LIKE '%county%'
                  OR b.name_lc LIKE '%independent school district%'
                  OR b.name_lc LIKE '%economic development corporation%'
                  OR b.name_lc LIKE '%industrial development corporation%'
                  OR b.website_domain_lc LIKE '%.gov'
                  OR b.website_domain_lc LIKE '%.gov.%'
                  OR b.website_domain_lc LIKE '%.us'
                  OR b.website_domain_lc LIKE '%.tx.us'
                THEN 'government'
                WHEN b.name_lc LIKE '%school%'
                  OR b.name_lc LIKE '%academy%'
                  OR b.name_lc LIKE '%university%'
                  OR b.name_lc LIKE '%college%'
                  OR b.name_lc LIKE '%isd%'
                THEN 'education'
                WHEN b.name_lc LIKE '%hospital%'
                  OR b.name_lc LIKE '%clinic%'
                  OR b.name_lc LIKE '%therapy%'
                  OR b.name_lc LIKE '%counseling%'
                  OR b.name_lc LIKE '%medical%'
                  OR b.name_lc LIKE '%health%'
                THEN 'healthcare'
                WHEN b.website_domain_lc IN (
                    'kongsberg.com',
                    'dxpe.com',
                    'vertexaerospace.com',
                    'northerntool.com',
                    'interstatebatteries.com',
                    'snapon.com',
                    'trueway.com',
                    'westernshamrock.com'
                )
                  OR b.name_lc LIKE '%enterprise%'
                  OR b.name_lc LIKE '%aerospace%'
                THEN 'big_brand'
                ELSE 'business'
            END AS audience_type,
            CASE
                WHEN b.name_lc LIKE '%church%'
                  OR b.name_lc LIKE '%methodist%'
                  OR b.name_lc LIKE '%baptist%'
                  OR b.name_lc LIKE '%fellowship%'
                THEN 'church'
                WHEN b.name_lc LIKE '%ministry%'
                  OR b.name_lc LIKE '%missions%'
                  OR b.name_lc LIKE '%bible%'
                  OR b.name_lc LIKE '%gospel%'
                THEN 'faith_ministry'
                WHEN b.name_lc LIKE '%dream center%'
                  OR b.name_lc LIKE '%center%'
                  OR b.profile_text_lc LIKE '%community%'
                THEN 'community_nonprofit'
                WHEN b.name_lc LIKE '%foundation%'
                  OR b.name_lc LIKE '%organization%'
                THEN 'foundation_nonprofit'
                WHEN b.name_lc LIKE '%art league%'
                  OR b.name_lc LIKE '%museum%'
                  OR b.name_lc LIKE '%arts%'
                THEN 'arts_nonprofit'
                WHEN b.name_lc LIKE '%economic development corporation%'
                  OR b.name_lc LIKE '%industrial development corporation%'
                THEN 'economic_development'
                WHEN b.website_domain_lc LIKE '%.gov'
                  OR b.website_domain_lc LIKE '%.gov.%'
                  OR b.website_domain_lc LIKE '%.tx.us'
                THEN 'public_agency'
                WHEN b.name_lc LIKE '%school%'
                  OR b.name_lc LIKE '%academy%'
                THEN 'school'
                WHEN b.name_lc LIKE '%university%'
                  OR b.name_lc LIKE '%college%'
                THEN 'higher_ed'
                WHEN b.name_lc LIKE '%therapy%'
                  OR b.name_lc LIKE '%counseling%'
                THEN 'therapy_practice'
                WHEN b.name_lc LIKE '%medical%'
                  OR b.name_lc LIKE '%health%'
                  OR b.name_lc LIKE '%clinic%'
                  OR b.name_lc LIKE '%hospital%'
                THEN 'medical_practice'
                WHEN b.name_lc LIKE '%ranch%'
                  OR b.name_lc LIKE '%farm%'
                  OR b.name_lc LIKE '%exotics%'
                THEN 'ag_ranch'
                WHEN b.name_lc LIKE '%boutique%'
                  OR b.name_lc LIKE '%shop%'
                  OR b.name_lc LIKE '%store%'
                  OR b.name_lc LIKE '%woodshop%'
                  OR b.name_lc LIKE '%washateria%'
                THEN 'local_retail'
                WHEN b.name_lc LIKE '%construction%'
                  OR b.name_lc LIKE '%roof%'
                  OR b.name_lc LIKE '%fence%'
                  OR b.name_lc LIKE '%piling%'
                THEN 'local_service'
                WHEN b.name_lc LIKE '%industrial%'
                  OR b.name_lc LIKE '%manufacturing%'
                  OR b.name_lc LIKE '%machinery%'
                  OR b.name_lc LIKE '%logistics%'
                THEN 'industrial_b2b'
                WHEN b.website_domain_lc IN (
                    'kongsberg.com',
                    'dxpe.com',
                    'vertexaerospace.com',
                    'northerntool.com',
                    'interstatebatteries.com',
                    'snapon.com',
                    'trueway.com',
                    'westernshamrock.com'
                )
                THEN 'enterprise_brand'
                ELSE 'general_business'
            END AS audience_subtype,
            CASE
                WHEN b.name_lc LIKE '%church%'
                  OR b.name_lc LIKE '%ministry%'
                  OR b.name_lc LIKE '%missions%'
                  OR b.name_lc LIKE '%faith%'
                  OR b.name_lc LIKE '%christ%'
                  OR b.name_lc LIKE '%bible%'
                  OR b.name_lc LIKE '%gospel%'
                  OR b.name_lc LIKE '%methodist%'
                  OR b.name_lc LIKE '%baptist%'
                  OR b.name_lc LIKE '%fellowship%'
                THEN 'mission_respectful'
                WHEN b.name_lc LIKE '%foundation%'
                  OR b.name_lc LIKE '%nonprofit%'
                  OR b.name_lc LIKE '%dream center%'
                  OR b.name_lc LIKE '%art league%'
                  OR b.name_lc LIKE '%auxiliary%'
                  OR b.name_lc LIKE '%vfw%'
                  OR b.name_lc LIKE '%legion%'
                  OR b.profile_text_lc LIKE '%nonprofit%'
                  OR b.profile_text_lc LIKE '%donor%'
                  OR b.profile_text_lc LIKE '%donation%'
                THEN 'community_respectful'
                WHEN b.name_lc LIKE '%city of %'
                  OR b.name_lc LIKE '%county%'
                  OR b.name_lc LIKE '%economic development corporation%'
                  OR b.name_lc LIKE '%industrial development corporation%'
                  OR b.website_domain_lc LIKE '%.gov'
                  OR b.website_domain_lc LIKE '%.gov.%'
                  OR b.website_domain_lc LIKE '%.us'
                  OR b.website_domain_lc LIKE '%.tx.us'
                THEN 'institutional_formal'
                WHEN b.website_domain_lc IN (
                    'kongsberg.com',
                    'dxpe.com',
                    'vertexaerospace.com',
                    'northerntool.com',
                    'interstatebatteries.com',
                    'snapon.com',
                    'trueway.com',
                    'westernshamrock.com'
                )
                  OR b.name_lc LIKE '%enterprise%'
                  OR b.name_lc LIKE '%aerospace%'
                THEN 'enterprise_cautious'
                ELSE 'small_business_neighborly'
            END AS outreach_voice
        FROM base b;

        DROP VIEW IF EXISTS leadops_v_audience_summary;
        CREATE VIEW leadops_v_audience_summary AS
        SELECT
            audience_family,
            audience_type,
            audience_subtype,
            outreach_voice,
            COUNT(*) AS lead_count
        FROM leadops_v_audience_classification
        GROUP BY
            audience_family,
            audience_type,
            audience_subtype,
            outreach_voice
        ORDER BY
            lead_count DESC,
            audience_family ASC,
            audience_type ASC,
            audience_subtype ASC;

        DROP VIEW IF EXISTS leadops_v_entity_cluster_summary;
        CREATE VIEW leadops_v_entity_cluster_summary AS
        SELECT
            c.cluster_id,
            c.cluster_basis,
            c.cluster_key,
            c.canonical_lead_id,
            l.name AS canonical_lead_name,
            c.member_count,
            c.member_lead_ids_json,
            c.member_names_json
        FROM leadops_entity_clusters c
        LEFT JOIN leadops_leads l
          ON l.lead_id = c.canonical_lead_id
        ORDER BY
            c.member_count DESC,
            c.cluster_basis ASC,
            c.cluster_key ASC;

        DROP VIEW IF EXISTS leadops_v_duplicate_entity_clusters;
        CREATE VIEW leadops_v_duplicate_entity_clusters AS
        SELECT *
        FROM leadops_v_entity_cluster_summary
        WHERE member_count > 1
        ORDER BY
            member_count DESC,
            cluster_basis ASC,
            cluster_key ASC;

        DROP VIEW IF EXISTS leadops_v_duplicate_entity_clusters_actionable;
        CREATE VIEW leadops_v_duplicate_entity_clusters_actionable AS
        WITH cluster_resolution AS (
            SELECT
                m.cluster_id,
                SUM(
                    CASE
                        WHEN m.is_canonical = 0
                         AND lower(COALESCE(d.decision, '')) IN (
                             'hold_duplicate_cluster',
                             'already_contacted',
                             'keep_held',
                             'keep_distinct_target',
                             'exclude_cluster'
                         )
                        THEN 1
                        ELSE 0
                    END
                ) AS resolved_noncanonical_members,
                SUM(
                    CASE
                        WHEN m.is_canonical = 0
                         AND lower(COALESCE(d.decision, '')) NOT IN (
                             'hold_duplicate_cluster',
                             'already_contacted',
                             'keep_held',
                             'keep_distinct_target',
                             'exclude_cluster'
                         )
                        THEN 1
                        WHEN m.is_canonical = 0
                         AND d.decision IS NULL
                        THEN 1
                        ELSE 0
                    END
                ) AS unresolved_noncanonical_members
            FROM leadops_entity_cluster_members m
            LEFT JOIN leadops_v_latest_review_decision d
              ON d.lead_id = m.lead_id
            GROUP BY
                m.cluster_id
        )
        SELECT
            decs.*,
            cr.resolved_noncanonical_members,
            cr.unresolved_noncanonical_members,
            CASE
                WHEN decs.member_count >= 8 THEN 100
                WHEN decs.member_count >= 5 THEN 90
                WHEN decs.member_count >= 3 THEN 80
                ELSE 70
            END AS priority_score
        FROM leadops_v_duplicate_entity_clusters decs
        JOIN cluster_resolution cr
          ON cr.cluster_id = decs.cluster_id
        WHERE lower(COALESCE(decs.cluster_key, '')) NOT IN (
            'error-lite@duckduckgo.com',
            'name@example.com',
            '1x.png',
            'bbb.org',
            'city-data.com',
            'crash2.zhihu.com',
            'facebook.com',
            'har.com',
            'hub.virtamate.com',
            'mapquest.com',
            'mlb.com',
            'nameberry.com',
            'quicktransportsolutions.com',
            'restaurantji.com',
            'safer.fmcsa.dot.gov',
            'app.fenderr.com',
            'big.dk',
            'census.gov',
            'cplusplus.com',
            'foodnetwork.com',
            'forums.vape.gg',
            'mexico.internationaltrucks.com',
            'stockanalysis.com',
            'support.microsoft.com',
            'britannica.com',
            'start.cortera.com',
            'tiktok.com',
            'texags.com',
            'texas-biz.com',
            'target.com',
            'wm.com',
            'wlox.com',
            'woodforest.com',
            'yelp.com',
            'zmenu.com'
            ,'yes'
            ,'zhihu.com'
        )
          AND lower(COALESCE(decs.cluster_key, '')) NOT LIKE '%@bug-reporting-%'
          AND lower(COALESCE(decs.cluster_key, '')) NOT LIKE '%@error-tracking.reddit.com'
          AND lower(COALESCE(decs.cluster_key, '')) NOT LIKE '%.m-w.com'
          AND COALESCE(cr.unresolved_noncanonical_members, 0) > 0
        ORDER BY
            priority_score DESC,
            cr.unresolved_noncanonical_members DESC,
            decs.member_count DESC,
            decs.cluster_basis ASC,
            decs.cluster_key ASC;

        DROP VIEW IF EXISTS leadops_v_data_quality_issues;
        DROP VIEW IF EXISTS leadops_v_duplicate_email_groups;
        CREATE VIEW leadops_v_duplicate_email_groups AS
        WITH duplicate_groups AS (
            SELECT
                lower(trim(l.email)) AS email_key,
                COUNT(*) AS lead_count,
                COUNT(DISTINCT COALESCE(ec.cluster_id, 'lead:' || CAST(l.lead_id AS TEXT))) AS cluster_count,
                COUNT(DISTINCT COALESCE(NULLIF(lower(trim(l.website_domain)), ''), '(blank)')) AS website_domain_count,
                SUM(
                    CASE
                        WHEN COALESCE(cs.overall_contact_state, 'uncontacted') IN ('contacted', 'bounced', 'replied', 'opt-out')
                        THEN 1 ELSE 0
                    END
                ) AS contacted_members,
                GROUP_CONCAT(CAST(l.lead_id AS TEXT), ',') AS lead_ids
            FROM leadops_leads l
            LEFT JOIN leadops_entity_cluster_members ec
              ON ec.lead_id = l.lead_id
            LEFT JOIN leadops_v_outreach_contact_state cs
              ON cs.lead_id = l.lead_id
            WHERE trim(COALESCE(l.email, '')) <> ''
            GROUP BY lower(trim(l.email))
            HAVING COUNT(*) > 1
        )
        SELECT
            email_key,
            lead_count,
            cluster_count,
            website_domain_count,
            contacted_members,
            lead_ids,
            CASE
                WHEN cluster_count > 1 OR website_domain_count > 1 THEN 1
                ELSE 0
            END AS is_cross_cluster_or_domain,
            CASE
                WHEN cluster_count > 1 OR website_domain_count > 1 THEN 'review_shared_inbox_or_wrong_entity'
                ELSE 'covered_by_duplicate_cluster'
            END AS recommended_action
        FROM duplicate_groups;

        CREATE VIEW leadops_v_data_quality_issues AS
        SELECT
            l.lead_id,
            l.name,
            'dirty_email' AS issue_type,
            l.email AS issue_value,
            'email field contains extra notes, placeholder text, or obvious junk' AS issue_reason
        FROM leadops_leads l
        WHERE COALESCE(l.email, '') LIKE '%;%'
           OR COALESCE(l.email, '') LIKE '%,%'
           OR lower(COALESCE(l.email, '')) LIKE '% via %'
           OR lower(COALESCE(l.email, '')) LIKE '%to be verified%'
           OR lower(COALESCE(l.email, '')) IN (
                'name@example.com',
                'user@domain.com',
                'example@domain.com',
                '[email protected]',
                'abc@example.com',
                'foo@bar.com',
                'fake_example@gmail.com',
                'via contact form',
                'not publicly listed',
                'not',
                's',
                'i',
                'n'
           )
           OR lower(COALESCE(l.email, '')) LIKE '%@duckduckgo.com%'
           OR lower(COALESCE(l.email, '')) LIKE '%@error-tracking.reddit.com%'
           OR lower(COALESCE(l.email, '')) LIKE '%@crash2.zhihu.com%'
           OR lower(COALESCE(l.email, '')) LIKE '%@bug-reporting-%'
           OR lower(COALESCE(l.email, '')) LIKE '%.png%'
           OR lower(COALESCE(l.email, '')) LIKE '%.gif%'
           OR lower(COALESCE(l.email, '')) LIKE '%.jpg%'
           OR lower(COALESCE(l.email, '')) LIKE '%.jpeg%'
           OR lower(COALESCE(l.email, '')) LIKE '%.webp%'
           OR lower(COALESCE(l.email, '')) LIKE '%.svg%'
           OR lower(COALESCE(l.email, '')) LIKE '%.avif%'
           OR lower(COALESCE(l.email, '')) LIKE 'help@mapquest.com%'

        UNION ALL

        SELECT
            l.lead_id,
            l.name,
            'duplicate_email' AS issue_type,
            lower(trim(l.email)) AS issue_value,
            CASE
                WHEN deg.cluster_count > 1 OR deg.website_domain_count > 1
                THEN 'shared email appears across multiple clusters or website domains'
                ELSE 'non-empty email value appears on multiple leads in the same cluster/domain'
            END AS issue_reason
        FROM leadops_leads l
        JOIN leadops_v_duplicate_email_groups deg
          ON deg.email_key = lower(trim(l.email))
         AND deg.is_cross_cluster_or_domain = 1

        UNION ALL

        SELECT
            l.lead_id,
            l.name,
            'dirty_website' AS issue_type,
            l.website AS issue_value,
            'website field looks directory-derived or annotated instead of canonical' AS issue_reason
        FROM leadops_leads l
        WHERE lower(COALESCE(l.website, '')) LIKE '%directory%'
           OR lower(COALESCE(l.website, '')) LIKE '%listing only%'
           OR lower(COALESCE(l.website, '')) LIKE '%facebook only%'
           OR lower(COALESCE(l.website, '')) LIKE '%(working)%'
           OR lower(COALESCE(l.website, '')) LIKE '%referenced%'

        UNION ALL

        SELECT
            l.lead_id,
            l.name,
            'duplicate_cluster' AS issue_type,
            decs.cluster_key AS issue_value,
            'lead belongs to an actionable multi-member business cluster' AS issue_reason
        FROM leadops_entity_cluster_members ecm
        JOIN leadops_v_duplicate_entity_clusters_actionable decs
          ON decs.cluster_id = ecm.cluster_id
        JOIN leadops_leads l
          ON l.lead_id = ecm.lead_id

        UNION ALL

        SELECT
            l.lead_id,
            l.name,
            'low_confidence_contactable' AS issue_type,
            COALESCE(l.website, l.email, '') AS issue_value,
            'contactable lead still has low or mismatch entity confidence' AS issue_reason
        FROM leadops_leads l
        JOIN leadops_entity_match em
          ON em.lead_id = l.lead_id
        WHERE COALESCE(l.email, '') <> ''
          AND COALESCE(l.website, '') <> ''
          AND lower(COALESCE(l.status, '')) <> 'disqualified'
          AND em.confidence_bucket IN ('low', 'mismatch')
          AND lower(COALESCE(l.email, '')) NOT IN (
                'user@domain.com',
                'example@domain.com',
                'abc@example.com',
                'foo@bar.com',
                'fake_example@gmail.com',
                'i',
                'n'
          )
          AND lower(COALESCE(l.email, '')) NOT LIKE '%@2.avif'
          AND lower(COALESCE(l.email, '')) NOT LIKE '%@2x.svg'
          AND lower(COALESCE(l.email, '')) NOT LIKE '%@sentry-next.wixpress.com'
          AND lower(COALESCE(l.website, '')) NOT LIKE '%bizapedia.com%'
          AND lower(COALESCE(l.website, '')) NOT LIKE '%city-data.com%'
          AND lower(COALESCE(l.website, '')) NOT LIKE '%quicktransportsolutions.com%'
          AND lower(COALESCE(l.website, '')) NOT LIKE '%nameberry.com%'
          AND lower(COALESCE(l.website, '')) NOT LIKE '%worldbank.org%'
          AND lower(COALESCE(l.website, '')) NOT LIKE '%house.gov%'
          AND lower(COALESCE(l.website, '')) NOT LIKE '%bbb.org/%'
          AND lower(COALESCE(l.website, '')) NOT LIKE '%forum.%'
          AND lower(COALESCE(l.website, '')) NOT LIKE '%vox.veritas.com%'
          AND lower(COALESCE(l.website, '')) NOT LIKE '%madison.com/news/%'
          AND lower(COALESCE(l.website, '')) NOT LIKE '%inews.co.uk/%'
          AND lower(COALESCE(l.website, '')) NOT LIKE '%beincrypto.com/%'
          AND lower(COALESCE(l.website, '')) NOT LIKE '%tarrantcountytx.gov/%'
          AND lower(COALESCE(l.website, '')) NOT LIKE '%wlox.com/%'
          AND lower(COALESCE(l.website, '')) NOT LIKE '%mlb.com/%'
          AND lower(COALESCE(l.website, '')) NOT LIKE '%morningstar.com/%'
          AND lower(COALESCE(l.website, '')) NOT LIKE '%spanishdict.com/%'
          AND lower(COALESCE(l.website, '')) NOT LIKE '%invstor.com/%'
          AND lower(COALESCE(l.website, '')) NOT LIKE '%marks.com/%'
          AND lower(COALESCE(l.website, '')) NOT LIKE '%tanner.org/%'
          AND lower(COALESCE(l.website, '')) NOT LIKE '%eatatverde.com/%'
        ORDER BY
            issue_type ASC,
            lead_id ASC;

        DROP VIEW IF EXISTS leadops_v_data_quality_priority;
        CREATE VIEW leadops_v_data_quality_priority AS
        SELECT
            dqi.lead_id,
            dqi.name,
            dqi.issue_type,
            dqi.issue_value,
            dqi.issue_reason,
            ll.status,
            ll.outreach_status,
            ll.email,
            ll.website,
            ll.updated,
            COALESCE(lrd.decision, '') AS latest_review_decision,
            COALESCE(lrd.reason, '') AS latest_review_reason,
            CASE dqi.issue_type
                WHEN 'dirty_email' THEN 'repair_email_field'
                WHEN 'duplicate_email' THEN 'review_duplicate_email'
                WHEN 'dirty_website' THEN 'repair_website_field'
                WHEN 'duplicate_cluster' THEN 'review_cluster_canonical'
                WHEN 'low_confidence_contactable' THEN 'review_entity_match'
                ELSE 'manual_review'
            END AS next_action,
            CASE dqi.issue_type
                WHEN 'dirty_email' THEN 100
                WHEN 'duplicate_email' THEN 97
                WHEN 'dirty_website' THEN 95
                WHEN 'duplicate_cluster' THEN 80
                WHEN 'low_confidence_contactable' THEN 70
                ELSE 50
            END AS priority_score
        FROM leadops_v_data_quality_issues dqi
        JOIN leadops_leads ll
          ON ll.lead_id = dqi.lead_id
        LEFT JOIN leadops_v_outreach_contact_state ocs
          ON ocs.lead_id = dqi.lead_id
        LEFT JOIN leadops_v_latest_review_decision lrd
          ON lrd.lead_id = dqi.lead_id
        WHERE lower(COALESCE(ll.status, '')) NOT IN ('disqualified', 'archived')
          AND lower(COALESCE(ocs.overall_contact_state, 'uncontacted')) NOT IN ('contacted', 'bounced', 'replied', 'opt-out')
          AND NOT (
                dqi.issue_type = 'low_confidence_contactable'
                AND (
                    lower(COALESCE(lrd.decision, '')) IN (
                        'already_contacted',
                        'hold_duplicate_cluster',
                        'keep_held',
                        'keep_distinct_target',
                        'promote_from_hold',
                        'promote_now'
                    )
                    OR lower(COALESCE(lrd.decision, '')) LIKE 'exclude_%'
                    OR lower(COALESCE(lrd.decision, '')) LIKE 'suppress_%'
                )
          )
          AND NOT (
                dqi.issue_type = 'duplicate_email'
                AND (
                    lower(COALESCE(lrd.decision, '')) IN (
                        'already_contacted',
                        'hold_duplicate_cluster',
                        'hold_entity_mismatch',
                        'keep_held',
                        'keep_distinct_target',
                        'promote_from_hold',
                        'promote_now'
                    )
                    OR lower(COALESCE(lrd.decision, '')) LIKE 'exclude_%'
                    OR lower(COALESCE(lrd.decision, '')) LIKE 'suppress_%'
                )
          )
        ORDER BY
            priority_score DESC,
            COALESCE(ll.updated, '') DESC,
            dqi.lead_id ASC;

        DROP VIEW IF EXISTS leadops_v_contacted_business_variants;
        CREATE VIEW leadops_v_contacted_business_variants AS
        WITH contacted_leads AS (
            SELECT
                l.lead_id,
                l.name,
                l.email,
                l.email_domain,
                l.website,
                l.website_domain,
                cs.overall_contact_state AS outreach_status,
                l.last_outreach_event_at
            FROM leadops_leads l
            JOIN leadops_v_outreach_contact_state cs
              ON cs.lead_id = l.lead_id
            WHERE COALESCE(cs.overall_contact_state, 'uncontacted') IN ('contacted', 'bounced', 'replied', 'opt-out')
        ),
        variant_keys AS (
            SELECT
                cl.lead_id,
                cl.name,
                cl.outreach_status,
                cl.last_outreach_event_at,
                'website_domain' AS key_type,
                lower(COALESCE(cl.website_domain, '')) AS match_key
            FROM contacted_leads cl
            WHERE COALESCE(cl.website_domain, '') <> ''

            UNION ALL

            SELECT
                cl.lead_id,
                cl.name,
                cl.outreach_status,
                cl.last_outreach_event_at,
                'email_domain' AS key_type,
                lower(COALESCE(cl.email_domain, '')) AS match_key
            FROM contacted_leads cl
            WHERE COALESCE(cl.email_domain, '') <> ''
              AND lower(COALESCE(cl.email_domain, '')) NOT IN (
                  'gmail.com', 'outlook.com', 'hotmail.com', 'yahoo.com', 'aol.com',
                  'icloud.com', 'me.com', 'mac.com', 'live.com', 'msn.com',
                  'protonmail.com', 'pm.me'
              )
              AND lower(COALESCE(cl.email_domain, '')) NOT LIKE '% (%'

            UNION ALL

            SELECT
                cl.lead_id,
                cl.name,
                cl.outreach_status,
                cl.last_outreach_event_at,
                'alias' AS key_type,
                lower(COALESCE(ea.alias_compact, '')) AS match_key
            FROM contacted_leads cl
            JOIN leadops_entity_aliases ea
              ON ea.lead_id = cl.lead_id
            WHERE COALESCE(ea.alias_compact, '') <> ''
        )
        SELECT
            lead_id,
            name,
            outreach_status,
            last_outreach_event_at,
            key_type,
            match_key
        FROM variant_keys
        WHERE COALESCE(match_key, '') <> '';

        DROP VIEW IF EXISTS leadops_v_send_now_contact_variant_risk;
        CREATE VIEW leadops_v_send_now_contact_variant_risk AS
        WITH candidate_keys AS (
            SELECT
                s.lead_id,
                s.name,
                s.email,
                s.website,
                s.entity_match_confidence,
                s.entity_match_score,
                s.entity_match_promoted,
                'website_domain' AS key_type,
                lower(COALESCE(s.website_domain, '')) AS match_key
            FROM leadops_v_send_now s
            WHERE COALESCE(s.website_domain, '') <> ''

            UNION ALL

            SELECT
                s.lead_id,
                s.name,
                s.email,
                s.website,
                s.entity_match_confidence,
                s.entity_match_score,
                s.entity_match_promoted,
                'email_domain' AS key_type,
                lower(COALESCE(s.email_domain, '')) AS match_key
            FROM leadops_v_send_now s
            WHERE COALESCE(s.email_domain, '') <> ''
              AND lower(COALESCE(s.email_domain, '')) NOT IN (
                  'gmail.com', 'outlook.com', 'hotmail.com', 'yahoo.com', 'aol.com',
                  'icloud.com', 'me.com', 'mac.com', 'live.com', 'msn.com',
                  'protonmail.com', 'pm.me'
              )
              AND lower(COALESCE(s.email_domain, '')) NOT LIKE '% (%'

            UNION ALL

            SELECT
                s.lead_id,
                s.name,
                s.email,
                s.website,
                s.entity_match_confidence,
                s.entity_match_score,
                s.entity_match_promoted,
                'alias' AS key_type,
                lower(COALESCE(ea.alias_compact, '')) AS match_key
            FROM leadops_v_send_now s
            JOIN leadops_entity_aliases ea
              ON ea.lead_id = s.lead_id
            WHERE COALESCE(ea.alias_compact, '') <> ''
        ),
        joined AS (
            SELECT
                ck.lead_id,
                ck.name,
                ck.email,
                ck.website,
                ck.entity_match_confidence,
                ck.entity_match_score,
                ck.entity_match_promoted,
                cbv.key_type AS matched_key_type,
                cbv.match_key,
                cbv.lead_id AS contacted_lead_id,
                cbv.name AS contacted_lead_name,
                cbv.outreach_status AS contacted_outreach_status,
                cbv.last_outreach_event_at AS contacted_last_event_at
            FROM candidate_keys ck
            JOIN leadops_v_contacted_business_variants cbv
              ON cbv.match_key = ck.match_key
             AND cbv.lead_id <> ck.lead_id
        )
        SELECT
            lead_id,
            name,
            email,
            website,
            entity_match_confidence,
            entity_match_score,
            entity_match_promoted,
            COUNT(*) AS matching_variant_rows,
            COUNT(DISTINCT contacted_lead_id) AS matching_contacted_leads,
            group_concat(DISTINCT matched_key_type) AS matched_key_types,
            group_concat(DISTINCT match_key) AS matched_keys,
            group_concat(DISTINCT contacted_lead_id) AS contacted_lead_ids,
            group_concat(DISTINCT contacted_lead_name) AS contacted_lead_names,
            group_concat(DISTINCT contacted_outreach_status) AS contacted_statuses,
            MAX(COALESCE(contacted_last_event_at, '')) AS latest_contacted_event_at
        FROM joined
        GROUP BY
            lead_id,
            name,
            email,
            website,
            entity_match_confidence,
            entity_match_score,
            entity_match_promoted
        ORDER BY
            matching_contacted_leads DESC,
            latest_contacted_event_at DESC,
            lead_id ASC;
        """
    )
    ensure_table_columns(
        conn,
        "leadops_import_runs",
        {
            "mode": "TEXT",
            "source_fingerprint_json": "TEXT",
            "change_summary_json": "TEXT",
            "profile_parse_mode": "TEXT",
            "deep_index_mode": "TEXT",
        },
    )
    ensure_table_columns(
        conn,
        "leadops_leads",
        {
            "index_status": "TEXT",
            "index_outreach_status": "TEXT",
            "reconciled_status_reason": "TEXT",
            "last_outreach_event_at": "TEXT",
            "last_outreach_channel": "TEXT",
        },
    )
    ensure_table_columns(
        conn,
        "leadops_profiles",
        {
            "business_overview": "TEXT",
            "service_offerings": "TEXT",
            "target_customers": "TEXT",
            "differentiators": "TEXT",
            "contact_decision_makers": "TEXT",
            "online_presence": "TEXT",
            "market_position": "TEXT",
            "opportunity_assessment": "TEXT",
            "disqualification_rationale": "TEXT",
            "lead_metadata": "TEXT",
            "website_presence": "TEXT",
            "audit_highlights": "TEXT",
            "security_trust": "TEXT",
            "ux_conversion": "TEXT",
            "performance_tech": "TEXT",
            "google_business_profile": "TEXT",
            "social_presence": "TEXT",
            "sources": "TEXT",
            "contact_information": "TEXT",
            "outreach_section": "TEXT",
        },
    )


def ensure_table_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, type_sql in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {type_sql}")


def clear_leadops_tables(conn: sqlite3.Connection, *, include_deep_index: bool) -> None:
    tables = [
        "leadops_drafts",
        "leadops_draft_provenance",
        "leadops_business_facts",
        "leadops_mailbox_snapshots",
        "leadops_bounce_followup_queue",
        "leadops_suppression_registry",
        "leadops_bounce_events",
        "leadops_research_tasks",
        "leadops_audit_findings",
        "leadops_audit_runs",
        "leadops_send_suppressions",
        "leadops_entity_promotions",
        "leadops_entity_aliases",
        "leadops_entity_match",
        "leadops_entity_cluster_members",
        "leadops_entity_clusters",
        "leadops_review_decisions",
        "leadops_opt_outs",
        "leadops_outreach_events",
        "leadops_contacts",
        "leadops_missing_fields",
    ]
    if include_deep_index:
        tables = [
            "leadops_deep_index_file_state",
            "leadops_vector_index_queue",
            "leadops_search_documents",
            "leadops_search_fts",
            "leadops_entity_edges",
            "leadops_evidence_artifacts",
            *tables,
        ]
    for table in tables:
        conn.execute(f"DELETE FROM {table}")


def clear_outreach_sync_tables(conn: sqlite3.Connection) -> None:
    for table in (
        "leadops_drafts",
        "leadops_draft_provenance",
        "leadops_mailbox_snapshots",
        "leadops_bounce_followup_queue",
        "leadops_suppression_registry",
        "leadops_bounce_events",
        "leadops_opt_outs",
        "leadops_outreach_events",
    ):
        conn.execute(f"DELETE FROM {table}")


def clear_override_sync_tables(conn: sqlite3.Connection) -> None:
    for table in (
        "leadops_business_facts",
        "leadops_send_suppressions",
        "leadops_entity_promotions",
        "leadops_entity_aliases",
        "leadops_entity_match",
        "leadops_entity_cluster_members",
        "leadops_entity_clusters",
        "leadops_review_decisions",
    ):
        conn.execute(f"DELETE FROM {table}")


def clear_audit_sync_tables(conn: sqlite3.Connection) -> None:
    for table in (
        "leadops_research_tasks",
        "leadops_audit_findings",
        "leadops_audit_runs",
    ):
        conn.execute(f"DELETE FROM {table}")


def clear_derived_sync_tables(conn: sqlite3.Connection) -> None:
    for table in (
        "leadops_missing_fields",
    ):
        conn.execute(f"DELETE FROM {table}")


def insert_leads(conn: sqlite3.Connection, leads: list[LeadRow]) -> None:
    conn.executemany(
        """
        INSERT INTO leadops_leads (
            lead_id, name, batch, status, index_status, outreach_status, index_outreach_status,
            reconciled_status_reason, last_outreach_event_at, last_outreach_channel, contact_path, contact_search,
            email, email_domain, phone, website, website_domain, contact_form, social_media,
            website_status, social_checked, source, disqualified, updated, profile_path, raw_index_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(lead_id) DO UPDATE SET
            name=excluded.name,
            batch=excluded.batch,
            status=excluded.status,
            index_status=excluded.index_status,
            outreach_status=excluded.outreach_status,
            index_outreach_status=excluded.index_outreach_status,
            reconciled_status_reason=excluded.reconciled_status_reason,
            last_outreach_event_at=excluded.last_outreach_event_at,
            last_outreach_channel=excluded.last_outreach_channel,
            contact_path=excluded.contact_path,
            contact_search=excluded.contact_search,
            email=excluded.email,
            email_domain=excluded.email_domain,
            phone=excluded.phone,
            website=excluded.website,
            website_domain=excluded.website_domain,
            contact_form=excluded.contact_form,
            social_media=excluded.social_media,
            website_status=excluded.website_status,
            social_checked=excluded.social_checked,
            source=excluded.source,
            disqualified=excluded.disqualified,
            updated=excluded.updated,
            profile_path=excluded.profile_path,
            raw_index_json=excluded.raw_index_json
        """,
        [
            (
                lead.lead_id,
                lead.name,
                lead.batch,
                lead.status,
                lead.status,
                lead.outreach_status,
                lead.outreach_status,
                "",
                "",
                "",
                lead.contact_path,
                lead.contact_search,
                lead.email,
                lead.email_domain,
                lead.phone,
                lead.website,
                lead.website_domain,
                lead.contact_form,
                lead.social_media,
                lead.website_status,
                lead.social_checked,
                lead.source,
                lead.disqualified,
                lead.updated,
                lead.profile_path,
                lead.raw_index_json,
            )
            for lead in leads
        ],
    )


def prune_removed_leads(conn: sqlite3.Connection, active_lead_ids: set[int]) -> int:
    existing_ids = {int(row[0]) for row in conn.execute("SELECT lead_id FROM leadops_leads")}
    removed_ids = sorted(existing_ids - active_lead_ids)
    if not removed_ids:
        return 0
    placeholders = ",".join("?" for _ in removed_ids)
    conn.execute(f"DELETE FROM leadops_profile_import_state WHERE lead_id IN ({placeholders})", removed_ids)
    conn.execute(f"DELETE FROM leadops_profile_outreach_log_events WHERE lead_id IN ({placeholders})", removed_ids)
    conn.execute(f"DELETE FROM leadops_profiles WHERE lead_id IN ({placeholders})", removed_ids)
    conn.execute(f"DELETE FROM leadops_deep_index_file_state WHERE lead_id IN ({placeholders})", removed_ids)
    conn.execute(f"DELETE FROM leadops_evidence_artifacts WHERE lead_id IN ({placeholders})", removed_ids)
    conn.execute(f"DELETE FROM leadops_search_documents WHERE lead_id IN ({placeholders})", removed_ids)
    conn.execute(f"DELETE FROM leadops_vector_index_queue WHERE lead_id IN ({placeholders})", removed_ids)
    conn.execute(f"DELETE FROM leadops_entity_edges WHERE src_lead_id IN ({placeholders}) OR dst_lead_id IN ({placeholders})", (*removed_ids, *removed_ids))
    conn.execute(f"DELETE FROM leadops_leads WHERE lead_id IN ({placeholders})", removed_ids)
    return len(removed_ids)


def sync_index_contacts(conn: sqlite3.Connection, leads: list[LeadRow]) -> int:
    conn.execute("DELETE FROM leadops_contacts")
    rows = []
    seen_keys: set[tuple[int, str, str]] = set()

    def add_row(
        lead_id: int,
        contact_type: str,
        value: object | None,
        normalized_value: object | None,
        label: str | None,
        is_primary: int,
        source: str,
    ) -> None:
        text = norm(value)
        normalized = norm(normalized_value)
        if not lead_id or not text or not normalized:
            return
        key = (lead_id, contact_type, normalized)
        if key in seen_keys:
            return
        seen_keys.add(key)
        rows.append((lead_id, contact_type, text, normalized, label, is_primary, source))

    for lead in leads:
        if lead.email:
            add_row(lead.lead_id, "email", lead.email, normalize_email(lead.email), "primary", 1, "index.csv")
        if lead.phone:
            add_row(lead.lead_id, "phone", lead.phone, re.sub(r"\D+", "", lead.phone), "primary", 1, "index.csv")
        if lead.website:
            add_row(lead.lead_id, "website", lead.website, lead.website_domain, "primary", 1, "index.csv")
        if lead.contact_form:
            add_row(lead.lead_id, "contact_form", lead.contact_form, lead.contact_form, "primary", 1, "index.csv")

    for lead_id, relative_path, field_confidence in iter_enrichment_records():
        for field, contact_type in ENRICHMENT_DIRECT_CONTACT_FIELDS.items():
            meta = field_confidence.get(field)
            if not isinstance(meta, dict) or not enrichment_should_accept(meta):
                continue
            if not enrichment_value_is_usable(field, meta.get("value"), field_confidence):
                continue
            add_row(
                lead_id,
                contact_type,
                meta.get("value"),
                normalize_enrichment_contact_value(contact_type, meta.get("value")),
                field,
                1,
                f"audit_enrichment:{relative_path}",
            )
        for field in ENRICHMENT_SOCIAL_FIELDS:
            meta = field_confidence.get(field)
            if not isinstance(meta, dict) or not enrichment_should_accept(meta):
                continue
            if not enrichment_social_ok(field, meta.get("value")):
                continue
            social_value = enrichment_social_value(meta.get("value"))
            add_row(
                lead_id,
                "social",
                social_value,
                normalize_enrichment_contact_value("social", social_value),
                field,
                0,
                f"audit_enrichment:{relative_path}",
            )
    if rows:
        conn.executemany(
            """
            INSERT INTO leadops_contacts (
                lead_id, contact_type, value, normalized_value, label, is_primary, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def apply_enrichment_profile_backfills(conn: sqlite3.Connection) -> int:
    profile_rows = {
        int(row[0]): {"address": norm(row[1]), "business_overview": norm(row[2]), "google_business_profile": norm(row[3])}
        for row in conn.execute(
            """
            SELECT lead_id, address, business_overview, google_business_profile
            FROM leadops_profiles
            """
        )
    }
    touched = 0

    for lead_id, _, field_confidence in iter_enrichment_records():
        row = profile_rows.get(lead_id)
        if not row:
            continue
        changed = False
        for field, column in ENRICHMENT_DIRECT_PROFILE_MAP.items():
            meta = field_confidence.get(field)
            if not isinstance(meta, dict) or not enrichment_should_accept(meta):
                continue
            value = norm(meta.get("value"))
            if not value:
                continue
            usable = enrichment_value_is_usable(field, value, field_confidence)
            if not usable:
                if row.get(column) == value:
                    conn.execute(f"UPDATE leadops_profiles SET {column} = '' WHERE lead_id = ?", (lead_id,))
                    row[column] = ""
                    changed = True
                continue
            if field in ENRICHMENT_SOCIAL_FIELDS and not enrichment_social_ok(field, value):
                continue
            if row.get(column):
                continue
            row[column] = value
            conn.execute(f"UPDATE leadops_profiles SET {column} = ? WHERE lead_id = ?", (value, lead_id))
            changed = True
        if changed:
            touched += 1
    return touched


def load_profile_import_state(conn: sqlite3.Connection) -> dict[int, dict[str, object]]:
    rows: dict[int, dict[str, object]] = {}
    for row in conn.execute(
        """
        SELECT lead_id, profile_path, file_mtime_ns, file_size, content_sha1, parse_version, missing_profile
        FROM leadops_profile_import_state
        """
    ):
        rows[int(row[0])] = {
            "profile_path": norm(row[1]),
            "file_mtime_ns": int(row[2] or 0),
            "file_size": int(row[3] or 0),
            "content_sha1": norm(row[4]),
            "parse_version": norm(row[5]),
            "missing_profile": int(row[6] or 0),
        }
    return rows


def load_deep_index_file_state(conn: sqlite3.Connection) -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for row in conn.execute(
        """
        SELECT relative_path, lead_id, file_mtime_ns, file_size, content_sha1, parse_version, artifact_group, artifact_kind
        FROM leadops_deep_index_file_state
        """
    ):
        rows[norm(row[0])] = {
            "lead_id": int(row[1] or 0),
            "file_mtime_ns": int(row[2] or 0),
            "file_size": int(row[3] or 0),
            "content_sha1": norm(row[4]),
            "parse_version": norm(row[5]),
            "artifact_group": norm(row[6]),
            "artifact_kind": norm(row[7]),
        }
    return rows


def sync_profiles_and_profile_events(
    conn: sqlite3.Connection,
    leads: list[LeadRow],
    progress_started: float | None = None,
) -> tuple[int, int, int]:
    profiles_inserted = 0
    profile_events_inserted = 0
    profile_rows = []
    profile_event_rows = []
    profile_state_rows = []
    delete_lead_ids: set[int] = set()
    profile_state = load_profile_import_state(conn)
    unchanged_profiles = 0
    hash_checked_profiles = 0
    missing_profiles = 0
    if progress_started is not None:
        log_progress("Building profile file map", progress_started)
    profile_file_map = build_profile_file_map()
    if progress_started is not None:
        log_progress(f"Profile file map ready with {len(profile_file_map)} paths", progress_started)

    for index, lead in enumerate(leads, start=1):
        profile_file = REPO_ROOT / lead.profile_path if lead.profile_path else None
        if not profile_file or not profile_file.exists():
            profile_file = profile_file_map.get(lead.lead_id)
        if not profile_file or not profile_file.exists():
            previous = profile_state.get(lead.lead_id)
            if previous and not int(previous.get("missing_profile") or 0):
                delete_lead_ids.add(lead.lead_id)
            missing_profiles += 1
            profile_state_rows.append(
                (
                    lead.lead_id,
                    "",
                    0,
                    0,
                    "",
                    PROFILE_PARSE_VERSION,
                    datetime.now().isoformat(timespec="seconds"),
                    1,
                )
            )
            continue
        profile_rel = str(profile_file.relative_to(REPO_ROOT))
        stat = profile_file.stat()
        previous = profile_state.get(lead.lead_id)
        if previous:
            fast_unchanged = (
                norm(previous.get("profile_path")) == profile_rel
                and int(previous.get("file_mtime_ns") or 0) == int(stat.st_mtime_ns)
                and int(previous.get("file_size") or 0) == int(stat.st_size)
                and norm(previous.get("parse_version")) == PROFILE_PARSE_VERSION
                and int(previous.get("missing_profile") or 0) == 0
            )
            if fast_unchanged:
                unchanged_profiles += 1
                continue
            if (
                norm(previous.get("profile_path")) == profile_rel
                and int(previous.get("file_size") or 0) == int(stat.st_size)
                and norm(previous.get("parse_version")) == PROFILE_PARSE_VERSION
                and int(previous.get("missing_profile") or 0) == 0
            ):
                hash_checked_profiles += 1
                file_sha1 = sha1_file(profile_file)
                if file_sha1 == norm(previous.get("content_sha1")):
                    unchanged_profiles += 1
                    profile_state_rows.append(
                        (
                            lead.lead_id,
                            profile_rel,
                            int(stat.st_mtime_ns),
                            int(stat.st_size),
                            file_sha1,
                            PROFILE_PARSE_VERSION,
                            datetime.now().isoformat(timespec="seconds"),
                            0,
                        )
                    )
                    continue
        delete_lead_ids.add(lead.lead_id)
        file_sha1 = sha1_file(profile_file)
        parsed = parse_profile_markdown(profile_file)
        raw_markdown = str(parsed["raw_markdown"])
        profile_rows.append(
            (
                lead.lead_id,
                parsed["title"],
                parsed["address"],
                parsed["naics"],
                parsed["distance_miles"],
                parsed["decision_maker"],
                parsed["last_updated"],
                parsed["snapshot"],
                parsed["observations"],
                parsed["business_overview"],
                parsed["service_offerings"],
                parsed["target_customers"],
                parsed["differentiators"],
                parsed["contact_decision_makers"],
                parsed["online_presence"],
                parsed["market_position"],
                parsed["opportunity_assessment"],
                parsed["disqualification_rationale"],
                parsed["lead_metadata"],
                parsed["website_presence"],
                parsed["audit_highlights"],
                parsed["security_trust"],
                parsed["ux_conversion"],
                parsed["performance_tech"],
                parsed["google_business_profile"],
                parsed["social_presence"],
                parsed["sources"],
                parsed["contact_information"],
                parsed["outreach_section"],
                parsed["outreach_angle"],
                parsed["website_audit"],
                parsed["next_steps"],
                parsed["evidence"],
                parsed["outreach_log_md"],
                raw_markdown,
                json.dumps(parsed["kv"], ensure_ascii=True, sort_keys=True),
                json.dumps(parsed["sections"], ensure_ascii=True, sort_keys=True),
            )
        )
        profile_state_rows.append(
            (
                lead.lead_id,
                str(profile_file.relative_to(REPO_ROOT)),
                int(stat.st_mtime_ns),
                int(stat.st_size),
                file_sha1,
                PROFILE_PARSE_VERSION,
                datetime.now().isoformat(timespec="seconds"),
                0,
            )
        )
        profiles_inserted += 1

        for event in parse_outreach_log_table(str(parsed["outreach_log_md"])):
            notes = event["notes"]
            profile_event_rows.append(
                (
                    lead.lead_id,
                    parse_isoish_datetime(event["date"]),
                    norm(event["channel"]),
                    norm(event["status"]),
                    extract_first_email(notes),
                    extract_subject(notes),
                    notes,
                    json.dumps(event, ensure_ascii=True, sort_keys=True),
                )
            )

        if progress_started is not None and index % 500 == 0:
            log_progress(
                f"Processed {index}/{len(leads)} leads into {profiles_inserted} changed profiles",
                progress_started,
            )

    if delete_lead_ids:
        placeholders = ",".join("?" for _ in delete_lead_ids)
        delete_ids = sorted(delete_lead_ids)
        conn.execute(f"DELETE FROM leadops_profile_outreach_log_events WHERE lead_id IN ({placeholders})", delete_ids)
        conn.execute(f"DELETE FROM leadops_profiles WHERE lead_id IN ({placeholders})", delete_ids)

    if profile_rows:
        if progress_started is not None:
            log_progress(f"Bulk inserting {len(profile_rows)} changed profile rows", progress_started)
        conn.executemany(
            """
            INSERT INTO leadops_profiles (
                lead_id, title, address, naics, distance_miles, decision_maker, last_updated,
                snapshot, observations, business_overview, service_offerings, target_customers, differentiators,
                contact_decision_makers, online_presence, market_position, opportunity_assessment, disqualification_rationale,
                lead_metadata, website_presence, audit_highlights,
                security_trust, ux_conversion, performance_tech, google_business_profile, social_presence,
                sources, contact_information, outreach_section, outreach_angle, website_audit, next_steps, evidence,
                outreach_log_md, raw_markdown, kv_json, sections_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            profile_rows,
        )

    if profile_event_rows:
        if progress_started is not None:
            log_progress(f"Bulk inserting {len(profile_event_rows)} changed profile outreach-log rows", progress_started)
        conn.executemany(
            """
            INSERT INTO leadops_profile_outreach_log_events (
                lead_id, event_date, channel, status, recipient, subject, notes, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            profile_event_rows,
        )
        profile_events_inserted = len(profile_event_rows)

    if profile_state_rows:
        conn.executemany(
            """
            INSERT INTO leadops_profile_import_state (
                lead_id, profile_path, file_mtime_ns, file_size, content_sha1, parse_version, imported_at, missing_profile
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(lead_id) DO UPDATE SET
                profile_path=excluded.profile_path,
                file_mtime_ns=excluded.file_mtime_ns,
                file_size=excluded.file_size,
                content_sha1=excluded.content_sha1,
                parse_version=excluded.parse_version,
                imported_at=excluded.imported_at,
                missing_profile=excluded.missing_profile
            """,
            profile_state_rows,
        )

    if progress_started is not None:
        log_progress(
            f"Profile sync summary: changed={profiles_inserted}, unchanged={unchanged_profiles}, hash_checked={hash_checked_profiles}, missing={missing_profiles}, deleted={len(delete_lead_ids)}",
            progress_started,
        )

    return profiles_inserted, 0, profile_events_inserted


def parse_contact_log_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if stripped.startswith("| Date ") or stripped.startswith("| ---") or stripped == "|  |  |  |  |  |  |":
            continue
        parts = [part.strip() for part in stripped.strip("|").split("|")]
        if len(parts) != 6:
            continue
        rows.append(
            {
                "date": parts[0],
                "lead": parts[1],
                "batch": parts[2],
                "channel": normalize_contact_log_channel(parts[3]),
                "status": parts[4],
                "notes": parts[5],
            }
        )
    return rows


def parse_markdown_table(path: Path, expected_columns: int) -> list[list[str]]:
    if not path.exists():
        return []
    rows: list[list[str]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if stripped.startswith("| ---"):
            continue
        parts = [part.strip() for part in stripped.strip("|").split("|")]
        if len(parts) != expected_columns:
            continue
        rows.append(parts)
    return rows


def infer_lead_id_from_name(raw_name: str, unique_name_map: dict[str, int]) -> int | None:
    candidate = norm(raw_name)
    if not candidate:
        return None
    match = LEAD_ID_PREFIX_RE.match(candidate)
    if match:
        return int(match.group(1))
    return unique_name_map.get(low(candidate))


def extract_subject(text: str) -> str:
    match = SUBJECT_RE.search(text)
    return norm(match.group(1)) if match else ""


def extract_first_email(text: str) -> str:
    emails = EMAIL_RE.findall(text or "")
    return normalize_email(emails[0]) if emails else ""


def find_latest_mailbox_snapshot() -> Path | None:
    files = sorted(TMP_DIR.glob("hostinger_mailbox_counts_*.json"))
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def insert_outreach_events(
    conn: sqlite3.Connection,
    leads: list[LeadRow],
    contact_log_path: Path,
    sent_items_path: Path,
    delivered_path: Path,
) -> int:
    unique_email_map = build_unique_email_map(leads)
    unique_name_map = build_unique_name_map(leads)
    valid_lead_ids = {lead.lead_id for lead in leads}
    events = []

    for row in parse_contact_log_rows(contact_log_path):
        lead_id = infer_lead_id_from_name(row["lead"], unique_name_map)
        recipient = extract_first_email(row["notes"])
        if not lead_id and recipient:
            lead_id = unique_email_map.get(recipient)
        if lead_id not in valid_lead_ids:
            lead_id = None
        subject = extract_subject(row["notes"])
        events.append(
            (
                lead_id,
                parse_isoish_datetime(row["date"]),
                row["lead"],
                row["batch"],
                row["channel"],
                row["status"],
                subject,
                recipient,
                row["notes"],
                "contact-log.md",
                json.dumps(row, ensure_ascii=True, sort_keys=True),
            )
        )

    for payload, source_name, default_status in (
        (load_json(sent_items_path), "sent-items.json", "sent"),
        (load_json(delivered_path), "delivered-emails.json", "delivered"),
    ):
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            recipient = normalize_email(item.get("email"))
            lead_id = unique_email_map.get(recipient)
            if lead_id not in valid_lead_ids:
                lead_id = None
            events.append(
                (
                    lead_id,
                    parse_isoish_datetime(item.get("when")),
                    "",
                    "",
                    "email",
                    default_status,
                    norm(item.get("subject")),
                    recipient,
                    "",
                    source_name,
                    json.dumps(item, ensure_ascii=True, sort_keys=True),
                )
            )

    if events:
        conn.executemany(
            """
            INSERT INTO leadops_outreach_events (
                lead_id, event_date, lead_name, batch, channel, status, subject, recipient, notes, source, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            events,
        )
    return len(events)


def insert_drafts(conn: sqlite3.Connection, leads: list[LeadRow], drafts_path: Path) -> int:
    payload = load_json(drafts_path)
    if not isinstance(payload, dict):
        return 0
    drafts = payload.get("value")
    if not isinstance(drafts, list):
        return 0
    unique_email_map = build_unique_email_map(leads)
    rows = []
    for item in drafts:
        if not isinstance(item, dict):
            continue
        recipient = normalize_email(item.get("to"))
        rows.append(
            (
                str(item.get("uid", "")),
                unique_email_map.get(recipient),
                recipient,
                norm(item.get("subject")),
                parse_isoish_datetime(item.get("date")),
                norm(item.get("href")),
                norm(item.get("body")),
                json.dumps(item, ensure_ascii=True, sort_keys=True),
            )
        )
    if rows:
        conn.executemany(
            """
            INSERT INTO leadops_drafts (
                uid, lead_id, recipient, subject, draft_date, mailbox_href, body_text, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def insert_opt_outs(conn: sqlite3.Connection, leads: list[LeadRow], opt_out_path: Path) -> int:
    unique_email_map = build_unique_email_map(leads)
    unique_name_map = build_unique_name_map(leads)
    rows = []
    table_rows = parse_markdown_table(opt_out_path, expected_columns=6)
    for parts in table_rows:
        if parts[0].lower() == "date":
            continue
        lead_ref = parts[1]
        recipient = normalize_email(parts[2])
        reply_from = normalize_email(parts[3])
        subject = parts[4]
        notes = parts[5]
        lead_id = infer_lead_id_from_name(lead_ref, unique_name_map)
        if not lead_id and recipient:
            lead_id = unique_email_map.get(recipient)
        rows.append(
            (
                lead_id,
                parse_isoish_datetime(parts[0]),
                lead_ref,
                recipient,
                reply_from,
                subject,
                notes,
                "opt-out-log.md",
                json.dumps(
                    {
                        "date": parts[0],
                        "lead": lead_ref,
                        "recipient": recipient,
                        "reply_from": reply_from,
                        "subject": subject,
                        "notes": notes,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                ),
            )
        )
    if rows:
        conn.executemany(
            """
            INSERT INTO leadops_opt_outs (
                lead_id, opt_out_date, lead_ref, recipient, reply_from, subject, notes, source, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def insert_mailbox_snapshots(conn: sqlite3.Connection) -> int:
    latest = find_latest_mailbox_snapshot()
    if not latest:
        return 0
    payload = load_json(latest)
    if not isinstance(payload, dict):
        return 0
    snapshot_at = parse_isoish_datetime(payload.get("snapshot_at") or payload.get("date"))
    mailboxes = payload.get("mailboxes", [])
    if not isinstance(mailboxes, list):
        return 0
    rows = []
    for item in mailboxes:
        if not isinstance(item, dict):
            continue
        rows.append(
            (
                snapshot_at,
                norm(item.get("mailbox")),
                int(item.get("count") or 0),
                f"mailbox-snapshot:{latest.name}",
                json.dumps(item, ensure_ascii=True, sort_keys=True),
            )
        )
    if rows:
        conn.executemany(
            """
            INSERT INTO leadops_mailbox_snapshots (
                snapshot_at, mailbox_name, item_count, source, raw_payload
            ) VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def insert_missing_fields(conn: sqlite3.Connection, report_path: Path) -> int:
    rows = []
    for item in parse_missing_fields_report(report_path):
        rows.append(
            (
                item["lead_id"],
                str(item["name"]),
                ", ".join(item["missing_fields"]),
                len(item["missing_fields"]),
                str(item["path"]),
                "missing-fields.md",
                json.dumps(item, ensure_ascii=True, sort_keys=True),
            )
        )
    if rows:
        conn.executemany(
            """
            INSERT INTO leadops_missing_fields (
                lead_id, name, missing_fields, missing_field_count, path, source, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def insert_send_suppressions(conn: sqlite3.Connection, path: Path) -> int:
    rows = []
    for item in parse_send_suppressions(path):
        rows.append(
            (
                int(item["lead_id"]),
                str(item["reason"]),
                "leadops-send-suppressions.json",
                json.dumps(item, ensure_ascii=True, sort_keys=True),
            )
        )
    if rows:
        conn.executemany(
            """
            INSERT INTO leadops_send_suppressions (
                lead_id, reason, source, raw_payload
            ) VALUES (?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def insert_entity_aliases(conn: sqlite3.Connection, path: Path) -> int:
    rows = []
    for lead_id, aliases in parse_entity_aliases(path).items():
        for alias in aliases:
            rows.append(
                (
                    lead_id,
                    alias,
                    json.dumps(sorted(tokenize_text(alias)), ensure_ascii=True),
                    compact_entity_text(alias),
                    "leadops-entity-aliases.json",
                )
            )
    if rows:
        conn.executemany(
            """
            INSERT INTO leadops_entity_aliases (
                lead_id, alias, alias_tokens_json, alias_compact, source
            ) VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def insert_entity_promotions(conn: sqlite3.Connection, path: Path) -> int:
    rows = []
    for item in parse_entity_promotions(path):
        rows.append(
            (
                int(item["lead_id"]),
                str(item["reason"]),
                "leadops-entity-promotions.json",
            )
        )
    if rows:
        conn.executemany(
            """
            INSERT INTO leadops_entity_promotions (
                lead_id, reason, source
            ) VALUES (?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def insert_review_decisions(conn: sqlite3.Connection, path: Path) -> int:
    rows = []
    for item in parse_review_decisions(path):
        rows.append(
            (
                int(item["lead_id"]),
                str(item["decision"]),
                str(item.get("reason", "")),
                str(item.get("source_file", "")),
                "leadops-review-decisions.csv",
                json.dumps(item, ensure_ascii=True, sort_keys=True),
            )
        )
    if rows:
        conn.executemany(
            """
            INSERT INTO leadops_review_decisions (
                lead_id, decision, reason, source_file, source, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def insert_audit_runs_and_findings(conn: sqlite3.Connection) -> tuple[int, int]:
    run_rows, _ = parse_diamond_audit_exports(AUDIT_EXPORTS_DIR)
    inserted_runs = 0
    inserted_findings = 0
    if not run_rows:
        return inserted_runs, inserted_findings

    unique_email_map = {
        normalize_email(email): int(lead_id)
        for lead_id, email in conn.execute("SELECT lead_id, COALESCE(email, '') FROM leadops_leads")
        if normalize_email(email)
    }

    for run in run_rows:
        cur = conn.execute(
            """
            INSERT INTO leadops_audit_runs (
                source_file, source_kind, audit_date, lead_range, criteria_raw,
                dedupe_list_path, summary_json, recommendation, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run["source_file"],
                run["source_kind"],
                run["audit_date"],
                run["lead_range"],
                run["criteria_raw"],
                run["dedupe_list_path"],
                run["summary_json"],
                run["recommendation"],
                run["raw_payload"],
            ),
        )
        run_id = int(cur.lastrowid)
        inserted_runs += 1
        finding_rows = []
        for bucket_name, item in run["findings"]:
            lead_id = item.get("LeadID")
            if not isinstance(lead_id, int):
                lead_id = unique_email_map.get(normalize_email(item.get("Email")))
            issue_type = norm(item.get("IssueType"))
            diamond_worthy = 1 if item.get("DiamondWorthy") else 0
            verified_live = item.get("VerifiedLive")
            if isinstance(verified_live, bool):
                verified_live_value = 1 if verified_live else 0
            elif norm(verified_live):
                verified_live_value = 1 if low(verified_live) in {"yes", "true", "1"} else 0
            else:
                verified_live_value = None
            finding_rows.append(
                (
                    run_id,
                    lead_id,
                    norm(item.get("Name")),
                    normalize_email(item.get("Email")),
                    norm(item.get("Website")),
                    issue_type,
                    normalize_issue_type(issue_type),
                    norm(item.get("IssueDescription")),
                    diamond_worthy,
                    norm(item.get("DedupeStatus")),
                    norm(item.get("DedupeNote")),
                    verified_live_value,
                    norm(item.get("VerificationMethod")),
                    norm(item.get("EvidencePath")),
                    norm(item.get("Note")),
                    classify_finding_type(issue_type, item.get("DiamondWorthy")),
                    severity_from_issue(issue_type, item.get("DiamondWorthy")),
                    run["source_file"],
                    json.dumps(
                        {
                            "bucket": bucket_name,
                            "item": item,
                        },
                        ensure_ascii=True,
                        sort_keys=True,
                    ),
                )
            )
        if finding_rows:
            conn.executemany(
                """
                INSERT INTO leadops_audit_findings (
                    run_id, lead_id, lead_name_snapshot, email_snapshot, website_snapshot,
                    issue_type_raw, issue_type_norm, issue_description, diamond_worthy,
                    dedupe_status, dedupe_note, verified_live, verification_method,
                    evidence_path, note, finding_class, severity, source_file, raw_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                finding_rows,
            )
            inserted_findings += len(finding_rows)
    return inserted_runs, inserted_findings


def insert_research_tasks(conn: sqlite3.Connection) -> int:
    rows = parse_deep_audit_queue(DEEP_AUDIT_QUEUE_JSON)
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO leadops_research_tasks (
            lead_id, task_type, status, priority, source_file, website, email, batch_range, raw_payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["lead_id"],
                row["task_type"],
                row["status"],
                row["priority"],
                row["source_file"],
                row["website"],
                row["email"],
                row["task_range"],
                row["raw_payload"],
            )
            for row in rows
        ],
    )
    return len(rows)


def insert_bounce_and_suppression_detail(conn: sqlite3.Connection, leads: list[LeadRow]) -> tuple[int, int]:
    unique_email_map = build_unique_email_map(leads)
    recipient_rows, domain_rows = parse_bounce_suppression(BOUNCE_SUPPRESSION_JSON)
    suppression_rows = recipient_rows + domain_rows
    if suppression_rows:
        conn.executemany(
            """
            INSERT INTO leadops_suppression_registry (
                target_type, target_value, action, kind, reason, first_seen, last_seen,
                event_count, reason_counts_json, source_file, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["target_type"],
                    row["target_value"],
                    row["action"],
                    row["kind"],
                    row["reason"],
                    row["first_seen"],
                    row["last_seen"],
                    row["event_count"],
                    row["reason_counts_json"],
                    row["source_file"],
                    row["raw_payload"],
                )
                for row in suppression_rows
            ],
        )

    bounce_rows = parse_bounced_email_logs(ALL_BOUNCED_EMAILS_JSON, BOUNCED_EMAILS_JSON)
    if bounce_rows:
        conn.executemany(
            """
            INSERT INTO leadops_bounce_events (
                lead_id, recipient, normalized_recipient, subject, event_at, mailbox,
                bounce_type, smtp_status, source_file, source_kind, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    unique_email_map.get(row["recipient"]),
                    row["recipient"],
                    row["recipient"],
                    row["subject"],
                    row["event_at"],
                    row["mailbox"],
                    row["bounce_type"],
                    row["smtp_status"],
                    row["source_file"],
                    row["source_kind"],
                    row["raw_payload"],
                )
                for row in bounce_rows
            ],
        )
    return len(suppression_rows), len(bounce_rows)


def insert_draft_provenance(conn: sqlite3.Connection, leads: list[LeadRow]) -> int:
    unique_email_map = build_unique_email_map(leads)
    rows = parse_draft_provenance(DRAFTS_REVISED_JSON)
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO leadops_draft_provenance (
            uid, lead_id, recipient, subject, draft_date, from_addr, href,
            body_text, body_source_text, draft_variant, source_file, raw_payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["uid"],
                unique_email_map.get(row["recipient"]),
                row["recipient"],
                row["subject"],
                row["draft_date"],
                row["from_addr"],
                row["href"],
                row["body_text"],
                row["body_source_text"],
                row["draft_variant"],
                row["source_file"],
                row["raw_payload"],
            )
            for row in rows
        ],
    )
    return len(rows)


def insert_bounce_followup_queue(conn: sqlite3.Connection) -> int:
    rows = parse_bounce_followup_queue(BOUNCE_FOLLOWUP_WORKLIST_JSON)
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO leadops_bounce_followup_queue (
            recipient, company, website, bounce_date, contact_form_url, status, source_file, raw_payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["recipient"],
                row["company"],
                row["website"],
                row["bounce_date"],
                row["contact_form_url"],
                row["status"],
                row["source_file"],
                row["raw_payload"],
            )
            for row in rows
        ],
    )
    return len(rows)


def insert_business_facts(conn: sqlite3.Connection, mode: str = "full") -> int:
    rows: list[tuple[object, ...]] = []

    def add_fact(
        lead_id: int | None,
        fact_type: str,
        fact_value: str | None,
        source_kind: str,
        source_file: str,
        confidence: str,
        raw_payload: object,
        verified_at: str = "",
    ) -> None:
        value = norm(fact_value)
        if not lead_id or not value:
            return
        rows.append(
            (
                lead_id,
                fact_type,
                value,
                source_kind,
                source_file,
                confidence,
                verified_at,
                json.dumps(raw_payload, ensure_ascii=True, sort_keys=True),
            )
        )

    if mode == "full":
        conn.execute("DELETE FROM leadops_business_facts")
    elif mode == "overrides_only":
        conn.execute(
            """
            DELETE FROM leadops_business_facts
            WHERE source_kind IN ('dba_candidates', 'contact_path_snippet', 'audit_enrichment')
            """
        )

    if mode in {"full"}:
        for lead_id, kv_json in conn.execute("SELECT lead_id, kv_json FROM leadops_profiles"):
            kv = json.loads(kv_json or "{}")
            if not isinstance(kv, dict):
                continue
            source_file = "leadops_profiles.kv_json"
            add_fact(lead_id, "address", kv.get("Address"), "profile_frontmatter", source_file, "high", kv)
            add_fact(lead_id, "decision_maker", kv.get("Decision maker"), "profile_frontmatter", source_file, "medium", kv)
            add_fact(lead_id, "naics", kv.get("NAICS"), "profile_frontmatter", source_file, "high", kv)
            add_fact(lead_id, "website", kv.get("Website"), "profile_frontmatter", source_file, "high", kv)
            add_fact(lead_id, "email", kv.get("Email"), "profile_frontmatter", source_file, "high", kv)
            add_fact(lead_id, "phone", kv.get("Phone"), "profile_frontmatter", source_file, "high", kv)
            add_fact(lead_id, "contact_form", kv.get("Contact form"), "profile_frontmatter", source_file, "medium", kv)
            add_fact(lead_id, "social_media", kv.get("Social media"), "profile_frontmatter", source_file, "medium", kv)
            add_fact(lead_id, "contact_path", kv.get("Contact path"), "profile_frontmatter", source_file, "medium", kv)
            add_fact(lead_id, "contact_search", kv.get("Contact search"), "profile_frontmatter", source_file, "medium", kv)

    if mode in {"full", "overrides_only"}:
        for item in parse_dba_candidates(DBA_CANDIDATES_JSON):
            lead_id = int(item["lead_id"])
            source_file = str(item["source_file"])
            add_fact(lead_id, "alternate_brand", item.get("name"), "dba_candidates", source_file, "medium", item)
            add_fact(lead_id, "alternate_phone", item.get("phone"), "dba_candidates", source_file, "low", item)
            add_fact(lead_id, "alternate_website", item.get("website"), "dba_candidates", source_file, "low", item)
            add_fact(lead_id, "public_address", item.get("address"), "dba_candidates", source_file, "medium", item)

        for block in parse_contact_path_snippets(CONTACT_PATH_SNIPPETS_TXT):
            lead_id = int(block["lead_id"])
            source_file = str(block["source_file"])
            payload = {"lines": block["lines"]}
            for line in block["lines"]:
                stripped = line.strip()
                lower_line = stripped.lower()
                if lower_line.startswith("primary site:"):
                    add_fact(lead_id, "website", stripped.split(":", 1)[1], "contact_path_snippet", source_file, "medium", payload)
                elif lower_line.startswith("legacy domain:"):
                    add_fact(lead_id, "alternate_website", stripped.split(":", 1)[1], "contact_path_snippet", source_file, "low", payload)
                elif lower_line.startswith("contact form:"):
                    add_fact(lead_id, "contact_form", stripped.split(":", 1)[1], "contact_path_snippet", source_file, "medium", payload)
                elif lower_line.startswith("email:"):
                    add_fact(lead_id, "email", stripped.split(":", 1)[1], "contact_path_snippet", source_file, "medium", payload)
                elif lower_line.startswith("- phone:") or lower_line.startswith("phone:"):
                    add_fact(lead_id, "phone", stripped.split(":", 1)[1], "contact_path_snippet", source_file, "medium", payload)
                elif lower_line.startswith("- address:") or lower_line.startswith("address:"):
                    add_fact(lead_id, "public_address", stripped.split(":", 1)[1], "contact_path_snippet", source_file, "medium", payload)
                elif "owner" in lower_line and ":" in stripped:
                    add_fact(lead_id, "owner", stripped.split(":", 1)[1], "contact_path_snippet", source_file, "medium", payload)
                elif lower_line.startswith("- facebook:"):
                    add_fact(lead_id, "facebook_url", stripped.split(":", 1)[1], "contact_path_snippet", source_file, "low", payload)
                elif lower_line.startswith("- instagram:"):
                    add_fact(lead_id, "instagram_url", stripped.split(":", 1)[1], "contact_path_snippet", source_file, "low", payload)
                elif lower_line.startswith("- linkedin:"):
                    add_fact(lead_id, "linkedin_url", stripped.split(":", 1)[1], "contact_path_snippet", source_file, "low", payload)

        for lead_id, relative_path, field_confidence in iter_enrichment_records():
            for field, meta in field_confidence.items():
                if field in ENRICHMENT_EXCLUDED_FACT_FIELDS:
                    continue
                if not isinstance(meta, dict) or not enrichment_should_accept(meta):
                    continue
                value = meta.get("value")
                if not enrichment_value_is_usable(field, value, field_confidence):
                    continue
                if field in ENRICHMENT_SOCIAL_FIELDS and not enrichment_social_ok(field, value):
                    continue
                if isinstance(value, list):
                    cleaned_list = [norm(item) for item in value if norm(item)]
                    if not cleaned_list:
                        continue
                    fact_value: object = cleaned_list
                else:
                    fact_value = norm(value)
                    if not fact_value:
                        continue
                rows.append(
                    (
                        lead_id,
                        field,
                        json.dumps(fact_value, ensure_ascii=True, sort_keys=True)
                        if isinstance(fact_value, (dict, list))
                        else str(fact_value),
                        "audit_enrichment",
                        relative_path,
                        norm(meta.get("trustLevel")),
                        "",
                        json.dumps(meta, ensure_ascii=True, sort_keys=True),
                    )
                )

    if rows:
        conn.executemany(
            """
            INSERT INTO leadops_business_facts (
                lead_id, fact_type, fact_value, source_kind, source_file, confidence, verified_at, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def upsert_evidence_artifact(
    conn: sqlite3.Connection,
    record: dict[str, object],
    *,
    content_sha1: str,
    indexed_at: str,
) -> None:
    path = Path(str(record["path"]))
    relative_path = str(record["relative_path"])
    text_extract = extract_text_for_search(path)
    raw_payload = json.dumps(
        {
            "name": path.name,
            "relative_path": relative_path,
            "artifact_group": record["artifact_group"],
            "artifact_kind": record["artifact_kind"],
            "content_sha1": content_sha1,
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    conn.execute("DELETE FROM leadops_evidence_artifacts WHERE relative_path = ?", (relative_path,))
    conn.execute(
        """
        INSERT INTO leadops_evidence_artifacts (
            lead_id, relative_path, artifact_group, artifact_kind, file_ext, mime_family,
            size_bytes, modified_at, text_extract, source_file, raw_payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(record["lead_id"]),
            relative_path,
            str(record["artifact_group"]),
            str(record["artifact_kind"]),
            str(record["file_ext"]),
            str(record["mime_family"]),
            int(record["size_bytes"]),
            str(record["modified_at"]),
            text_extract,
            relative_path,
            raw_payload,
        ),
    )
    conn.execute(
        """
        INSERT INTO leadops_deep_index_file_state (
            relative_path, lead_id, file_mtime_ns, file_size, content_sha1, parse_version,
            artifact_group, artifact_kind, last_indexed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(relative_path) DO UPDATE SET
            lead_id=excluded.lead_id,
            file_mtime_ns=excluded.file_mtime_ns,
            file_size=excluded.file_size,
            content_sha1=excluded.content_sha1,
            parse_version=excluded.parse_version,
            artifact_group=excluded.artifact_group,
            artifact_kind=excluded.artifact_kind,
            last_indexed_at=excluded.last_indexed_at
        """,
        (
            relative_path,
            int(record["lead_id"]),
            int(record["file_mtime_ns"]),
            int(record["size_bytes"]),
            content_sha1,
            DEEP_INDEX_PARSE_VERSION,
            str(record["artifact_group"]),
            str(record["artifact_kind"]),
            indexed_at,
        ),
    )


def sync_evidence_artifacts(conn: sqlite3.Connection) -> tuple[int, int, int, int]:
    state = load_deep_index_file_state(conn)
    current_records = list(iter_all_evidence_file_records(EVIDENCE_INDEX_ROOTS))
    current_paths = {str(record["relative_path"]) for record in current_records}
    indexed_at = datetime.now().isoformat(timespec="seconds")
    changed = 0
    unchanged = 0
    hash_checked = 0

    for record in current_records:
        relative_path = str(record["relative_path"])
        previous = state.get(relative_path)
        metadata_matches = (
            previous is not None
            and int(previous["lead_id"]) == int(record["lead_id"])
            and int(previous["file_mtime_ns"]) == int(record["file_mtime_ns"])
            and int(previous["file_size"]) == int(record["size_bytes"])
            and norm(previous["parse_version"]) == DEEP_INDEX_PARSE_VERSION
            and norm(previous["artifact_group"]) == norm(record["artifact_group"])
            and norm(previous["artifact_kind"]) == norm(record["artifact_kind"])
        )
        if metadata_matches:
            unchanged += 1
            continue

        content_sha1 = sha1_file(Path(str(record["path"])))
        hash_checked += 1
        hash_matches = (
            previous is not None
            and norm(previous["content_sha1"]) == content_sha1
            and norm(previous["parse_version"]) == DEEP_INDEX_PARSE_VERSION
            and int(previous["lead_id"]) == int(record["lead_id"])
            and norm(previous["artifact_group"]) == norm(record["artifact_group"])
            and norm(previous["artifact_kind"]) == norm(record["artifact_kind"])
        )
        if hash_matches:
            conn.execute(
                """
                INSERT INTO leadops_deep_index_file_state (
                    relative_path, lead_id, file_mtime_ns, file_size, content_sha1, parse_version,
                    artifact_group, artifact_kind, last_indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(relative_path) DO UPDATE SET
                    lead_id=excluded.lead_id,
                    file_mtime_ns=excluded.file_mtime_ns,
                    file_size=excluded.file_size,
                    content_sha1=excluded.content_sha1,
                    parse_version=excluded.parse_version,
                    artifact_group=excluded.artifact_group,
                    artifact_kind=excluded.artifact_kind,
                    last_indexed_at=excluded.last_indexed_at
                """,
                (
                    relative_path,
                    int(record["lead_id"]),
                    int(record["file_mtime_ns"]),
                    int(record["size_bytes"]),
                    content_sha1,
                    DEEP_INDEX_PARSE_VERSION,
                    str(record["artifact_group"]),
                    str(record["artifact_kind"]),
                    indexed_at,
                ),
            )
            unchanged += 1
            continue

        upsert_evidence_artifact(conn, record, content_sha1=content_sha1, indexed_at=indexed_at)
        changed += 1

    deleted_paths = sorted(set(state.keys()) - current_paths)
    if deleted_paths:
        placeholders = ",".join("?" for _ in deleted_paths)
        conn.execute(f"DELETE FROM leadops_evidence_artifacts WHERE relative_path IN ({placeholders})", deleted_paths)
        conn.execute(f"DELETE FROM leadops_deep_index_file_state WHERE relative_path IN ({placeholders})", deleted_paths)

    return changed, unchanged, hash_checked, len(deleted_paths)


def insert_entity_edges(conn: sqlite3.Connection) -> int:
    rows: list[tuple[object, ...]] = []

    for cluster_id, canonical_lead_id, cluster_key in conn.execute(
        "SELECT cluster_id, canonical_lead_id, cluster_key FROM leadops_entity_clusters"
    ):
        for member_lead_id, decision in conn.execute(
            """
            SELECT ecm.lead_id, COALESCE(lrd.decision, '')
            FROM leadops_entity_cluster_members ecm
            LEFT JOIN leadops_v_latest_review_decision lrd
              ON lrd.lead_id = ecm.lead_id
            WHERE ecm.cluster_id = ?
              AND ecm.lead_id <> ?
            """,
            (cluster_id, canonical_lead_id),
        ):
            rows.append(
                (
                    member_lead_id,
                    canonical_lead_id,
                    "entity_cluster_canonical",
                    norm(cluster_key),
                    "high",
                    "entity_cluster",
                    "leadops_entity_clusters",
                    json.dumps(
                        {
                            "cluster_id": cluster_id,
                            "cluster_key": cluster_key,
                            "decision": decision,
                        },
                        ensure_ascii=True,
                        sort_keys=True,
                    ),
                )
            )

    seen_email_domains: set[tuple[int, int, str]] = set()
    for email_domain, members_json in conn.execute(
        """
        SELECT cluster_key, member_lead_ids_json
        FROM leadops_entity_clusters
        WHERE lower(COALESCE(cluster_basis, '')) = 'email_domain'
        """
    ):
        if not norm(email_domain):
            continue
        try:
            members = [int(v) for v in json.loads(members_json or "[]")]
        except Exception:
            continue
        if len(members) < 2:
            continue
        anchor = min(members)
        for lead_id in members:
            if lead_id == anchor:
                continue
            key = (lead_id, anchor, norm(email_domain))
            if key in seen_email_domains:
                continue
            seen_email_domains.add(key)
            rows.append(
                (
                    lead_id,
                    anchor,
                    "shared_email_domain",
                    norm(email_domain),
                    "medium",
                    "entity_cluster",
                    "leadops_entity_clusters",
                    json.dumps({"email_domain": email_domain, "members": members}, ensure_ascii=True, sort_keys=True),
                )
            )

    if rows:
        conn.executemany(
            """
            INSERT INTO leadops_entity_edges (
                src_lead_id, dst_lead_id, edge_type, edge_value, confidence, source_kind, source_file, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def clear_deep_index_support_tables(conn: sqlite3.Connection) -> None:
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'leadops_vector_embeddings'"
    ).fetchone():
        conn.execute("DELETE FROM leadops_vector_embeddings")
    conn.execute("DELETE FROM leadops_vector_index_queue")
    conn.execute("DELETE FROM leadops_search_fts")
    conn.execute("DELETE FROM leadops_search_documents")
    conn.execute("DELETE FROM leadops_entity_edges")


def ensure_queue_rows_for_model(conn: sqlite3.Connection, model_name: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO leadops_vector_index_queue (
            doc_id, lead_id, doc_type, source_path, content_hash,
            embedding_status, embedding_model, embedded_at
        )
        SELECT
            d.id,
            d.lead_id,
            d.doc_type,
            d.source_path,
            d.content_hash,
            CASE
                WHEN ve.doc_id IS NOT NULL AND ve.content_hash = d.content_hash THEN 'embedded'
                ELSE 'pending'
            END,
            ?,
            CASE
                WHEN ve.doc_id IS NOT NULL AND ve.content_hash = d.content_hash THEN ve.indexed_at
                ELSE NULL
            END
        FROM leadops_search_documents d
        LEFT JOIN leadops_vector_embeddings ve
          ON ve.doc_id = d.id
         AND ve.embedding_model = ?
        """,
        (model_name, model_name),
    )
    conn.execute(
        """
        UPDATE leadops_vector_index_queue
        SET
            lead_id = (
                SELECT d.lead_id
                FROM leadops_search_documents d
                WHERE d.id = leadops_vector_index_queue.doc_id
            ),
            doc_type = (
                SELECT d.doc_type
                FROM leadops_search_documents d
                WHERE d.id = leadops_vector_index_queue.doc_id
            ),
            source_path = (
                SELECT d.source_path
                FROM leadops_search_documents d
                WHERE d.id = leadops_vector_index_queue.doc_id
            ),
            content_hash = (
                SELECT d.content_hash
                FROM leadops_search_documents d
                WHERE d.id = leadops_vector_index_queue.doc_id
            )
        WHERE embedding_model = ?
        """,
        (model_name,),
    )
    conn.execute(
        """
        UPDATE leadops_vector_index_queue
        SET
            embedding_status = CASE
                WHEN EXISTS (
                    SELECT 1
                    FROM leadops_vector_embeddings ve
                    WHERE ve.doc_id = leadops_vector_index_queue.doc_id
                      AND ve.embedding_model = leadops_vector_index_queue.embedding_model
                      AND ve.content_hash = leadops_vector_index_queue.content_hash
                )
                THEN 'embedded'
                ELSE 'pending'
            END,
            embedded_at = (
                SELECT ve.indexed_at
                FROM leadops_vector_embeddings ve
                WHERE ve.doc_id = leadops_vector_index_queue.doc_id
                  AND ve.embedding_model = leadops_vector_index_queue.embedding_model
                  AND ve.content_hash = leadops_vector_index_queue.content_hash
                LIMIT 1
            )
        WHERE embedding_model = ?
        """,
        (model_name,),
    )


def rehydrate_semantic_vectors_from_snapshots(
    conn: sqlite3.Connection,
    snapshot_paths: Iterable[Path],
) -> dict[str, object]:
    imported: list[dict[str, object]] = []
    restored_models: set[str] = set()
    for index, snapshot_path in enumerate(snapshot_paths):
        if not snapshot_path.exists():
            continue
        alias = f"semantic_restore_{index}"
        conn.execute(f"ATTACH DATABASE ? AS {alias}", (str(snapshot_path),))
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    f"SELECT name FROM {alias}.sqlite_master WHERE type = 'table'"
                )
            }
            if "leadops_vector_embeddings" not in tables:
                continue
            source_rows = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM {alias}.leadops_vector_embeddings"
                ).fetchone()[0]
            )
            if source_rows == 0:
                imported.append(
                    {
                        "snapshot": str(snapshot_path),
                        "source_rows": 0,
                        "matched_rows": 0,
                    }
                )
                continue
            matched_rows = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM {alias}.leadops_vector_embeddings s
                    JOIN leadops_search_documents d
                      ON d.content_hash = s.content_hash
                     AND d.doc_type = s.doc_type
                     AND COALESCE(d.lead_id, -1) = COALESCE(s.lead_id, -1)
                    """
                ).fetchone()[0]
            )
            conn.execute(
                f"""
                INSERT OR REPLACE INTO leadops_vector_embeddings (
                    doc_id, lead_id, doc_type, source_path, content_hash,
                    embedding_model, embedding_dim, vector_blob, vector_norm, indexed_at
                )
                SELECT
                    d.id,
                    d.lead_id,
                    d.doc_type,
                    d.source_path,
                    d.content_hash,
                    s.embedding_model,
                    s.embedding_dim,
                    s.vector_blob,
                    s.vector_norm,
                    s.indexed_at
                FROM {alias}.leadops_vector_embeddings s
                JOIN leadops_search_documents d
                  ON d.content_hash = s.content_hash
                 AND d.doc_type = s.doc_type
                 AND COALESCE(d.lead_id, -1) = COALESCE(s.lead_id, -1)
                WHERE COALESCE(s.embedding_model, '') <> ''
                """
            )
            for row in conn.execute(
                f"SELECT DISTINCT embedding_model FROM {alias}.leadops_vector_embeddings WHERE COALESCE(embedding_model, '') <> ''"
            ):
                restored_models.add(norm(row[0]))
            imported.append(
                {
                    "snapshot": str(snapshot_path),
                    "source_rows": source_rows,
                    "matched_rows": matched_rows,
                }
            )
        finally:
            conn.execute(f"DETACH DATABASE {alias}")

    for model_name in sorted(model for model in restored_models if model):
        ensure_queue_rows_for_model(conn, model_name)

    conn.commit()
    return {
        "snapshots": imported,
        "restored_models": sorted(model for model in restored_models if model),
        "vector_embeddings": int(
            conn.execute("SELECT COUNT(*) FROM leadops_vector_embeddings").fetchone()[0]
        ),
        "embedded_queue_rows": int(
            conn.execute(
                "SELECT COUNT(*) FROM leadops_vector_index_queue WHERE lower(COALESCE(embedding_status, 'pending')) = 'embedded'"
            ).fetchone()[0]
        ),
    }


def rehydrate_missing_search_documents_from_snapshots(
    conn: sqlite3.Connection,
    snapshot_paths: Iterable[Path],
    *,
    archive_suffix: str = "#snapshot-restore-20260331",
) -> dict[str, object]:
    imported: list[dict[str, object]] = []
    inserted_total = 0
    archived_total = 0
    path_conflicts_total = 0
    for index, snapshot_path in enumerate(snapshot_paths):
        if not snapshot_path.exists():
            continue
        alias = f"search_doc_restore_{index}"
        conn.execute(f"ATTACH DATABASE ? AS {alias}", (str(snapshot_path),))
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    f"SELECT name FROM {alias}.sqlite_master WHERE type = 'table'"
                )
            }
            if "leadops_search_documents" not in tables:
                continue
            missing_total = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM {alias}.leadops_search_documents s
                    LEFT JOIN leadops_search_documents d
                      ON d.content_hash = s.content_hash
                     AND d.doc_type = s.doc_type
                     AND COALESCE(d.lead_id, -1) = COALESCE(s.lead_id, -1)
                    WHERE d.id IS NULL
                    """
                ).fetchone()[0]
            )
            path_conflicts = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM {alias}.leadops_search_documents s
                    LEFT JOIN leadops_search_documents d
                      ON d.content_hash = s.content_hash
                     AND d.doc_type = s.doc_type
                     AND COALESCE(d.lead_id, -1) = COALESCE(s.lead_id, -1)
                    JOIN leadops_search_documents p
                      ON p.source_path = s.source_path
                     AND p.doc_type = s.doc_type
                    WHERE d.id IS NULL
                    """
                ).fetchone()[0]
            )
            conn.execute(
                f"""
                INSERT INTO leadops_search_documents (
                    lead_id, doc_type, title, source_path, body_text, content_hash, source_kind
                )
                SELECT
                    s.lead_id,
                    s.doc_type,
                    s.title,
                    s.source_path,
                    s.body_text,
                    s.content_hash,
                    s.source_kind
                FROM {alias}.leadops_search_documents s
                LEFT JOIN leadops_search_documents d
                  ON d.content_hash = s.content_hash
                 AND d.doc_type = s.doc_type
                 AND COALESCE(d.lead_id, -1) = COALESCE(s.lead_id, -1)
                LEFT JOIN leadops_search_documents p
                  ON p.source_path = s.source_path
                 AND p.doc_type = s.doc_type
                WHERE d.id IS NULL
                  AND p.id IS NULL
                """
            )
            inserted = int(conn.execute("SELECT changes()").fetchone()[0])
            conn.execute(
                f"""
                INSERT INTO leadops_search_documents (
                    lead_id, doc_type, title, source_path, body_text, content_hash, source_kind
                )
                SELECT
                    s.lead_id,
                    s.doc_type,
                    s.title,
                    s.source_path || ?,
                    s.body_text,
                    s.content_hash,
                    s.source_kind
                FROM {alias}.leadops_search_documents s
                LEFT JOIN leadops_search_documents d
                  ON d.content_hash = s.content_hash
                 AND d.doc_type = s.doc_type
                 AND COALESCE(d.lead_id, -1) = COALESCE(s.lead_id, -1)
                JOIN leadops_search_documents p
                  ON p.source_path = s.source_path
                 AND p.doc_type = s.doc_type
                LEFT JOIN leadops_search_documents ap
                  ON ap.source_path = s.source_path || ?
                 AND ap.doc_type = s.doc_type
                WHERE d.id IS NULL
                  AND ap.id IS NULL
                """,
                (archive_suffix, archive_suffix),
            )
            archived = int(conn.execute("SELECT changes()").fetchone()[0])
            inserted_total += inserted
            archived_total += archived
            path_conflicts_total += path_conflicts
            imported.append(
                {
                    "snapshot": str(snapshot_path),
                    "missing_total": missing_total,
                    "path_conflicts": path_conflicts,
                    "inserted": inserted,
                    "archived_inserted": archived,
                }
            )
            conn.commit()
        finally:
            conn.execute(f"DETACH DATABASE {alias}")

    return {
        "snapshots": imported,
        "inserted": inserted_total,
        "archived_inserted": archived_total,
        "path_conflicts": path_conflicts_total,
        "search_documents": int(
            conn.execute("SELECT COUNT(*) FROM leadops_search_documents").fetchone()[0]
        ),
    }


def insert_search_documents(conn: sqlite3.Connection) -> int:
    rows: list[tuple[object, ...]] = []
    fts_rows: list[tuple[object, ...]] = []

    def add_doc(
        lead_id: int | None,
        doc_type: str,
        title: str,
        source_path: str,
        body_text: str | None,
        source_kind: str,
    ) -> None:
        text = norm(body_text)
        if not lead_id or not text:
            return
        content_hash = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()
        rows.append((lead_id, doc_type, title, source_path, text, content_hash, source_kind))

    for lead_id, title, raw_markdown, profile_path in conn.execute(
        """
        SELECT p.lead_id, p.title, p.raw_markdown, l.profile_path
        FROM leadops_profiles p
        LEFT JOIN leadops_leads l
          ON l.lead_id = p.lead_id
        """
    ):
        add_doc(
            int(lead_id),
            "profile_markdown",
            norm(title),
            norm(profile_path),
            raw_markdown,
            "leadops_profiles",
        )

    for lead_id, artifact_kind, relative_path, text_extract in conn.execute(
        """
        SELECT lead_id, artifact_kind, relative_path, text_extract
        FROM leadops_evidence_artifacts
        WHERE length(COALESCE(text_extract, '')) > 0
        """
    ):
        add_doc(
            int(lead_id),
            f"evidence_{artifact_kind}",
            artifact_kind.replace("_", " "),
            relative_path,
            text_extract,
            "leadops_evidence_artifacts",
        )

    if rows:
        conn.executemany(
            """
            INSERT INTO leadops_search_documents (
                lead_id, doc_type, title, source_path, body_text, content_hash, source_kind
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        for doc_id, lead_id, doc_type, title, source_path, body_text, source_kind in conn.execute(
            """
            SELECT id, lead_id, doc_type, title, source_path, body_text, source_kind
            FROM leadops_search_documents
            """
        ):
            fts_rows.append((norm(title), norm(body_text), int(lead_id or 0), norm(doc_type), norm(source_path), norm(source_kind)))
    if fts_rows:
        conn.executemany(
            """
            INSERT INTO leadops_search_fts (
                title, body_text, lead_id, doc_type, source_path, source_kind
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            fts_rows,
        )
    return len(rows)


def insert_vector_index_queue(conn: sqlite3.Connection) -> int:
    rows = []
    for doc_id, lead_id, doc_type, source_path, content_hash in conn.execute(
        "SELECT id, lead_id, doc_type, source_path, content_hash FROM leadops_search_documents"
    ):
        for embedding_model in DEFAULT_VECTOR_MODEL_KEYS:
            rows.append(
                (
                    doc_id,
                    lead_id,
                    doc_type,
                    source_path,
                    content_hash,
                    "pending",
                    embedding_model,
                    "",
                )
            )
    if rows:
        conn.executemany(
            """
            INSERT INTO leadops_vector_index_queue (
                doc_id, lead_id, doc_type, source_path, content_hash, embedding_status, embedding_model, embedded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def insert_entity_match(conn: sqlite3.Connection) -> int:
    rows = []
    alias_map: dict[int, list[tuple[set[str], str]]] = {}
    for lead_id, alias_tokens_json, alias_compact in conn.execute(
        """
        SELECT lead_id, alias_tokens_json, alias_compact
        FROM leadops_entity_aliases
        """
    ):
        alias_tokens = set(json.loads(alias_tokens_json or "[]"))
        alias_map.setdefault(int(lead_id), []).append((alias_tokens, norm(alias_compact)))
    query = """
        SELECT
            l.lead_id,
            l.name,
            l.email,
            l.email_domain,
            l.website,
            l.website_domain,
            COALESCE(p.title, '')
        FROM leadops_leads l
        LEFT JOIN leadops_profiles p
          ON p.lead_id = l.lead_id
    """
    for lead_id, name, email, email_domain, website, website_domain, profile_title in conn.execute(query):
        lead_tokens = tokenize_text(clean_lead_name_for_tokens(name))
        lead_tokens |= tokenize_text(profile_title)
        website_tokens_set = domain_tokens(website_domain or website)
        email_domain_tokens_set = set() if norm(email_domain) in GENERIC_EMAIL_DOMAINS else domain_tokens(email_domain)
        email_local_tokens_set = email_local_tokens(email)
        lead_compact = compact_entity_text(clean_lead_name_for_tokens(name))
        if profile_title:
            profile_compact = compact_entity_text(profile_title)
            if len(profile_compact) > len(lead_compact):
                lead_compact = profile_compact
        website_compact = compact_entity_text(domain_core(website_domain or website))
        email_domain_compact = "" if norm(email_domain) in GENERIC_EMAIL_DOMAINS else compact_entity_text(domain_core(email_domain))
        email_local_compact = compact_entity_text(normalize_email(email).split("@", 1)[0] if "@" in normalize_email(email) else "")
        for alias_tokens, alias_compact in alias_map.get(int(lead_id), []):
            lead_tokens |= alias_tokens
            if len(alias_compact) > len(lead_compact):
                lead_compact = alias_compact
        score, bucket, rationale = summarize_entity_match(
            lead_tokens,
            website_tokens_set,
            email_domain_tokens_set,
            email_local_tokens_set,
            norm(email_domain),
            norm(website_domain),
            lead_compact,
            website_compact,
            email_domain_compact,
            email_local_compact,
        )
        rows.append(
            (
                int(lead_id),
                score,
                bucket,
                json.dumps(sorted(lead_tokens), ensure_ascii=True),
                json.dumps(sorted(website_tokens_set), ensure_ascii=True),
                json.dumps(sorted(email_domain_tokens_set), ensure_ascii=True),
                json.dumps(sorted(email_local_tokens_set), ensure_ascii=True),
                rationale,
            )
        )
    if rows:
        conn.executemany(
            """
            INSERT INTO leadops_entity_match (
                lead_id, match_score, confidence_bucket, lead_tokens_json, website_tokens_json,
                email_domain_tokens_json, email_local_tokens_json, rationale
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def insert_entity_clusters(conn: sqlite3.Connection) -> int:
    groups: dict[str, dict[str, object]] = {}
    promoted_canonicals = {
        int(lead_id)
        for (lead_id,) in conn.execute(
            """
            SELECT lead_id
            FROM leadops_v_latest_review_decision
            WHERE lower(COALESCE(decision, '')) IN ('promote_now', 'promote_from_hold')
            """
        ).fetchall()
    }
    query = """
        SELECT lead_id, name, status, outreach_status, email, email_domain, website, website_domain
        FROM leadops_leads
    """
    for lead_id, name, status, outreach_status, email, email_domain, website, website_domain in conn.execute(query):
        basis, key = derive_business_cluster_key(email, email_domain, website, website_domain)
        if not key:
            key = str(lead_id)
        cluster_id = f"{basis}:{key}"
        entry = groups.setdefault(
            cluster_id,
            {
                "cluster_basis": basis,
                "cluster_key": key,
                "members": [],
            },
        )
        entry["members"].append(
            {
                "lead_id": int(lead_id),
                "name": norm(name),
                "status": norm(status),
                "outreach_status": norm(outreach_status),
            }
        )

    cluster_rows = []
    member_rows = []
    for cluster_id, payload in groups.items():
        members = list(payload["members"])
        override_candidates = [member for member in members if int(member["lead_id"]) in promoted_canonicals]
        canonical_lead_id = (
            choose_cluster_canonical_member(override_candidates)
            if override_candidates
            else choose_cluster_canonical_member(members)
        )
        member_ids = [int(member["lead_id"]) for member in members]
        member_names = [norm(member["name"]) for member in members]
        cluster_rows.append(
            (
                cluster_id,
                str(payload["cluster_basis"]),
                str(payload["cluster_key"]),
                canonical_lead_id,
                len(members),
                json.dumps(member_ids, ensure_ascii=True),
                json.dumps(member_names, ensure_ascii=True),
                "computed:leadops-bootstrap",
            )
        )
        for member in members:
            member_rows.append(
                (
                    cluster_id,
                    int(member["lead_id"]),
                    1 if int(member["lead_id"]) == canonical_lead_id else 0,
                    str(payload["cluster_basis"]),
                    "computed:leadops-bootstrap",
                )
            )

    if cluster_rows:
        conn.executemany(
            """
            INSERT INTO leadops_entity_clusters (
                cluster_id, cluster_basis, cluster_key, canonical_lead_id, member_count,
                member_lead_ids_json, member_names_json, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            cluster_rows,
        )
    if member_rows:
        conn.executemany(
            """
            INSERT INTO leadops_entity_cluster_members (
                cluster_id, lead_id, is_canonical, member_basis, source
            ) VALUES (?, ?, ?, ?, ?)
            """,
            member_rows,
        )
    return len(cluster_rows)


def normalize_outreach_status(value: str | None) -> str:
    status = low(value)
    if not status:
        return ""
    if status in {"opt out", "opt-out"}:
        return "opt-out"
    if status.startswith("drafted"):
        return "drafted"
    if status in {"sent", "delivered"}:
        return "sent"
    if status == "replied":
        return "replied"
    if status == "bounced":
        return "bounced"
    if status == "uncontacted":
        return "uncontacted"
    if status == "disqualified":
        return "disqualified"
    return status


def better_timestamp(candidate: str, current: str) -> bool:
    if not candidate:
        return False
    if not current:
        return True
    return candidate > current


def include_event_in_outreach_reconciliation(source: str, channel: str) -> bool:
    source_name = norm(source)
    channel_name = normalize_contact_log_channel(channel)
    if source_name in {"sent-items.json", "delivered-emails.json"}:
        return True
    if source_name == "contact-log.md":
        return channel_name in {"email", "contact form"}
    return False


def reconcile_lead_outreach_statuses(conn: sqlite3.Connection) -> int:
    lead_rows = conn.execute(
        """
        SELECT lead_id, outreach_status, index_outreach_status, disqualified
        FROM leadops_leads
        """
    ).fetchall()
    facts: dict[int, dict[str, object]] = {
        int(row[0]): {
            "index_outreach_status": normalize_outreach_status(row[2] or row[1]),
            "disqualified": int(row[3] or 0),
            "has_draft": False,
            "has_sent": False,
            "has_bounced": False,
            "has_replied": False,
            "has_opt_out": False,
            "last_event_at": "",
            "last_channel": "",
            "reasons": [],
        }
        for row in lead_rows
    }

    for lead_id, event_date, channel, status, source in conn.execute(
        """
        SELECT lead_id, COALESCE(event_date, ''), COALESCE(channel, ''), COALESCE(status, ''), source
        FROM leadops_outreach_events
        WHERE lead_id IS NOT NULL
        """
    ):
        if lead_id not in facts:
            continue
        if not include_event_in_outreach_reconciliation(source, channel):
            continue
        fact = facts[lead_id]
        normalized = normalize_outreach_status(status)
        channel_name = normalize_contact_log_channel(channel) or norm(channel) or "unknown"
        source_name = norm(source)
        if source_name in {"sent-items.json", "delivered-emails.json"}:
            channel_name = "email"
        if normalized == "drafted":
            fact["has_draft"] = True
        elif normalized == "sent":
            fact["has_sent"] = True
        elif normalized == "bounced":
            fact["has_bounced"] = True
        elif normalized == "replied":
            fact["has_replied"] = True
        elif normalized == "opt-out":
            fact["has_opt_out"] = True
        if normalized in {"drafted", "sent", "bounced", "replied", "opt-out"}:
            fact["reasons"].append(f"{source_name}:{channel_name}:{normalized}")
            if better_timestamp(event_date, str(fact["last_event_at"])):
                fact["last_event_at"] = event_date
                fact["last_channel"] = channel_name

    for lead_id, draft_date in conn.execute(
        """
        SELECT lead_id, COALESCE(MAX(draft_date), '')
        FROM leadops_drafts
        WHERE lead_id IS NOT NULL
        GROUP BY lead_id
        """
    ):
        if lead_id not in facts:
            continue
        fact = facts[lead_id]
        fact["has_draft"] = True
        fact["reasons"].append("drafts.json:drafted")
        if better_timestamp(draft_date, str(fact["last_event_at"])):
            fact["last_event_at"] = draft_date
            fact["last_channel"] = "email"

    for lead_id, opt_out_date in conn.execute(
        """
        SELECT lead_id, COALESCE(MAX(opt_out_date), '')
        FROM leadops_opt_outs
        WHERE lead_id IS NOT NULL
        GROUP BY lead_id
        """
    ):
        if lead_id not in facts:
            continue
        fact = facts[lead_id]
        fact["has_opt_out"] = True
        fact["reasons"].append("opt-out-log.md:opt-out")
        if better_timestamp(opt_out_date, str(fact["last_event_at"])):
            fact["last_event_at"] = opt_out_date
            fact["last_channel"] = "email"

    rows = []
    for lead_id, fact in facts.items():
        baseline = str(fact["index_outreach_status"])
        if fact["has_opt_out"]:
            reconciled = "opt-out"
        elif fact["has_replied"]:
            reconciled = "replied"
        elif fact["has_bounced"]:
            reconciled = "bounced"
        elif fact["has_sent"]:
            reconciled = "sent"
        elif fact["has_draft"]:
            if baseline in {"sent", "bounced", "replied", "opt-out"}:
                reconciled = baseline
            else:
                reconciled = "drafted"
        else:
            reconciled = str(baseline or ("disqualified" if fact["disqualified"] else "uncontacted"))

        reasons = sorted(dict.fromkeys(str(r) for r in fact["reasons"]))
        if reconciled == baseline and "drafts.json:drafted" in reasons:
            reasons = [reason for reason in reasons if reason != "drafts.json:drafted"]
        if not reasons and baseline:
            reasons = [f"index.csv:{baseline}"]

        rows.append(
            (
                reconciled,
                "; ".join(reasons),
                str(fact["last_event_at"]),
                str(fact["last_channel"]),
                lead_id,
            )
        )

    conn.executemany(
        """
        UPDATE leadops_leads
        SET outreach_status = ?,
            reconciled_status_reason = ?,
            last_outreach_event_at = ?,
            last_outreach_channel = ?
        WHERE lead_id = ?
        """,
        rows,
    )
    return len(rows)


def backup_db(db_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_name(f"{db_path.stem}.pre-leadops-{timestamp}{db_path.suffix}.bak")
    shutil.copy2(db_path, backup_path)
    return backup_path


def write_report(
    report_path: Path,
    db_path: Path,
    backup_path: Path | None,
    counts: dict[str, int],
) -> None:
    lines = [
        "# LeadOps SQLite Bootstrap",
        "",
        f"- Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Database: `{db_path}`",
    ]
    if backup_path:
        lines.append(f"- Backup: `{backup_path}`")
    lines.extend(
        [
            "",
            "## Imported",
            "",
            f"- Leads: {counts['leads']}",
            f"- Profiles: {counts['profiles']}",
            f"- Contacts: {counts['contacts']}",
            f"- Outreach events: {counts['events']}",
            f"- Profile outreach-log events: {counts['profile_events']}",
            f"- Drafts: {counts['drafts']}",
            f"- Opt-outs: {counts['opt_outs']}",
            f"- Mailbox snapshots: {counts['mailbox_snapshots']}",
            f"- Missing-field rows: {counts['missing_fields']}",
            f"- Audit runs: {counts['audit_runs']}",
            f"- Audit findings: {counts['audit_findings']}",
            f"- Research tasks: {counts['research_tasks']}",
            f"- Suppression registry rows: {counts['suppression_registry']}",
            f"- Bounce events: {counts['bounce_events']}",
            f"- Draft provenance rows: {counts['draft_provenance']}",
            f"- Bounce follow-up queue rows: {counts['bounce_followup_queue']}",
            f"- Business facts: {counts['business_facts']}",
            f"- Evidence artifacts: {counts['evidence_artifacts']}",
            f"- Entity edges: {counts['entity_edges']}",
            f"- Search documents: {counts['search_documents']}",
            f"- Vector queue rows: {counts['vector_queue']}",
            f"- Send suppressions: {counts['send_suppressions']}",
            f"- Entity aliases: {counts['entity_alias_rows']}",
            f"- Entity promotions: {counts['entity_promotion_rows']}",
            f"- Review decisions: {counts['review_decision_rows']}",
            f"- Entity-match rows: {counts['entity_match_rows']}",
            f"- Entity clusters: {counts['entity_cluster_rows']}",
            f"- Reconciled lead outreach statuses: {counts['reconciled_leads']}",
        ]
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap a leadops schema inside crm.sqlite and import the current lead corpus.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to sqlite database (default: crm.sqlite)")
    parser.add_argument("--no-backup", action="store_true", help="Skip creating a backup copy before schema/import changes.")
    parser.add_argument("--report", default=str(REPO_ROOT / "reports" / f"leadops-bootstrap-{datetime.now().strftime('%Y-%m-%d')}.md"))
    parser.add_argument(
        "--deep-index",
        action="store_true",
        help="Also rebuild heavy evidence/search/vector layers from leads/profiles/**/evidence.",
    )
    parser.add_argument(
        "--no-rehydrate-semantic-vectors",
        action="store_true",
        help="During --deep-index, do not rehydrate vector embeddings from tmp semantic snapshot DBs after rebuilding docs/queue.",
    )
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")
    if not INDEX_CSV.exists():
        raise SystemExit(f"Missing source index: {INDEX_CSV}")

    backup_path = None if args.no_backup else backup_db(db_path)
    leads = load_leads(INDEX_CSV)
    progress_started = time.perf_counter()
    log_progress(f"Loaded {len(leads)} leads from index.csv", progress_started)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    started_at = datetime.now().isoformat(timespec="seconds")
    source_snapshots = collect_source_snapshots()
    run_payload = {
        "index_csv": str(INDEX_CSV.relative_to(REPO_ROOT)),
        "contact_log_md": str(CONTACT_LOG_MD.relative_to(REPO_ROOT)),
        "opt_out_log_md": str(OPT_OUT_LOG_MD.relative_to(REPO_ROOT)),
        "sent_items_json": str(SENT_ITEMS_JSON.relative_to(REPO_ROOT)),
        "delivered_items_json": str(DELIVERED_ITEMS_JSON.relative_to(REPO_ROOT)),
        "drafts_json": str(DRAFTS_JSON.relative_to(REPO_ROOT)),
        "drafts_revised_json": str(DRAFTS_REVISED_JSON.relative_to(REPO_ROOT)),
        "missing_fields_md": str(MISSING_FIELDS_MD.relative_to(REPO_ROOT)),
        "deep_audit_queue_json": str(DEEP_AUDIT_QUEUE_JSON.relative_to(REPO_ROOT)),
        "bounce_suppression_json": str(BOUNCE_SUPPRESSION_JSON.relative_to(REPO_ROOT)),
        "all_bounced_emails_json": str(ALL_BOUNCED_EMAILS_JSON.relative_to(REPO_ROOT)),
        "bounced_emails_json": str(BOUNCED_EMAILS_JSON.relative_to(REPO_ROOT)),
        "bounce_followup_worklist_json": str(BOUNCE_FOLLOWUP_WORKLIST_JSON.relative_to(REPO_ROOT)),
        "dba_candidates_json": str(DBA_CANDIDATES_JSON.relative_to(REPO_ROOT)),
        "contact_path_snippets_txt": str(CONTACT_PATH_SNIPPETS_TXT.relative_to(REPO_ROOT)),
        "profiles_root": str(PROFILES_ROOT.relative_to(REPO_ROOT)),
        "send_suppressions_json": str(SEND_SUPPRESSIONS_JSON.relative_to(REPO_ROOT)),
        "entity_aliases_json": str(ENTITY_ALIASES_JSON.relative_to(REPO_ROOT)),
        "entity_promotions_json": str(ENTITY_PROMOTIONS_JSON.relative_to(REPO_ROOT)),
        "review_decisions_csv": str(REVIEW_DECISIONS_CSV.relative_to(REPO_ROOT)),
        "mailbox_snapshot_json": str(find_latest_mailbox_snapshot().relative_to(REPO_ROOT)) if find_latest_mailbox_snapshot() else "",
        "lead_count_seen": len(leads),
        "deep_index_enabled": bool(args.deep_index),
        "source_keys_seen": [item["source_key"] for item in source_snapshots],
    }
    try:
        log_progress("Creating schema", progress_started)
        create_schema(conn)
        log_progress("Recording import run", progress_started)
        run_cur = conn.execute(
            """
            INSERT INTO leadops_import_runs (
                started_at, source_summary_json, mode, profile_parse_mode, deep_index_mode
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                started_at,
                json.dumps(run_payload, ensure_ascii=True, sort_keys=True),
                "incremental",
                "full",
                "incremental" if args.deep_index else "skipped",
            ),
        )
        run_id = int(run_cur.lastrowid)
        prior_sources = load_prior_source_registry(conn)
        source_comparisons, source_change_summary = compare_source_snapshots(source_snapshots, prior_sources)
        refresh_mode = determine_refresh_mode(set(source_change_summary.get("changed_groups", [])), deep_index=False)
        persist_source_registry_and_run_sources(conn, run_id, source_comparisons)
        conn.execute(
            """
            UPDATE leadops_import_runs
            SET source_summary_json = ?,
                source_fingerprint_json = ?,
                change_summary_json = ?,
                mode = ?
            WHERE id = ?
            """,
            (
                json.dumps(
                    {
                        **run_payload,
                        "source_change_summary": source_change_summary,
                        "refresh_mode": refresh_mode,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                ),
                json.dumps(source_snapshots, ensure_ascii=True, sort_keys=True),
                json.dumps(source_change_summary, ensure_ascii=True, sort_keys=True),
                refresh_mode,
                run_id,
            ),
        )
        if refresh_mode == "noop" and not args.deep_index:
            log_progress("No source changes detected; finishing early", progress_started)
            conn.execute(
                "UPDATE leadops_import_runs SET completed_at = ? WHERE id = ?",
                (datetime.now().isoformat(timespec="seconds"), run_id),
            )
            conn.commit()
            report_path = Path(args.report)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            counts = {
                "leads": len(leads),
                "profiles": int(conn.execute("SELECT COUNT(*) FROM leadops_profiles").fetchone()[0]),
                "contacts": int(conn.execute("SELECT COUNT(*) FROM leadops_contacts").fetchone()[0]),
                "events": int(conn.execute("SELECT COUNT(*) FROM leadops_outreach_events").fetchone()[0]),
                "profile_events": int(conn.execute("SELECT COUNT(*) FROM leadops_profile_outreach_log_events").fetchone()[0]),
                "drafts": int(conn.execute("SELECT COUNT(*) FROM leadops_drafts").fetchone()[0]),
                "opt_outs": int(conn.execute("SELECT COUNT(*) FROM leadops_opt_outs").fetchone()[0]),
                "mailbox_snapshots": int(conn.execute("SELECT COUNT(*) FROM leadops_mailbox_snapshots").fetchone()[0]),
                "missing_fields": int(conn.execute("SELECT COUNT(*) FROM leadops_missing_fields").fetchone()[0]),
                "audit_runs": int(conn.execute("SELECT COUNT(*) FROM leadops_audit_runs").fetchone()[0]),
                "audit_findings": int(conn.execute("SELECT COUNT(*) FROM leadops_audit_findings").fetchone()[0]),
                "research_tasks": int(conn.execute("SELECT COUNT(*) FROM leadops_research_tasks").fetchone()[0]),
                "suppression_registry": int(conn.execute("SELECT COUNT(*) FROM leadops_suppression_registry").fetchone()[0]),
                "bounce_events": int(conn.execute("SELECT COUNT(*) FROM leadops_bounce_events").fetchone()[0]),
                "draft_provenance": int(conn.execute("SELECT COUNT(*) FROM leadops_draft_provenance").fetchone()[0]),
                "bounce_followup_queue": int(conn.execute("SELECT COUNT(*) FROM leadops_bounce_followup_queue").fetchone()[0]),
                "business_facts": int(conn.execute("SELECT COUNT(*) FROM leadops_business_facts").fetchone()[0]),
                "evidence_artifacts": int(conn.execute("SELECT COUNT(*) FROM leadops_evidence_artifacts").fetchone()[0]),
                "entity_edges": int(conn.execute("SELECT COUNT(*) FROM leadops_entity_edges").fetchone()[0]),
                "search_documents": int(conn.execute("SELECT COUNT(*) FROM leadops_search_documents").fetchone()[0]),
                "vector_queue": int(conn.execute("SELECT COUNT(*) FROM leadops_vector_index_queue").fetchone()[0]),
                "send_suppressions": int(conn.execute("SELECT COUNT(*) FROM leadops_send_suppressions").fetchone()[0]),
                "entity_alias_rows": int(conn.execute("SELECT COUNT(*) FROM leadops_entity_aliases").fetchone()[0]),
                "entity_promotion_rows": int(conn.execute("SELECT COUNT(*) FROM leadops_entity_promotions").fetchone()[0]),
                "review_decision_rows": int(conn.execute("SELECT COUNT(*) FROM leadops_review_decisions").fetchone()[0]),
                "entity_match_rows": int(conn.execute("SELECT COUNT(*) FROM leadops_entity_match").fetchone()[0]),
                "entity_cluster_rows": int(conn.execute("SELECT COUNT(*) FROM leadops_entity_clusters").fetchone()[0]),
                "reconciled_leads": int(conn.execute("SELECT COUNT(*) FROM leadops_leads").fetchone()[0]),
            }
            write_report(report_path, db_path, backup_path, counts)
            log_progress(f"Wrote report to {report_path}", progress_started)
            print(json.dumps({"db": str(db_path), "backup": str(backup_path) if backup_path else "", "counts": counts, "report": str(report_path), "skipped": True}, indent=2))
            return
        if refresh_mode == "noop":
            log_progress("No core source changes detected; reusing current leadops core before deep indexing", progress_started)
        elif refresh_mode == "outreach_only":
            log_progress("Refresh mode: outreach-only", progress_started)
            clear_outreach_sync_tables(conn)
            log_progress("Importing outreach events", progress_started)
            events_count = insert_outreach_events(conn, leads, CONTACT_LOG_MD, SENT_ITEMS_JSON, DELIVERED_ITEMS_JSON)
            log_progress("Importing drafts", progress_started)
            drafts_count = insert_drafts(conn, leads, DRAFTS_JSON)
            log_progress("Importing opt-outs", progress_started)
            opt_outs_count = insert_opt_outs(conn, leads, OPT_OUT_LOG_MD)
            log_progress("Importing mailbox snapshots", progress_started)
            mailbox_snapshots_count = insert_mailbox_snapshots(conn)
            log_progress("Importing suppression registry and bounce events", progress_started)
            suppression_registry_count, bounce_events_count = insert_bounce_and_suppression_detail(conn, leads)
            log_progress("Importing draft provenance", progress_started)
            draft_provenance_count = insert_draft_provenance(conn, leads)
            log_progress("Importing bounce follow-up queue", progress_started)
            bounce_followup_queue_count = insert_bounce_followup_queue(conn)
            profiles_count = int(conn.execute("SELECT COUNT(*) FROM leadops_profiles").fetchone()[0])
            profile_events_count = int(conn.execute("SELECT COUNT(*) FROM leadops_profile_outreach_log_events").fetchone()[0])
            contacts_count = int(conn.execute("SELECT COUNT(*) FROM leadops_contacts").fetchone()[0])
            missing_fields_count = int(conn.execute("SELECT COUNT(*) FROM leadops_missing_fields").fetchone()[0])
            audit_runs_count = int(conn.execute("SELECT COUNT(*) FROM leadops_audit_runs").fetchone()[0])
            audit_findings_count = int(conn.execute("SELECT COUNT(*) FROM leadops_audit_findings").fetchone()[0])
            research_tasks_count = int(conn.execute("SELECT COUNT(*) FROM leadops_research_tasks").fetchone()[0])
            business_facts_count = int(conn.execute("SELECT COUNT(*) FROM leadops_business_facts").fetchone()[0])
            send_suppressions_count = int(conn.execute("SELECT COUNT(*) FROM leadops_send_suppressions").fetchone()[0])
            entity_alias_count = int(conn.execute("SELECT COUNT(*) FROM leadops_entity_aliases").fetchone()[0])
            entity_promotion_count = int(conn.execute("SELECT COUNT(*) FROM leadops_entity_promotions").fetchone()[0])
            review_decision_count = int(conn.execute("SELECT COUNT(*) FROM leadops_review_decisions").fetchone()[0])
            entity_match_count = int(conn.execute("SELECT COUNT(*) FROM leadops_entity_match").fetchone()[0])
            entity_cluster_count = int(conn.execute("SELECT COUNT(*) FROM leadops_entity_clusters").fetchone()[0])
        elif refresh_mode == "derived_only":
            log_progress("Refresh mode: derived-only", progress_started)
            clear_derived_sync_tables(conn)
            log_progress("Importing missing-fields report", progress_started)
            missing_fields_count = insert_missing_fields(conn, MISSING_FIELDS_MD)
            profiles_count = int(conn.execute("SELECT COUNT(*) FROM leadops_profiles").fetchone()[0])
            profile_events_count = int(conn.execute("SELECT COUNT(*) FROM leadops_profile_outreach_log_events").fetchone()[0])
            contacts_count = int(conn.execute("SELECT COUNT(*) FROM leadops_contacts").fetchone()[0])
            events_count = int(conn.execute("SELECT COUNT(*) FROM leadops_outreach_events").fetchone()[0])
            drafts_count = int(conn.execute("SELECT COUNT(*) FROM leadops_drafts").fetchone()[0])
            opt_outs_count = int(conn.execute("SELECT COUNT(*) FROM leadops_opt_outs").fetchone()[0])
            mailbox_snapshots_count = int(conn.execute("SELECT COUNT(*) FROM leadops_mailbox_snapshots").fetchone()[0])
            audit_runs_count = int(conn.execute("SELECT COUNT(*) FROM leadops_audit_runs").fetchone()[0])
            audit_findings_count = int(conn.execute("SELECT COUNT(*) FROM leadops_audit_findings").fetchone()[0])
            research_tasks_count = int(conn.execute("SELECT COUNT(*) FROM leadops_research_tasks").fetchone()[0])
            suppression_registry_count = int(conn.execute("SELECT COUNT(*) FROM leadops_suppression_registry").fetchone()[0])
            bounce_events_count = int(conn.execute("SELECT COUNT(*) FROM leadops_bounce_events").fetchone()[0])
            draft_provenance_count = int(conn.execute("SELECT COUNT(*) FROM leadops_draft_provenance").fetchone()[0])
            bounce_followup_queue_count = int(conn.execute("SELECT COUNT(*) FROM leadops_bounce_followup_queue").fetchone()[0])
            business_facts_count = int(conn.execute("SELECT COUNT(*) FROM leadops_business_facts").fetchone()[0])
            send_suppressions_count = int(conn.execute("SELECT COUNT(*) FROM leadops_send_suppressions").fetchone()[0])
            entity_alias_count = int(conn.execute("SELECT COUNT(*) FROM leadops_entity_aliases").fetchone()[0])
            entity_promotion_count = int(conn.execute("SELECT COUNT(*) FROM leadops_entity_promotions").fetchone()[0])
            review_decision_count = int(conn.execute("SELECT COUNT(*) FROM leadops_review_decisions").fetchone()[0])
            entity_match_count = int(conn.execute("SELECT COUNT(*) FROM leadops_entity_match").fetchone()[0])
            entity_cluster_count = int(conn.execute("SELECT COUNT(*) FROM leadops_entity_clusters").fetchone()[0])
        elif refresh_mode == "audit_only":
            log_progress("Refresh mode: audit-only", progress_started)
            clear_audit_sync_tables(conn)
            log_progress("Importing audit runs and findings", progress_started)
            audit_runs_count, audit_findings_count = insert_audit_runs_and_findings(conn)
            log_progress("Importing research tasks", progress_started)
            research_tasks_count = insert_research_tasks(conn)
            profiles_count = int(conn.execute("SELECT COUNT(*) FROM leadops_profiles").fetchone()[0])
            profile_events_count = int(conn.execute("SELECT COUNT(*) FROM leadops_profile_outreach_log_events").fetchone()[0])
            contacts_count = int(conn.execute("SELECT COUNT(*) FROM leadops_contacts").fetchone()[0])
            events_count = int(conn.execute("SELECT COUNT(*) FROM leadops_outreach_events").fetchone()[0])
            drafts_count = int(conn.execute("SELECT COUNT(*) FROM leadops_drafts").fetchone()[0])
            opt_outs_count = int(conn.execute("SELECT COUNT(*) FROM leadops_opt_outs").fetchone()[0])
            mailbox_snapshots_count = int(conn.execute("SELECT COUNT(*) FROM leadops_mailbox_snapshots").fetchone()[0])
            missing_fields_count = int(conn.execute("SELECT COUNT(*) FROM leadops_missing_fields").fetchone()[0])
            suppression_registry_count = int(conn.execute("SELECT COUNT(*) FROM leadops_suppression_registry").fetchone()[0])
            bounce_events_count = int(conn.execute("SELECT COUNT(*) FROM leadops_bounce_events").fetchone()[0])
            draft_provenance_count = int(conn.execute("SELECT COUNT(*) FROM leadops_draft_provenance").fetchone()[0])
            bounce_followup_queue_count = int(conn.execute("SELECT COUNT(*) FROM leadops_bounce_followup_queue").fetchone()[0])
            business_facts_count = int(conn.execute("SELECT COUNT(*) FROM leadops_business_facts").fetchone()[0])
            send_suppressions_count = int(conn.execute("SELECT COUNT(*) FROM leadops_send_suppressions").fetchone()[0])
            entity_alias_count = int(conn.execute("SELECT COUNT(*) FROM leadops_entity_aliases").fetchone()[0])
            entity_promotion_count = int(conn.execute("SELECT COUNT(*) FROM leadops_entity_promotions").fetchone()[0])
            review_decision_count = int(conn.execute("SELECT COUNT(*) FROM leadops_review_decisions").fetchone()[0])
            entity_match_count = int(conn.execute("SELECT COUNT(*) FROM leadops_entity_match").fetchone()[0])
            entity_cluster_count = int(conn.execute("SELECT COUNT(*) FROM leadops_entity_clusters").fetchone()[0])
        elif refresh_mode == "override_only":
            log_progress("Refresh mode: override-only", progress_started)
            clear_override_sync_tables(conn)
            profiles_count = int(conn.execute("SELECT COUNT(*) FROM leadops_profiles").fetchone()[0])
            profile_events_count = int(conn.execute("SELECT COUNT(*) FROM leadops_profile_outreach_log_events").fetchone()[0])
            contacts_count = int(conn.execute("SELECT COUNT(*) FROM leadops_contacts").fetchone()[0])
            events_count = int(conn.execute("SELECT COUNT(*) FROM leadops_outreach_events").fetchone()[0])
            drafts_count = int(conn.execute("SELECT COUNT(*) FROM leadops_drafts").fetchone()[0])
            opt_outs_count = int(conn.execute("SELECT COUNT(*) FROM leadops_opt_outs").fetchone()[0])
            mailbox_snapshots_count = int(conn.execute("SELECT COUNT(*) FROM leadops_mailbox_snapshots").fetchone()[0])
            missing_fields_count = int(conn.execute("SELECT COUNT(*) FROM leadops_missing_fields").fetchone()[0])
            audit_runs_count = int(conn.execute("SELECT COUNT(*) FROM leadops_audit_runs").fetchone()[0])
            audit_findings_count = int(conn.execute("SELECT COUNT(*) FROM leadops_audit_findings").fetchone()[0])
            research_tasks_count = int(conn.execute("SELECT COUNT(*) FROM leadops_research_tasks").fetchone()[0])
            suppression_registry_count = int(conn.execute("SELECT COUNT(*) FROM leadops_suppression_registry").fetchone()[0])
            bounce_events_count = int(conn.execute("SELECT COUNT(*) FROM leadops_bounce_events").fetchone()[0])
            draft_provenance_count = int(conn.execute("SELECT COUNT(*) FROM leadops_draft_provenance").fetchone()[0])
            bounce_followup_queue_count = int(conn.execute("SELECT COUNT(*) FROM leadops_bounce_followup_queue").fetchone()[0])
            log_progress("Importing business facts", progress_started)
            business_facts_count = insert_business_facts(conn, mode="overrides_only")
            log_progress("Importing send suppressions", progress_started)
            send_suppressions_count = insert_send_suppressions(conn, SEND_SUPPRESSIONS_JSON)
            log_progress("Importing entity aliases", progress_started)
            entity_alias_count = insert_entity_aliases(conn, ENTITY_ALIASES_JSON)
            log_progress("Importing entity promotions", progress_started)
            entity_promotion_count = insert_entity_promotions(conn, ENTITY_PROMOTIONS_JSON)
            log_progress("Importing review decisions", progress_started)
            review_decision_count = insert_review_decisions(conn, REVIEW_DECISIONS_CSV)
            log_progress("Computing entity-match confidence", progress_started)
            entity_match_count = insert_entity_match(conn)
            log_progress("Computing entity clusters", progress_started)
            entity_cluster_count = insert_entity_clusters(conn)
        else:
            log_progress(
                f"Refresh mode: {refresh_mode}. Clearing prior leadops tables",
                progress_started,
            )
            clear_leadops_tables(conn, include_deep_index=False)
            log_progress("Pruning removed leads", progress_started)
            removed_lead_count = prune_removed_leads(conn, {lead.lead_id for lead in leads})
            log_progress("Importing leads", progress_started)
            insert_leads(conn, leads)
            log_progress("Syncing index contacts", progress_started)
            contacts_count = sync_index_contacts(conn, leads)
            log_progress("Syncing profiles and profile events", progress_started)
            profiles_count, _, profile_events_count = sync_profiles_and_profile_events(
                conn,
                leads,
                progress_started=progress_started,
            )
            log_progress("Applying enrichment profile backfills", progress_started)
            enrichment_profile_backfill_count = apply_enrichment_profile_backfills(conn)
            log_progress(
                f"Enrichment profile backfills applied to {enrichment_profile_backfill_count} leads",
                progress_started,
            )
            log_progress("Importing outreach events", progress_started)
            events_count = insert_outreach_events(conn, leads, CONTACT_LOG_MD, SENT_ITEMS_JSON, DELIVERED_ITEMS_JSON)
            log_progress("Importing drafts", progress_started)
            drafts_count = insert_drafts(conn, leads, DRAFTS_JSON)
            log_progress("Importing opt-outs", progress_started)
            opt_outs_count = insert_opt_outs(conn, leads, OPT_OUT_LOG_MD)
            log_progress("Importing mailbox snapshots", progress_started)
            mailbox_snapshots_count = insert_mailbox_snapshots(conn)
            log_progress("Importing missing-fields report", progress_started)
            missing_fields_count = insert_missing_fields(conn, MISSING_FIELDS_MD)
            log_progress("Importing audit runs and findings", progress_started)
            audit_runs_count, audit_findings_count = insert_audit_runs_and_findings(conn)
            log_progress("Importing research tasks", progress_started)
            research_tasks_count = insert_research_tasks(conn)
            log_progress("Importing suppression registry and bounce events", progress_started)
            suppression_registry_count, bounce_events_count = insert_bounce_and_suppression_detail(conn, leads)
            log_progress("Importing draft provenance", progress_started)
            draft_provenance_count = insert_draft_provenance(conn, leads)
            log_progress("Importing bounce follow-up queue", progress_started)
            bounce_followup_queue_count = insert_bounce_followup_queue(conn)
            log_progress("Importing business facts", progress_started)
            business_facts_count = insert_business_facts(conn)
        if args.deep_index:
            log_progress("Syncing evidence artifacts incrementally", progress_started)
            evidence_changed_count, evidence_unchanged_count, evidence_hash_checked_count, evidence_deleted_count = sync_evidence_artifacts(conn)
            log_progress(
                "Deep index evidence summary: "
                f"changed={evidence_changed_count}, unchanged={evidence_unchanged_count}, "
                f"hash_checked={evidence_hash_checked_count}, deleted={evidence_deleted_count}",
                progress_started,
            )
            log_progress("Refreshing deep search/vector support tables", progress_started)
            clear_deep_index_support_tables(conn)
            log_progress("Computing entity edges", progress_started)
            entity_edges_count = insert_entity_edges(conn)
            log_progress("Importing search documents", progress_started)
            search_documents_count = insert_search_documents(conn)
            log_progress("Preparing vector index queue", progress_started)
            vector_queue_count = insert_vector_index_queue(conn)
            if not args.no_rehydrate_semantic_vectors:
                log_progress("Rehydrating missing search documents from snapshot DBs", progress_started)
                search_doc_restore_summary = rehydrate_missing_search_documents_from_snapshots(
                    conn,
                    (
                        DEFAULT_FAST_SEMANTIC_DB,
                        DEFAULT_QUALITY_SEMANTIC_DB,
                    ),
                )
                log_progress(
                    "Search-document restore summary: "
                    + json.dumps(search_doc_restore_summary, ensure_ascii=True, sort_keys=True),
                    progress_started,
                )
                log_progress("Rehydrating semantic vectors from snapshot DBs", progress_started)
                semantic_restore_summary = rehydrate_semantic_vectors_from_snapshots(
                    conn,
                    (
                        DEFAULT_FAST_SEMANTIC_DB,
                        DEFAULT_QUALITY_SEMANTIC_DB,
                    ),
                )
                log_progress(
                    "Semantic restore summary: "
                    + json.dumps(semantic_restore_summary, ensure_ascii=True, sort_keys=True),
                    progress_started,
                )
                vector_queue_count = int(
                    conn.execute("SELECT COUNT(*) FROM leadops_vector_index_queue").fetchone()[0]
                )
            evidence_artifacts_count = int(
                conn.execute("SELECT COUNT(*) FROM leadops_evidence_artifacts").fetchone()[0]
            )
        else:
            log_progress("Skipping deep evidence/search/vector indexing", progress_started)
            evidence_artifacts_count = int(
                conn.execute("SELECT COUNT(*) FROM leadops_evidence_artifacts").fetchone()[0]
            )
            entity_edges_count = int(
                conn.execute("SELECT COUNT(*) FROM leadops_entity_edges").fetchone()[0]
            )
            search_documents_count = int(
                conn.execute("SELECT COUNT(*) FROM leadops_search_documents").fetchone()[0]
            )
            vector_queue_count = int(
                conn.execute("SELECT COUNT(*) FROM leadops_vector_index_queue").fetchone()[0]
            )
        if refresh_mode == "full":
            log_progress("Importing send suppressions", progress_started)
            send_suppressions_count = insert_send_suppressions(conn, SEND_SUPPRESSIONS_JSON)
            log_progress("Importing entity aliases", progress_started)
            entity_alias_count = insert_entity_aliases(conn, ENTITY_ALIASES_JSON)
            log_progress("Importing entity promotions", progress_started)
            entity_promotion_count = insert_entity_promotions(conn, ENTITY_PROMOTIONS_JSON)
            log_progress("Importing review decisions", progress_started)
            review_decision_count = insert_review_decisions(conn, REVIEW_DECISIONS_CSV)
            log_progress("Computing entity-match confidence", progress_started)
            entity_match_count = insert_entity_match(conn)
            log_progress("Computing entity clusters", progress_started)
            entity_cluster_count = insert_entity_clusters(conn)
        log_progress("Reconciling outreach statuses", progress_started)
        reconciled_leads_count = reconcile_lead_outreach_statuses(conn)
        log_progress("Marking import run complete", progress_started)
        conn.execute(
            "UPDATE leadops_import_runs SET completed_at = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), run_id),
        )
        log_progress("Committing sqlite transaction", progress_started)
        conn.commit()
    finally:
        conn.close()
        log_progress("Closed sqlite connection", progress_started)

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as counts_conn:
        def table_count(table: str) -> int:
            return int(counts_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

        counts = {
            "leads": table_count("leadops_leads"),
            "profiles": table_count("leadops_profiles"),
            "contacts": table_count("leadops_contacts"),
            "events": table_count("leadops_outreach_events"),
            "profile_events": table_count("leadops_profile_outreach_log_events"),
            "drafts": table_count("leadops_drafts"),
            "opt_outs": table_count("leadops_opt_outs"),
            "mailbox_snapshots": table_count("leadops_mailbox_snapshots"),
            "missing_fields": table_count("leadops_missing_fields"),
            "audit_runs": table_count("leadops_audit_runs"),
            "audit_findings": table_count("leadops_audit_findings"),
            "research_tasks": table_count("leadops_research_tasks"),
            "suppression_registry": table_count("leadops_suppression_registry"),
            "bounce_events": table_count("leadops_bounce_events"),
            "draft_provenance": table_count("leadops_draft_provenance"),
            "bounce_followup_queue": table_count("leadops_bounce_followup_queue"),
            "business_facts": table_count("leadops_business_facts"),
            "evidence_artifacts": table_count("leadops_evidence_artifacts"),
            "entity_edges": table_count("leadops_entity_edges"),
            "search_documents": table_count("leadops_search_documents"),
            "vector_queue": table_count("leadops_vector_index_queue"),
            "send_suppressions": table_count("leadops_send_suppressions"),
            "entity_alias_rows": table_count("leadops_entity_aliases"),
            "entity_promotion_rows": table_count("leadops_entity_promotions"),
            "review_decision_rows": table_count("leadops_review_decisions"),
            "entity_match_rows": table_count("leadops_entity_match"),
            "entity_cluster_rows": table_count("leadops_entity_clusters"),
            "reconciled_leads": table_count("leadops_leads"),
        }
    write_report(report_path, db_path, backup_path, counts)
    log_progress(f"Wrote report to {report_path}", progress_started)

    print(json.dumps({"db": str(db_path), "backup": str(backup_path) if backup_path else "", "counts": counts, "report": str(report_path)}, indent=2))


if __name__ == "__main__":
    main()

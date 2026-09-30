#!/usr/bin/env python3
"""
LeadOps demo dataset generator.

Builds a fully synthetic, runnable demo of the LeadOps pipeline with one command:

    python demo/generate_demo.py

What it does:
  1. Generates synthetic lead inputs under demo/work/ (index.csv, profile markdown,
     review decisions, a sample audit file). No real business data anywhere.
  2. Copies the REAL bootstrap_leadops_sqlite.py next to those inputs.
  3. Runs the REAL pipeline to produce demo/crm.demo.sqlite (41 tables, 63 views).
  4. Adds an empty leadops_vector_embeddings table so the Streamlit UI's schema
     check passes (vector search needs local embedding models; the demo skips it).
  5. Builds the free-text search index (leadops_search_documents /
     leadops_search_fts) from the 24 profile markdown files, since the pipeline
     only does that under --deep-index.

Then launch the UI:

    cp demo/crm.demo.sqlite crm.sqlite   # crm.sqlite is gitignored
    streamlit run app.py

Stdlib only. Deterministic (seeded) so every clone builds the same demo.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import random
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

DEMO_ROOT = Path(__file__).resolve().parent
REPO_ROOT = DEMO_ROOT.parent
WORK = DEMO_ROOT / "work"
DB_PATH = DEMO_ROOT / "crm.demo.sqlite"

random.seed(20260929)

# ---------------------------------------------------------------------------
# Synthetic businesses: (name, slug, category, email, phone, website,
#                        status, outreach, decision, decision_reason)
# Empty email/phone/website = missing data (feeds the needs_research queue).
# ---------------------------------------------------------------------------
BUSINESSES = [
    ("Piney Woods Plumbing Co.", "piney-woods-plumbing", "Plumbing",
     "office@pineywoods-plumbing.test", "(936) 555-0141", "https://pineywoods-plumbing.test",
     "researched", "uncontacted", "approved", "Full contact path verified; strong local presence."),
    ("Lake Conroe HVAC Pros", "lake-conroe-hvac", "HVAC",
     "service@lakeconroehvac.test", "(936) 555-0117", "https://lakeconroehvac.test",
     "researched", "uncontacted", "approved", "Decision maker identified; site has quote form."),
    ("Sam Houston Roofing LLC", "sam-houston-roofing", "Roofing",
     "", "(936) 555-0188", "https://samhoustonroofing.test",
     "new", "uncontacted", "needs_research", "No email found yet; phone-only contact path."),
    ("Conroe Family Dental", "conroe-family-dental", "Dental",
     "hello@conroefamilydental.test", "(936) 555-0123", "https://conroefamilydental.test",
     "researched", "uncontacted", "approved", "Prior outreach opened; follow up on audit offer."),
    ("East Texas Auto Repair", "east-texas-auto", "Auto repair",
     "shop@easttexasauto.test", "(936) 555-0166", "",
     "new", "uncontacted", "needs_research", "No website found; verify business is still operating."),
    ("Magnolia Landscaping", "magnolia-landscaping", "Landscaping",
     "quotes@magnolialandscaping.test", "", "https://magnolialandscaping.test",
     "new", "uncontacted", "needs_research", "No phone found; email-only path so far."),
    ("Lone Star Legal Group", "lone-star-legal", "Law firm",
     "intake@lonestarlegal.test", "(936) 555-0190", "https://lonestarlegal.test",
     "researched", "uncontacted", "approved", "Managing partner confirmed; site needs speed work."),
    ("Fisherman's Catch Restaurant", "fishermans-catch", "Restaurant",
     "events@fishermanscatch.test", "(936) 555-0134", "https://fishermanscatch.test",
     "new", "uncontacted", "", ""),
    ("I-45 Tire & Lube", "i45-tire-lube", "Auto repair",
     "service@i45tirelube.test", "(936) 555-0152", "https://i45tirelube.test",
     "researched", "uncontacted", "approved", "Parked domain flagged in audit; real business, wrong site."),
    ("Conroe Coffee Roasters", "conroe-coffee", "Coffee roaster",
     "wholesale@conroecoffee.test", "(936) 555-0171", "https://conroecoffee.test",
     "new", "uncontacted", "", ""),
    ("Pine Curtain Cleaning Co.", "pine-curtain-cleaning", "Cleaning",
     "", "(936) 555-0129", "https://pinecurtaincleaning.test",
     "new", "uncontacted", "needs_research", "No email; contact form only."),
    ("Texas Pride Pest Control", "texas-pride-pest", "Pest control",
     "office@texaspridepest.test", "(936) 555-0144", "https://texaspridepest.test",
     "disqualified", "uncontacted", "disqualified", "National franchise; no local decision maker."),
    ("Old Town Barbershop", "old-town-barbershop", "Barbershop",
     "book@oldtownbarber.test", "(936) 555-0108", "https://oldtownbarber.test",
     "researched", "uncontacted", "approved", "Owner-operator; warm reply to first touch."),
    ("Lakeview Fitness Center", "lakeview-fitness", "Gym",
     "join@lakeviewfitness.test", "(936) 555-0161", "https://lakeviewfitness.test",
     "new", "uncontacted", "", ""),
    ("Conroe Print & Ship", "conroe-print-ship", "Printing",
     "orders@conroeprintship.test", "(936) 555-0139", "",
     "new", "uncontacted", "needs_research", "No website; Facebook page only."),
    ("Bluebonnet Boutique", "bluebonnet-boutique", "Retail",
     "hello@bluebonnetboutique.test", "(936) 555-0112", "https://bluebonnetboutique.test",
     "researched", "uncontacted", "approved", "Shopify store; checkout audit opportunity."),
    ("Montgomery County Movers", "montgomery-movers", "Moving",
     "quotes@montgomerymovers.test", "(936) 555-0178", "https://montgomerymovers.test",
     "new", "uncontacted", "", ""),
    ("The Rustic Table Catering", "rustic-table", "Catering",
     "events@rustictable.test", "", "https://rustictable.test",
     "new", "uncontacted", "needs_research", "No phone; Instagram DM may be the path."),
    ("All-Star Youth Sports", "allstar-youth-sports", "Nonprofit",
     "info@allstaryouth.test", "(936) 555-0155", "https://allstaryouth.test",
     "disqualified", "uncontacted", "disqualified", "Nonprofit; no budget for services."),
    ("Conroe Smiles Orthodontics", "conroe-smiles", "Orthodontics",
     "smile@conroesmiles.test", "(936) 555-0126", "https://conroesmiles.test",
     "researched", "uncontacted", "approved", "Two locations; expansion signal."),
    ("Piney Point Pet Grooming", "piney-point-grooming", "Pet grooming",
     "book@pineypointgrooming.test", "(936) 555-0183", "https://pineypointgrooming.test",
     "new", "uncontacted", "", ""),
    ("Highway 105 Hardware", "hwy105-hardware", "Hardware store",
     "counter@hwy105hardware.test", "(936) 555-0149", "",
     "researched", "uncontacted", "needs_research", "Website offline (DNS failure); verify before outreach."),
    ("Sunset Terrace Apartments", "sunset-terrace", "Property management",
     "leasing@sunsetterrace.test", "(936) 555-0119", "https://sunsetterrace.test",
     "new", "uncontacted", "", ""),
    ("Cedar Creek Bookkeeping", "cedar-creek-books", "Bookkeeping",
     "", "", "https://cedarcreekbooks.test",
     "new", "uncontacted", "needs_research", "No email or phone; website contact form only."),
]

FIRST = ["James", "Maria", "Robert", "Linda", "Michael", "Sarah", "David", "Emma",
         "Daniel", "Olivia", "Chris", "Ashley", "Brian", "Megan", "Kevin", "Rachel"]
LAST = ["Carter", "Nguyen", "Brooks", "Reyes", "Foster", "Bishop", "Coleman", "Hayes",
        "Pruitt", "Sandoval", "Mercer", "Vaughn", "Ellison", "Brandt", "Kessler", "Dunlap"]
TITLES = ["Owner", "Owner", "General Manager", "Office Manager", "Managing Partner", "Founder"]

STREETS = ["N Frazier St", "W Davis St", "N Thompson St", "FM 2854", "League Line Rd",
           "N Loop 336 W", "S Frazier St", "W Phillips St", "N Main St", "Teas Nursery Rd"]


def profile_markdown(biz_id: int, biz: tuple, outreach_log: str = "") -> str:
    (name, _slug, category, email, phone, website,
     _status, _outreach, _decision, _reason) = biz
    person = f"{random.choice(FIRST)} {random.choice(LAST)}"
    title = random.choice(TITLES)
    street = f"{random.randint(100, 4999)} {random.choice(STREETS)}, Conroe, TX 77301"
    email_line = email if email else "not found"
    phone_line = phone if phone else "not found"
    site_line = website if website else "no website found"
    log_section = f"\n## Outreach Log\n\n{outreach_log}\n" if outreach_log else ""
    return f"""# {name} — Lead Profile

> Synthetic demo record. Not a real business. Generated for the LeadOps demo.

## Snapshot
{category} serving the Conroe, TX area. Lead #{biz_id} in the demo batch.

## Business Overview
Locally operated {category.lower()} business. Public footprint suggests an owner-operator
or small team; online presence varies from full website to directory listings only.

## Service Offerings
Core {category.lower()} services for residential and small-business customers in Montgomery County.

## Contact Information
- Email: {email_line}
- Phone: {phone_line}
- Website: {site_line}
- Address: {street}

## Contact Decision Makers
{person}, {title}

## Online Presence
Website: {site_line}. Directory listings present; review volume modest.

## Website Presence
{site_line}

## Market Position
Established local option in a competitive Conroe {category.lower()} market.

## Opportunity Assessment
Website and outreach audit could surface quick wins (speed, contact paths, local SEO basics).

## Outreach Angle
Lead with a specific, observable finding from the website audit rather than a generic pitch.

## Next Steps
Verify contact path, then queue for human-reviewed outreach.

## Evidence
- Source: synthetic demo dataset (demo/generate_demo.py); no real business data.
{log_section}"""


# Outreach history for two leads, parsed by the pipeline from profile markdown
# into leadops_outreach_events (drives contacted/replied states in the UI).
OUTREACH_LOGS = {
    6204: """| Date | Channel | Status | Notes |
| ---- | ------- | ------ | ----- |
| 2026-09-20 | email | sent | Intro email with website audit offer |
| 2026-09-22 | email | replied | Interested — asked for a sample audit |
""",
    6213: """| Date | Channel | Status | Notes |
| ---- | ------- | ------ | ----- |
| 2026-09-25 | email | sent | Intro email with website audit offer |
""",
}


def write_inputs() -> None:
    if WORK.exists():
        shutil.rmtree(WORK)
    profiles_root = WORK / "leads" / "profiles"
    notes_dir = WORK / "notes"
    exports_dir = WORK / "outreach" / "exports"
    logs_dir = WORK / "outreach" / "logs"
    profiles_root.mkdir(parents=True)
    notes_dir.mkdir(parents=True)
    exports_dir.mkdir(parents=True)
    logs_dir.mkdir(parents=True)

    # index.csv — the lead list the pipeline ingests
    with (WORK / "leads" / "index.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["LeadID", "Name", "Batch", "Status", "Disqualified",
                         "OutreachStatus", "ContactPath", "ContactSearch", "Email",
                         "Phone", "Website", "ContactForm", "SocialMedia",
                         "WebsiteStatus", "SocialChecked", "Source", "Updated",
                         "ProfilePath"])
        for i, biz in enumerate(BUSINESSES):
            lead_id = 6201 + i
            (name, slug, _cat, email, phone, website,
             status, outreach, _dec, _reason) = biz
            writer.writerow([
                lead_id, name, "demo-1", status,
                "yes" if status == "disqualified" else "",
                outreach,
                "email" if email else ("phone" if phone else "research"),
                name, email, phone, website,
                "yes" if website else "", "", "", "", "demo",
                "2026-09-29", f"leads/profiles/{lead_id}-{slug}/profile.md",
            ])

    # profile markdown per lead: leads/profiles/<id>-<slug>/profile.md
    for i, biz in enumerate(BUSINESSES):
        lead_id = 6201 + i
        slug = biz[1]
        prof_dir = profiles_root / f"{lead_id}-{slug}"
        prof_dir.mkdir(parents=True)
        (prof_dir / "profile.md").write_text(
            profile_markdown(lead_id, biz, OUTREACH_LOGS.get(lead_id, "")),
            encoding="utf-8")

    # human review decisions — the human-in-the-loop evidence
    with (notes_dir / "leadops-review-decisions.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["lead_id", "decision", "reason", "source_file"])
        for i, biz in enumerate(BUSINESSES):
            lead_id = 6201 + i
            _name, _slug, _cat, _e, _p, _w, _s, _o, decision, reason = biz
            if decision:
                writer.writerow([lead_id, decision, reason, "demo:human-review"])

    # contact log — outreach history that drives contacted/replied states
    (logs_dir / "contact-log.md").write_text(
        """# Contact Log (demo)

| Date | Lead | Batch | Channel | Status | Notes |
| ---- | ---- | ----- | ------- | ------ | ----- |
| 2026-09-20 | Conroe Family Dental | demo-1 | email | sent | Intro email with website audit offer |
| 2026-09-22 | Conroe Family Dental | demo-1 | email | replied | Interested — asked for a sample audit |
| 2026-09-25 | Old Town Barbershop | demo-1 | email | sent | Intro email with website audit offer |
""",
        encoding="utf-8")

    # sample audit findings (dict wrapper shape the pipeline expects)
    audit_payload = {
        "auditInfo": {
            "auditDate": "2026-09-28T10:00:00",
            "range": "6201-6224",
            "criteria": ["website reachability", "domain placeholder detection"],
            "dedupeList": "",
        },
        "summary": {
            "recommendation": "Verify offline/parked sites before outreach; 3 leads flagged.",
            "diamondCount": 3,
        },
        "diamondLeads": [
            {"LeadID": 6209, "Name": "I-45 Tire & Lube",
             "Email": "service@i45tirelube.test", "Website": "https://i45tirelube.test",
             "IssueType": "PARKED",
             "IssueDescription": "Parked/placeholder page detected - domain content does not align with lead identity",
             "DiamondWorthy": True},
            {"LeadID": 6205, "Name": "East Texas Auto Repair",
             "Email": "shop@easttexasauto.test", "Website": "",
             "IssueType": "OFFLINE",
             "IssueDescription": "No website found - business may rely on directory listings only",
             "DiamondWorthy": True},
            {"LeadID": 6222, "Name": "Highway 105 Hardware",
             "Email": "counter@hwy105hardware.test", "Website": "",
             "IssueType": "OFFLINE",
             "IssueDescription": "Website offline - DNS resolution failed (getaddrinfo failed)",
             "DiamondWorthy": True},
        ],
        "nonDiamondLeads": [],
    }
    (exports_dir / "diamond-audit-demo-1.json").write_text(
        json.dumps(audit_payload, indent=2), encoding="utf-8")


def run_pipeline() -> None:
    # The pipeline resolves inputs relative to the bootstrap script's directory,
    # so it runs from a copy placed inside demo/work/.
    shutil.copy(REPO_ROOT / "bootstrap_leadops_sqlite.py",
                WORK / "bootstrap_leadops_sqlite.py")
    if DB_PATH.exists():
        DB_PATH.unlink()
    sqlite3.connect(DB_PATH).close()  # pipeline requires the file to exist
    cmd = [sys.executable, str(WORK / "bootstrap_leadops_sqlite.py"),
           "--db", str(DB_PATH), "--no-backup",
           "--report", str(DEMO_ROOT / "demo-report.md")]
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=WORK)

    # The UI's schema check requires this table; it is normally built by the
    # optional --deep-index vector pass (needs local embedding models).
    # The demo ships it empty: vector search shows no results, everything else works.
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leadops_vector_embeddings (
            lead_id INTEGER,
            doc_id TEXT,
            doc_type TEXT,
            embedding_model TEXT,
            embedding_dim INTEGER,
            vector_blob BLOB,
            indexed_at TEXT
        )
    """)
    conn.commit()

    checks = {
        "leads": "SELECT COUNT(*) FROM leadops_leads",
        "profiles": "SELECT COUNT(*) FROM leadops_profiles",
        "contacts": "SELECT COUNT(*) FROM leadops_contacts",
        "review decisions": "SELECT COUNT(*) FROM leadops_review_decisions",
        "audit findings": "SELECT COUNT(*) FROM leadops_audit_findings",
        "send_now queue": "SELECT COUNT(*) FROM leadops_v_send_now",
        "needs_research queue": "SELECT COUNT(*) FROM leadops_v_needs_research",
    }
    print("\nDemo database checks:")
    for label, sql in checks.items():
        n = conn.execute(sql).fetchone()[0]
        print(f"  {label:20s} {n}")
    conn.close()
    populate_fts()


def populate_fts() -> None:
    """Index the demo profile markdown files for free-text search.

    The real pipeline only builds leadops_search_documents / leadops_search_fts
    under --deep-index (needs local embedding models for the vector side).
    The demo skips that, but the Streamlit UI's "just type to search" box only
    reads the FTS5 table, so we run the pipeline's own search indexer here.
    It reads the 24 profile markdown files' content from leadops_profiles and
    inserts docs + FTS rows with the exact same schema/conventions.
    """
    spec = importlib.util.spec_from_file_location(
        "demo_bootstrap", str(WORK / "bootstrap_leadops_sqlite.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module.__name__] = module  # dataclasses needs the module registered
    spec.loader.exec_module(module)
    conn = sqlite3.connect(DB_PATH)
    try:
        n = module.insert_search_documents(conn)
        conn.commit()
    finally:
        conn.close()
    print(f"\nFTS search documents indexed: {n} (from leadops_profiles.raw_markdown)")


def main() -> None:
    print("Generating synthetic demo inputs...")
    write_inputs()
    print("Running the real LeadOps pipeline on demo inputs...")
    run_pipeline()
    print(f"""
Done. Demo database: {DB_PATH}

Launch the UI:
  cp {DB_PATH} {REPO_ROOT / 'crm.sqlite'}   # crm.sqlite is gitignored
  cd {REPO_ROOT} && streamlit run app.py

Every record is synthetic (see demo/generate_demo.py). No real business data.
""")


if __name__ == "__main__":
    main()

# Case Study: LeadOps — AI-Assisted Lead Intelligence Pipeline

> **Status: DRAFT.** All operational numbers below come from the synthetic demo dataset (24 fictional businesses). No production metrics are claimed. Sections marked [NEEDS PRODUCTION DATA] await real-world figures.

## What it is

LeadOps is an AI-assisted lead intelligence platform that turns fragmented business data into reviewable outreach decisions. It combines SQLite structured storage, vector retrieval over notes and inbox context, mailbox parsing, lead scoring, and a Streamlit human-in-the-loop review UI.

The system is designed around traceability, not blind automation: it ranks and explains next actions while preserving evidence for human review.

## The problem

Local-business outreach runs on messy, scattered signals: tax records, directory listings, inbox threads, website audits, contact notes. Handled as a flat list, this means hours of manual triage, missed follow-ups, and no systematic way to prioritize. LeadOps normalizes these signals into consistent operational profiles and generates scored, reviewable queues.

## Architecture

| Component | File | What it does |
|---|---|---|
| Operator dashboard | `app.py` | Streamlit entry point |
| Review UI | `leads_ui.py` (1,215 lines) | Lead review and management surface |
| Retrieval | `leadops_retrieve.py` (919 lines) | Vector + SQLite retrieval across records and notes |
| Next-action engine | `leadops_next_action_candidates.py` (183 lines) | Generates human-reviewed action candidates |
| Bootstrap | `bootstrap_leadops_sqlite.py` | Builds the database from raw inputs |

Supporting: `core_utils.py`, `leads_network.py`, audit JSON outputs, and a test suite.

## The demo (synthetic data, fully reproducible)

The repo ships a demo generator (`demo/generate_demo.py`) that builds a SQLite database from 24 fictional Conroe, TX businesses and runs the **real** bootstrap pipeline against them — so the UI shows the actual workflow, not mockups.

Verified demo dataset contents (from `demo/demo-report.md`, generated 2026-10-02):

- **24** leads, **24** profiles, **84** contacts
- **18** human review decisions
- **3** audit findings from **1** audit run
- **24** entity-match rows, **24** reconciled outreach statuses
- Queues: send-ready, needs-research, review decisions, audit findings

Verification: 7 smoke tests passed; GitHub Actions CI green. A 2-minute narrated video tour of the live demo is linked from the README.

Vector search is disabled in the demo (no embedding models ship with it); the retrieval path is exercised against SQLite.

## Methodology: human-in-the-loop by design

Every consequential output passes through a review queue:

1. **Ingest** — raw signals normalized into profiles (business facts, contacts, outreach history).
2. **Score** — queues ranked by configurable scoring, not black-box ordering.
3. **Review** — the operator sees the evidence behind each recommendation (source records, match rationale) and approves, edits, or rejects.
4. **Audit** — findings logged with provenance; wrong-entity and contact-path mistakes treated as first-class review problems.

Nothing sends, publishes, or mutates without explicit operator action.

## [NEEDS PRODUCTION DATA] Operational impact

The following sections require figures from production use. They are structural placeholders, not claims.

- **Volume:** leads processed per week/month in live operation.
- **Triage time:** operator hours before vs. after LeadOps adoption (before/after estimate).
- **Decision quality:** review-decision outcomes (approved / edited / rejected rates).
- **Outreach results:** send volume, response rates attributable to prioritized queues.

## Honest limitations

- The demo uses 24 fictional businesses; production scale and noise are unproven in this repo.
- "Hours saved" requires a before/after measurement that has not been taken.
- Vector retrieval is architected but not exercised in the demo (no embedding models included).
- This is an operator's tool, not autonomous outreach — it does not send anything on its own.

# LeadOps / SMB Growth OS

AI-assisted lead intelligence platform for turning fragmented business data into reviewable outreach decisions.

This repo shows the data pipeline behind a local-business growth workflow: profile normalization, mailbox parsing, vector retrieval, contact-path extraction, queue generation, and Streamlit review surfaces. It is designed around traceability, not blind automation: the system helps rank and explain next actions while preserving evidence for human review.

## Why It Matters

- Normalizes noisy lead records into consistent operational profiles.
- Combines structured SQLite data with semantic retrieval over lead notes and inbox context.
- Scores queues so outreach work can be prioritized instead of handled as a flat list.
- Treats wrong-entity and contact-path mistakes as first-class review problems.

## Proof Artifacts

| Artifact | What it shows |
| --- | --- |
| `app.py` | Streamlit operator dashboard entry point |
| `leads_ui.py` | Lead review and management surface |
| `leadops_retrieve.py` | Retrieval path across lead records and notes |
| `leadops_next_action_candidates.py` | Human-reviewed next-action candidate generation |
| `audits/diamond-audit-6200-6599-2026-03-18.json` | Sanitized example structured audit output |
| `audits/batch-3-security-hardening-deep-audit.json` | Sanitized example deep-audit result shape |

## Features

**Lead Processing Pipeline**
- Profile parsing and normalization
- Mailbox sync and email thread extraction
- Queue generation with priority scoring
- Safe-send ranking to optimize outreach timing
- Contact-path extraction and deduplication

**Vector Search**
- Dual embedding lanes: Qwen3 0.6B (fast) + Qwen3 4B (quality)
- SQLite vector store with policy-switchable retrieval
- Semantic similarity matching across lead records

**Streamlit UI**
- Multipage dashboard with schema validation
- Audience classification
- Send-time scoring
- Wrong-entity hunt retrieval workflows
- Real-time lead research automation

**Integrations**
- SPF/DKIM signal analysis from inbox parsing
- Automated audit scoring across 8,400+ lead records
- Next-action sequencing that produces reviewable recommendations for human approval

## Portfolio Boundary

This is a sanitized public slice of a larger local-business operating workspace. The code is published to show the schema, retrieval, queueing, and review logic; a full local run expects private lead profiles, mailbox exports, and SQLite artifacts that are intentionally not included in this repo.

## Usage

```bash
# Install dependencies
python -m pip install -r requirements.txt

# Bootstrap the SQLite database after wiring private lead/source data paths
python bootstrap_leadops_sqlite.py

# Run the Streamlit UI
streamlit run app.py

# Or use the leads UI
streamlit run leads_ui.py
```

## Public Data Boundary

This public repo contains the application code and sanitized example audit outputs, not the private CRM database, mailbox exports, outreach logs, model files, or full lead corpus. Local UI runs expect a `crm.sqlite` database generated from your own private/source data. The tracked `audits/` files are included only to show the shape of reviewable evidence, with business emails redacted.

## Files

- `app.py` — main Streamlit multipage application
- `core_utils.py` — shared database/vector utilities for the Streamlit pages
- `leads_ui.py` — lead management UI
- `leads_network.py` — graph visualization of lead relationships
- `bootstrap_leadops_sqlite.py` — database initialization and schema setup
- `leadops_retrieve.py` — retrieval and search logic
- `leadops_next_action_candidates.py` — human-reviewed next-action candidate generation

## Recruiter Reading Guide

Start with `leadops_retrieve.py` for the retrieval path, then `app.py` and `leads_ui.py` for the operator-facing workflow. The strongest signal is the combination of automation and review discipline: the system narrows work, but it does not pretend messy lead data is cleaner than it is.

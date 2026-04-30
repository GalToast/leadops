# LeadOps / SMB Growth OS

AI-assisted lead intelligence platform for turning fragmented business data into reviewable outreach decisions.

This repo shows the data pipeline behind a local-business growth workflow: profile normalization, mailbox parsing, vector retrieval, contact-path extraction, queue generation, and Streamlit review surfaces. It is designed around traceability, not blind automation: the system helps rank and explain next actions while preserving evidence for human review.

## Why It Matters

- Normalizes noisy lead records into consistent operational profiles.
- Combines structured SQLite data with semantic retrieval over lead notes and inbox context.
- Scores queues so outreach work can be prioritized instead of handled as a flat list.
- Treats wrong-entity and contact-path mistakes as first-class review problems.

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
- Outreach sequencing with zero manual intervention

## Usage

```bash
# Bootstrap the SQLite database
python bootstrap_leadops_sqlite.py

# Run the Streamlit UI
streamlit run app.py

# Or use the leads UI
streamlit run leads_ui.py
```

## Files

- `app.py` — main Streamlit multipage application
- `leads_ui.py` — lead management UI
- `leads_network.py` — graph visualization of lead relationships
- `bootstrap_leadops_sqlite.py` — database initialization and schema setup
- `leadops_retrieve.py` — retrieval and search logic
- `leadops_draft_candidates.py` — outreach draft generation

## Recruiter Reading Guide

Start with `leadops_retrieve.py` for the retrieval path, then `app.py` and `leads_ui.py` for the operator-facing workflow. The strongest signal is the combination of automation and review discipline: the system narrows work, but it does not pretend messy lead data is cleaner than it is.

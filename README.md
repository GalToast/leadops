# LeadOps / SMB Growth OS

End-to-end AI-assisted lead intelligence platform for small-to-medium business sales and outreach.

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

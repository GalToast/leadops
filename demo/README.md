# LeadOps demo

A self-contained, synthetic walkthrough of the LeadOps pipeline. One command builds
a demo database from fake data with the **real** bootstrap script, so you can click
through the actual workflow: lead queues, human review decisions, audit findings,
contact states, and outreach history.

**Every record is fictional.** Names, emails, phones, and websites are generated
(`.test` domains, `(936) 555-01xx` numbers). No real business data anywhere.

## Quickstart

```bash
python -m pip install -r requirements.txt   # numpy, pandas, plotly, pyvis, requests, streamlit
python demo/generate_demo.py                # builds demo/crm.demo.sqlite from synthetic inputs
cp demo/crm.demo.sqlite crm.sqlite          # crm.sqlite is gitignored; the UI reads it here
streamlit run app.py
```

Then open the URL Streamlit prints (usually http://localhost:8501).

## What the demo contains

24 fictional Conroe, TX-area businesses in realistic workflow states:

| Slice | Count | What it shows |
|---|---|---|
| Leads / profiles / contacts | 24 / 24 / 84 | Ingestion: CSV index → lead rows, profile markdown → structured profiles, contact extraction |
| Ready-to-send queue | 12 | Outreach-ready leads gated by identity-match confidence, email validity, and enrichment |
| Needs-research queue | 15 | Leads missing email/phone/website, or still in `new` status, with the reason shown |
| Human review decisions | 18 | The human-in-the-loop: `approved`, `needs_research`, `disqualified` with reasons |
| Audit findings | 3 | Parked-domain and offline-website findings from a sample diamond audit |
| Outreach history | 3 events | Two emails sent, one reply received — contacted leads are correctly excluded from the send queue |
| Disqualified | 2 | Franchise with no local decision maker; nonprofit with no budget |

## How it works

`demo/generate_demo.py` (stdlib only, deterministic) writes synthetic inputs to
`demo/work/` — `leads/index.csv`, 24 profile-markdown files, a review-decisions CSV,
a contact log, and a sample audit JSON — then copies the repo's real
`bootstrap_leadops_sqlite.py` next to them and runs it. The result is a real
`crm.demo.sqlite` with the full schema (41 tables, 63 views) the UI expects.

One deliberate shortcut: the pipeline's optional `--deep-index` vector pass needs
local embedding models, so the demo creates an empty `leadops_vector_embeddings`
table. The UI's schema check passes; semantic vector search simply returns nothing
in the demo. Everything else — keyword search, queues, filters, detail views — works.

To rebuild from scratch, delete `demo/work/`, `demo/crm.demo.sqlite`, and
`demo/demo-report.md`, then re-run the generator.

## Demo script (for the walkthrough video)

1. **Queues first** — "Ready to Send (12)" vs "Need Research (15)": the system triages.
2. **Why a lead is ready** — open one: contact path, enrichment, identity-match confidence.
3. **Why a lead isn't** — a needs-research lead shows exactly what's missing.
4. **Human in the loop** — review decisions: approved / needs_research / disqualified, with reasons.
5. **Audits feed the queue** — 3 findings (parked domain, offline sites). Note the nuance: the parked-domain lead (I-45 Tire & Lube) is held out of sending **not** by the audit finding — a reviewer approved it as "real business, wrong site" — but by low entity-match confidence (37/low): the domain doesn't align with the business identity, so the send queue's confidence gate (`high`/`medium` only) keeps it out.
6. **Outreach states** — two emails sent, one lead replied; the contacted leads leave the send queue automatically.
7. **Search** — keyword search across the corpus (vector search disabled in demo).

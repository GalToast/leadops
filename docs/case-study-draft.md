> **DRAFT — needs real numbers.** Extracted 2026-10-09 from the temp workspace.
> Structure is sound; metrics (leads processed, hours saved) await the production
> crm.sqlite. Do not publish as finished.

# Case Study: LeadOps — Scaling AI Intelligence to 8.4k Records

## Executive Summary
**Challenge:** How to process and verify thousands of unstructured local business records without a massive manual operations team.
**Solution:** A custom AI-assisted operating system leveraging model-assisted review, browser automation, and vector-based semantic classification.
**Result:** 8,406 records processed through an automated pipeline with human review gates for uncertain signals, producing a structured business intelligence corpus.

## Technical Architecture
### 1. The Intake Engine
- **Source Aggregation:** Automated ingestion of messy public signals (tax records, franchise filings, local directories).
- **Browser Automation:** Playwright-based research agents for website checks, public signal collection, and repeatable review handoffs.

### 2. Semantic Intelligence
- **Dual Embedding Pipeline:** A fast classification lane paired with a higher-fidelity semantic review lane for neighborhood clustering.
- **Local RAG:** Integrated SQLite vector store for retrieval, comparison, and cross-referencing.

### 3. Verification & Signal Analysis
- **Technical Audits:** Automated extraction of SPF/DKIM data to rank "safe-send" outreach candidates.
- **Identity Resolution:** Deduplication and reconciliation logic for business entities across multiple disjointed sources.

## Impact & Scale
- **Volume:** 8,406 records.
- **Efficiency:** Automated discovery and enrichment with operator review queues for edge cases.
- **Utility:** Powers the **Montgomery County Semantic Explorer**, a living visualization of local economic topology.

---
*Built by McCullough Digital | mccullough.cloud*

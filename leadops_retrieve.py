from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "crm.sqlite"
SEMANTIC_SCRIPT = REPO_ROOT / "scripts" / "maintenance" / "semantic_search.py"
CORPUS_QUERY_SCRIPT = REPO_ROOT / "scripts" / "maintenance" / "leadops_corpus_query.py"


WORKFLOWS = {
    "similar": {
        "query": "{query}",
        "fts": None,
        "semantic_port": 8096,
        "description": "General similar-lead retrieval over the default raw 0.6B corpus.",
    },
    "audit-analogs": {
        "query": "business website audit with trust issues, broken contact paths, thin content, wrong website, redirect, or domain problems related to {query}",
        "fts": "redirect OR domain OR trust OR contact OR broken OR hijacked OR wrong website",
        "semantic_port": 8097,
        "description": "Find leads with similar audit and trust failures.",
    },
    "mission-style": {
        "query": "faith-based nonprofit ministry with outreach, donations, community help, recovery, missions, or service related to {query}",
        "fts": "church OR ministry OR nonprofit OR donate OR missions OR outreach",
        "semantic_port": 8098,
        "description": "Find mission-style organizations and ministries.",
    },
    "wrong-entity-hunt": {
        "query": "lead with wrong website, hijacked domain, unrelated mapping, redirect issue, or entity mismatch related to {query}",
        "fts": "wrong website OR hijacked OR redirect OR unrelated OR mismatch OR wrong domain",
        "semantic_port": 8099,
        "description": "Find likely wrong-entity or wrong-domain cases.",
    },
}

NOISY_PREVIEW_PREFIXES = (
    "lead id:",
    "lead profile:",
    "status:",
    "outreach status:",
    "contact search:",
    "contact path:",
    "social check:",
    "batch:",
    "batch line:",
    "email:",
    "website:",
    "contact form:",
    "address:",
    "phone:",
    "social media:",
    "naics:",
    "distance (zip centroid):",
    "decision maker:",
    "source:",
    "phone:",
    "decision:",
    "reason:",
    "platforms searched:",
    "last updated:",
    "entity match:",
    "canonical truth",
    "current task",
    "suggested next steps",
    "rolling log",
)


def run_json_command(cmd: list[str]) -> object:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    completed = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return json.loads(completed.stdout)


def emit(payload: object, as_json: bool) -> None:
    encoding = sys.stdout.encoding or "utf-8"
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    if as_json:
        print(safe_text)
        return
    print(safe_text)


def corpus_health(db_path: Path) -> dict[str, object]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM leadops_search_documents) AS search_documents,
                (SELECT COUNT(*) FROM leadops_vector_index_queue) AS vector_queue,
                (SELECT COUNT(*) FROM leadops_vector_embeddings) AS vector_embeddings,
                (SELECT COUNT(*) FROM leadops_vector_index_queue WHERE lower(COALESCE(embedding_status, 'pending')) <> 'embedded') AS pending_docs,
                (SELECT COUNT(*) FROM leadops_v_send_now_reviewed_mailbox_safe) AS safe_send_ready,
                (SELECT COUNT(*) FROM leadops_v_research_now) AS research_now,
                (SELECT COUNT(*) FROM leadops_v_do_not_work) AS do_not_work,
                (SELECT MAX(indexed_at) FROM leadops_vector_embeddings) AS last_indexed_at
            """
        ).fetchone()
        return dict(row)


def table_columns(conn: sqlite3.Connection, name: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({name})").fetchall()
    except sqlite3.DatabaseError:
        return set()
    return {str(row["name"]) for row in rows}


def select_existing(
    conn: sqlite3.Connection,
    source: str,
    desired: list[str],
    where_sql: str,
    params: tuple[object, ...],
) -> dict[str, object]:
    columns = table_columns(conn, source)
    chosen = [column for column in desired if column in columns]
    if not chosen:
        return {}
    query = f"SELECT {', '.join(chosen)} FROM {source} {where_sql}"
    row = conn.execute(query, params).fetchone()
    return dict(row) if row else {}


def lead_context(db_path: Path, lead_id: int) -> dict[str, object]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        lead = select_existing(
            conn,
            "leadops_leads",
            [
                "lead_id",
                "name",
                "status",
                "outreach_status",
                "email",
                "website",
                "contact_form",
                "phone",
                "source",
                "batch",
                "batch_line",
            ],
            "WHERE lead_id = ?",
            (lead_id,),
        )
        entity_match = select_existing(
            conn,
            "leadops_entity_match",
            ["lead_id", "confidence_bucket", "match_score", "rationale"],
            "WHERE lead_id = ?",
            (lead_id,),
        )
        review = select_existing(
            conn,
            "leadops_v_latest_review_decision",
            [
                "lead_id",
                "decision",
                "decision_group",
                "decision_reason",
                "decision_note",
                "updated_at",
            ],
            "WHERE lead_id = ?",
            (lead_id,),
        )
        outreach = select_existing(
            conn,
            "leadops_v_outreach_contact_state",
            [
                "lead_id",
                "overall_contact_state",
                "email_lane_status",
                "contact_form_status",
                "email_contacted_rows",
                "contact_form_contacted_rows",
            ],
            "WHERE lead_id = ?",
            (lead_id,),
        )
        return {
            "lead": lead,
            "entity_match": entity_match,
            "latest_review_decision": review,
            "outreach_contact_state": outreach,
        }


def trim_preview(text: str, limit: int = 320) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)] + "…"


def clean_preview_text(text: str) -> str:
    cleaned = (text or "").replace("\ufeff", " ").replace("\x00", " ")
    cleaned = re.sub(r"^[?#\s\ufffd]+", "", cleaned)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = cleaned.replace("\ufffd", "")
    return cleaned


def is_noisy_preview_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if re.fullmatch(r"[-=#>*`~_ ]{2,}", stripped):
        return True
    lowered = stripped.lower()
    if lowered in {"---", "```", "notes", "contact info", "social presence"}:
        return True
    return any(lowered.startswith(prefix) for prefix in NOISY_PREVIEW_PREFIXES)


def extract_named_section(text: str, section_names: tuple[str, ...]) -> str:
    cleaned = clean_preview_text(text)
    if not cleaned.strip():
        return ""
    headings = "|".join(re.escape(name) for name in section_names)
    pattern = re.compile(
        rf"(?ims)^[ \t]*##+[ \t]+(?:{headings})[ \t]*\n(?P<body>.*?)(?=^[ \t]*##+[ \t]+\S|\Z)"
    )
    parts: list[str] = []
    for match in pattern.finditer(cleaned):
        body = match.group("body").strip()
        if body:
            parts.append(body)
    return "\n\n".join(parts)


def best_preview_excerpt(text: str, limit: int = 320) -> str:
    cleaned = clean_preview_text(text)
    if not cleaned.strip():
        return ""

    preferred = extract_named_section(cleaned, ("Snapshot", "Observations", "Website audit", "Evidence"))
    candidate = preferred or cleaned

    lines = candidate.splitlines()
    useful_lines: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        line = re.sub(r"^[#>*`\-]+\s*", "", line)
        line = re.sub(r"\s+", " ", line).strip(" \t-:|")
        if is_noisy_preview_line(line):
            continue
        if len(line) < 18 and len(line.split()) < 4:
            continue
        useful_lines.append(line)
        if len(" ".join(useful_lines)) >= limit * 2:
            break

    if useful_lines:
        return trim_preview(" ".join(useful_lines), limit)
    return trim_preview(cleaned, limit)


def clean_fts_snippet(snippet: str, limit: int = 240) -> str:
    cleaned = clean_preview_text(snippet)
    cleaned = re.sub(r"\[[^\]]+\]", lambda m: m.group(0).strip("[]"), cleaned)
    cleaned = cleaned.replace("�", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return best_preview_excerpt(cleaned, limit)


def resolve_source_path(source_path: str) -> Path | None:
    if not source_path:
        return None
    candidate = REPO_ROOT / "leads" / source_path
    if candidate.exists():
        return candidate
    candidate = REPO_ROOT / source_path
    if candidate.exists():
        return candidate
    return None


def file_preview_from_source_path(source_path: str, limit: int = 320) -> str:
    resolved = resolve_source_path(source_path)
    if not resolved:
        return ""
    try:
        text = resolved.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    return best_preview_excerpt(text, limit)


def file_text_from_source_path(source_path: str) -> str:
    resolved = resolve_source_path(source_path)
    if not resolved:
        return ""
    try:
        return resolved.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def fetch_doc_previews(conn: sqlite3.Connection, lead_id: int, limit: int) -> list[dict[str, object]]:
    columns = table_columns(conn, "leadops_search_documents")
    if not columns:
        return []
    body_length_expr = "body_length" if "body_length" in columns else "NULL AS body_length"
    body_column = "body"
    if body_column not in columns:
        body_column = "content" if "content" in columns else None
    if not body_column:
        rows = []
        fallback_query = """
            SELECT
                id,
                lead_id,
                doc_type,
                title,
                source_path,
                {body_length_expr}
            FROM leadops_search_documents
            WHERE lead_id = ?
            ORDER BY
                CASE doc_type
                    WHEN 'profile_markdown' THEN 0
                    ELSE 1
                END,
                source_path ASC
            LIMIT ?
        """.format(body_length_expr=body_length_expr)
        for row in conn.execute(fallback_query, (lead_id, limit)).fetchall():
            rows.append(
                {
                    "id": row["id"],
                    "lead_id": row["lead_id"],
                    "doc_type": row["doc_type"],
                    "title": row["title"],
                    "source_path": row["source_path"],
                    "body_length": row["body_length"],
                    "preview": file_preview_from_source_path(str(row["source_path"] or "")),
                }
            )
        return rows
    query = f"""
        SELECT
            id,
            lead_id,
            doc_type,
            title,
            source_path,
            {body_length_expr},
            {body_column} AS body
        FROM leadops_search_documents
        WHERE lead_id = ?
        ORDER BY
            CASE doc_type
                WHEN 'profile_markdown' THEN 0
                ELSE 1
            END,
            source_path ASC
        LIMIT ?
    """
    rows = []
    for row in conn.execute(query, (lead_id, limit)).fetchall():
        rows.append(
            {
                "id": row["id"],
                "lead_id": row["lead_id"],
                "doc_type": row["doc_type"],
                "title": row["title"],
                "source_path": row["source_path"],
                "body_length": row["body_length"],
                "preview": best_preview_excerpt(str(row["body"] or "")),
            }
        )
    return rows


def enrich_semantic_results(
    conn: sqlite3.Connection,
    semantic_results: list[dict[str, object]],
    preview_chars: int = 240,
) -> list[dict[str, object]]:
    columns = table_columns(conn, "leadops_search_documents")
    if not columns:
        return semantic_results
    body_column = "body"
    if body_column not in columns:
        body_column = "content" if "content" in columns else None
    if not body_column:
        enriched: list[dict[str, object]] = []
        for row in semantic_results:
            extra = dict(row)
            extra["preview"] = file_preview_from_source_path(str(row.get("source_path") or ""), preview_chars)
            enriched.append(extra)
        return enriched
    enriched: list[dict[str, object]] = []
    for row in semantic_results:
        extra = dict(row)
        source_path = row.get("source_path")
        if source_path:
            doc = conn.execute(
                f"""
                SELECT title, {body_column} AS body
                FROM leadops_search_documents
                WHERE source_path = ?
                LIMIT 1
                """,
                (source_path,),
            ).fetchone()
            if doc:
                extra["preview"] = best_preview_excerpt(str(doc["body"] or ""), preview_chars)
                if not extra.get("title") and doc["title"]:
                    extra["title"] = doc["title"]
            elif source_path:
                extra["preview"] = file_preview_from_source_path(str(source_path), preview_chars)
        enriched.append(extra)
    return enriched


def past_winner_map(conn: sqlite3.Connection) -> dict[int, dict[str, object]]:
    columns = table_columns(conn, "leadops_v_outreach_contact_state")
    if not columns:
        return {}
    desired = [
        "lead_id",
        "name",
        "overall_contact_state",
        "email_lane_status",
        "draft_rows",
        "email_contacted_rows",
        "reconciled_status_reason",
    ]
    chosen = [column for column in desired if column in columns]
    if "lead_id" not in chosen:
        return {}
    query = f"""
        SELECT {", ".join(chosen)}
        FROM leadops_v_outreach_contact_state
        WHERE
            COALESCE(draft_rows, 0) > 0
            OR COALESCE(email_contacted_rows, 0) > 0
            OR lower(COALESCE(email_lane_status, '')) IN ('drafted', 'sent', 'bounced', 'replied', 'opt-out')
            OR lower(COALESCE(overall_contact_state, '')) IN ('drafted', 'sent', 'bounced', 'replied', 'opt-out')
    """
    review_columns = table_columns(conn, "leadops_v_latest_review_decision")
    review_map: dict[int, dict[str, object]] = {}
    if review_columns and "lead_id" in review_columns:
        review_query = """
            SELECT lead_id, decision, reason
            FROM leadops_v_latest_review_decision
        """
        for row in conn.execute(review_query).fetchall():
            review_map[int(row["lead_id"])] = {
                "decision": row["decision"],
                "reason": row["reason"],
            }

    winners: dict[int, dict[str, object]] = {}
    for row in conn.execute(query).fetchall():
        lead_id = int(row["lead_id"])
        winner = dict(row)
        if lead_id in review_map:
            winner["review_decision"] = review_map[lead_id].get("decision")
            winner["review_reason"] = review_map[lead_id].get("reason")
        winners[lead_id] = winner
    return winners


def similar_past_winners(
    semantic_results: list[dict[str, object]],
    winners: dict[int, dict[str, object]],
    current_lead_id: int,
    limit: int,
) -> list[dict[str, object]]:
    picked: list[dict[str, object]] = []
    seen: set[int] = set()
    for row in semantic_results:
        lead_id = row.get("lead_id")
        if not isinstance(lead_id, int):
            continue
        if lead_id == current_lead_id or lead_id in seen or lead_id not in winners:
            continue
        merged = dict(row)
        merged["winner_context"] = winners[lead_id]
        preview = str(merged.get("preview") or "").strip()
        if preview:
            merged["winner_rationale"] = trim_preview(preview, 140)
        picked.append(merged)
        seen.add(lead_id)
        if len(picked) >= limit:
            break
    return picked


def enrich_fts_results(conn: sqlite3.Connection, fts_results: object, preview_chars: int = 240) -> object:
    if not isinstance(fts_results, list):
        return fts_results
    columns = table_columns(conn, "leadops_search_documents")
    body_column = "body"
    if body_column not in columns:
        body_column = "content" if "content" in columns else None
    enriched: list[dict[str, object]] = []
    for row in fts_results:
        if not isinstance(row, dict):
            enriched.append(row)
            continue
        extra = dict(row)
        source_path = str(row.get("source_path") or "")
        preview = ""
        if source_path and body_column:
            doc = conn.execute(
                f"""
                SELECT {body_column} AS body
                FROM leadops_search_documents
                WHERE source_path = ?
                LIMIT 1
                """,
                (source_path,),
            ).fetchone()
            if doc:
                preview = best_preview_excerpt(str(doc["body"] or ""), preview_chars)
        if not preview and source_path:
            preview = file_preview_from_source_path(source_path, preview_chars)
        if not preview:
            preview = clean_fts_snippet(str(row.get("snippet") or ""), preview_chars)
        extra["fts_preview"] = preview
        enriched.append(extra)
    return enriched


def fetch_audit_findings_summary(conn: sqlite3.Connection, lead_id: int, limit: int = 5) -> list[dict[str, object]]:
    columns = table_columns(conn, "leadops_v_audit_findings_actionable")
    needed = [
        "lead_id",
        "severity",
        "issue_type_norm",
        "issue_description",
        "verified_live",
        "verification_method",
        "evidence_path",
        "next_action",
        "diamond_worthy",
        "note",
    ]
    if not columns or "lead_id" not in columns:
        return []
    chosen = [column for column in needed if column in columns]
    order_parts = []
    if "diamond_worthy" in columns:
        order_parts.append("diamond_worthy DESC")
    if "severity" in columns:
        order_parts.append(
            """CASE severity
                WHEN 'critical' THEN 4
                WHEN 'high' THEN 3
                WHEN 'medium' THEN 2
                WHEN 'low' THEN 1
                ELSE 0
            END DESC"""
        )
    if "verified_live" in columns:
        order_parts.append("verified_live DESC")
    order_parts.append("lead_id ASC")
    query = f"""
        SELECT {', '.join(chosen)}
        FROM leadops_v_audit_findings_actionable
        WHERE lead_id = ?
        ORDER BY {', '.join(order_parts)}
        LIMIT ?
    """
    return [dict(row) for row in conn.execute(query, (lead_id, limit)).fetchall()]


def infer_issue_from_text(text: str) -> dict[str, object] | None:
    lowered = (text or "").lower()
    patterns = [
        ("site_outage", "The website domain does not resolve, so people currently cannot reach the site.", ["could not resolve host", "dns resolution fails", "domain does not resolve", "site is unreachable"]),
        ("site_outage", "The website appears to be returning a bad gateway error instead of a working page.", ["bad gateway"]),
        ("default_wordpress", 'The public homepage still shows the default WordPress "Hello world!" residue.', ["hello world!"]),
        ("admin_exposure", "The public site is exposing a reachable WordPress login page.", ["public wordpress login endpoint", "wp-login.php"]),
        ("http_transport", "The contact or site flow appears to allow insecure HTTP transport.", ["form transport", "http transport", "insecure form transport"]),
    ]
    for issue_type, summary, needles in patterns:
        if any(needle in lowered for needle in needles):
            return {
                "issue_type": issue_type,
                "summary": summary,
                "source": "profile_or_preview",
                "verified_live": None,
            }
    return None


def best_issue_summary(
    context: dict[str, object],
    audit_findings: list[dict[str, object]],
    doc_previews: list[dict[str, object]],
    fts_results: object,
) -> dict[str, object]:
    if audit_findings:
        top = audit_findings[0]
        summary = str(top.get("issue_description") or "").strip()
        if not summary:
            issue_type = str(top.get("issue_type_norm") or "issue").strip()
            summary = f"Top audit finding: {issue_type}"
        return {
            "summary": summary,
            "issue_type": top.get("issue_type_norm"),
            "severity": top.get("severity"),
            "source": "leadops_v_audit_findings_actionable",
            "verified_live": bool(top.get("verified_live")) if top.get("verified_live") is not None else None,
            "evidence_path": top.get("evidence_path"),
            "next_action": top.get("next_action"),
        }

    candidate_texts: list[str] = []
    source_texts: list[str] = []
    for row in doc_previews:
        preview = str(row.get("preview") or "")
        if preview:
            candidate_texts.append(preview)
        source_path = str(row.get("source_path") or "")
        if source_path:
            full_text = file_text_from_source_path(source_path)
            if full_text:
                source_texts.append(full_text)
    if isinstance(fts_results, list):
        for row in fts_results:
            snippet = str(row.get("snippet") or "")
            if snippet:
                candidate_texts.append(snippet)

    for text in source_texts:
        inferred = infer_issue_from_text(text)
        if inferred:
            return inferred

    for text in candidate_texts:
        inferred = infer_issue_from_text(text)
        if inferred:
            return inferred

    lead = context.get("lead") or {}
    website = str(lead.get("website") or "").strip()
    if not website:
        return {
            "summary": "No strong verified issue was summarized yet; profile context likely needs a manual pass.",
            "issue_type": "needs_manual_summary",
            "severity": None,
            "source": "fallback",
            "verified_live": None,
            "evidence_path": None,
            "next_action": None,
        }

    return {
        "summary": "No strong verified issue was summarized yet; use the profile and nearest semantic analogs to inspect the lead.",
        "issue_type": "needs_manual_summary",
        "severity": None,
        "source": "fallback",
        "verified_live": None,
        "evidence_path": None,
        "next_action": None,
    }


def fts_seed_query(context: dict[str, object], explicit_query: str | None) -> str:
    if explicit_query:
        return explicit_query
    lead = context.get("lead") or {}
    name = str(lead.get("name") or "").strip()
    if not name:
        return ""
    tokens = [token for token in re.findall(r"[A-Za-z0-9]{3,}", name) if token.lower() not in {"llc", "inc", "ltd", "corp"}]
    if not tokens:
        return f'"{name}"'
    return " OR ".join(tokens[:5])


def semantic_seed_query(context: dict[str, object], explicit_query: str | None) -> str:
    if explicit_query:
        return explicit_query
    lead = context.get("lead") or {}
    name = str(lead.get("name") or "").strip()
    website = str(lead.get("website") or "").strip()
    email = str(lead.get("email") or "").strip()
    pieces = [piece for piece in [name, website, email] if piece]
    if not pieces:
        return "lead with website audit findings, outreach context, and similar business evidence"
    return "lead, audit evidence, outreach context, and similar businesses related to " + " | ".join(pieces)


def lead_bundle(args: argparse.Namespace) -> dict[str, object]:
    db_path = Path(args.db).resolve()
    context = lead_context(db_path, args.lead_id)
    if not context.get("lead"):
        raise SystemExit(f"Lead not found: {args.lead_id}")

    docs_cmd = [
        sys.executable,
        str(CORPUS_QUERY_SCRIPT),
        "--json",
        "docs",
        "--lead-id",
        str(args.lead_id),
        "--limit",
        str(args.docs_limit),
    ]
    artifacts_cmd = [
        sys.executable,
        str(CORPUS_QUERY_SCRIPT),
        "--json",
        "artifacts",
        "--lead-id",
        str(args.lead_id),
        "--limit",
        str(args.artifacts_limit),
    ]
    fts_query = fts_seed_query(context, args.query)
    fts_results: object = []
    if fts_query:
        fts_cmd = [
            sys.executable,
            str(CORPUS_QUERY_SCRIPT),
            "--json",
            "search",
            fts_query,
            "--lead-id",
            str(args.lead_id),
            "--limit",
            str(args.fts_limit),
        ]
        fts_results = run_json_command(fts_cmd)

    semantic_query = semantic_seed_query(context, args.query)
    semantic_fetch_limit = max(args.semantic_limit, args.past_winners_limit * 8, 40)
    semantic_cmd = [
        sys.executable,
        str(SEMANTIC_SCRIPT),
        "--json",
        "--profile",
        "fast",
        "--llama-port",
        str(args.semantic_port),
        "search",
        semantic_query,
        "--limit",
        str(semantic_fetch_limit),
    ]

    docs = run_json_command(docs_cmd)
    artifacts = run_json_command(artifacts_cmd)
    semantic_results = run_json_command(semantic_cmd)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        fts_results = enrich_fts_results(conn, fts_results)
        audit_findings = fetch_audit_findings_summary(conn, args.lead_id)
        doc_previews = fetch_doc_previews(conn, args.lead_id, args.docs_limit)
        semantic_results = enrich_semantic_results(conn, semantic_results)
        winners = past_winner_map(conn)
        similar_winners = similar_past_winners(semantic_results, winners, args.lead_id, args.past_winners_limit)
    semantic_results = semantic_results[: args.semantic_limit]

    return {
        "lead_id": args.lead_id,
        "bundle_kind": args.bundle_kind,
        "query": args.query,
        "fts_query": fts_query,
        "semantic_query": semantic_query,
        "context": context,
        "best_issue_summary": best_issue_summary(context, audit_findings, doc_previews, fts_results),
        "audit_findings": audit_findings,
        "docs": docs,
        "doc_previews": doc_previews,
        "artifacts": artifacts,
        "fts_local_results": fts_results,
        "similar_past_winners": similar_winners,
        "semantic_results": semantic_results,
    }


def workflow_search(args: argparse.Namespace) -> dict[str, object]:
    workflow = WORKFLOWS[args.workflow]
    semantic_query = workflow["query"].format(query=args.query)
    semantic_cmd = [
        sys.executable,
        str(SEMANTIC_SCRIPT),
        "--json",
        "--profile",
        "fast",
        "--llama-port",
        str(workflow.get("semantic_port", 8096)),
        "search",
        semantic_query,
        "--limit",
        str(args.limit),
    ]
    semantic_results = run_json_command(semantic_cmd)

    fts_results = []
    if workflow["fts"]:
        fts_cmd = [
            sys.executable,
            str(CORPUS_QUERY_SCRIPT),
            "--json",
            "search",
            workflow["fts"],
            "--limit",
            str(args.limit),
        ]
        fts_results = run_json_command(fts_cmd)

    return {
        "workflow": args.workflow,
        "description": workflow["description"],
        "query": args.query,
        "semantic_profile": "fast",
        "semantic_query": semantic_query,
        "semantic_results": semantic_results,
        "fts_query": workflow["fts"],
        "fts_results": fts_results,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent-friendly retrieval workflows over the leadops corpus.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to crm.sqlite")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    health = sub.add_parser("corpus-health", help="Show corpus and queue health")

    workflow = sub.add_parser("workflow", help="Run a named retrieval workflow")
    workflow.add_argument("workflow", choices=sorted(WORKFLOWS.keys()))
    workflow.add_argument("query", help="Human query or seed concept")
    workflow.add_argument("--limit", type=int, default=5)

    bundle = sub.add_parser("bundle", help="Build a RAG-ready retrieval bundle for one lead")
    bundle.add_argument("--lead-id", type=int, required=True)
    bundle.add_argument("--query", help="Optional custom retrieval query")
    bundle.add_argument(
        "--bundle-kind",
        choices=("draft", "audit", "research", "general"),
        default="general",
        help="Intent label for downstream agent use",
    )
    bundle.add_argument("--docs-limit", type=int, default=12)
    bundle.add_argument("--artifacts-limit", type=int, default=20)
    bundle.add_argument("--fts-limit", type=int, default=8)
    bundle.add_argument("--semantic-limit", type=int, default=8)
    bundle.add_argument("--past-winners-limit", type=int, default=5)
    bundle.add_argument("--semantic-port", type=int, default=8096)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    db_path = Path(args.db).resolve()
    if args.command == "corpus-health":
        emit(corpus_health(db_path), args.json)
        return
    if args.command == "workflow":
        emit(workflow_search(args), args.json)
        return
    if args.command == "bundle":
        emit(lead_bundle(args), args.json)
        return
    parser.print_help()
    raise SystemExit(1)


if __name__ == "__main__":
    main()

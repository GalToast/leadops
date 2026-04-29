from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "crm.sqlite"
DRAFTS_ROOT = REPO_ROOT / "outreach" / "drafts"
FAMILY_EXCLUDE_IDS = {1618}
FAMILY_EXCLUDE_PATTERNS = ("coffee cabin", "cj insulation", "cj builders")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rank next draft candidates from leadops, excluding already drafted and family-sensitive leads."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to crm.sqlite")
    parser.add_argument("--limit", type=int, default=25, help="Maximum recommended candidates to return")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    return parser


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def print_text(text: str) -> None:
    encoding = sys.stdout.encoding or "utf-8"
    safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    print(safe_text)


def emit(payload: object, as_json: bool) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if as_json:
        print_text(text)
        return
    print_text(text)


def parse_draft_lead_ids() -> set[int]:
    drafted_ids: set[int] = set()
    if not DRAFTS_ROOT.exists():
        return drafted_ids
    for path in DRAFTS_ROOT.rglob("*.txt"):
        name = path.name
        parts = name.split("-")
        if parts and parts[0].isdigit():
            drafted_ids.add(int(parts[0]))
            continue
        if name.startswith("lead-") and len(parts) > 1 and parts[1].isdigit():
            drafted_ids.add(int(parts[1]))
    return drafted_ids


def mailbox_draft_lead_ids(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT DISTINCT lead_id FROM leadops_drafts WHERE lead_id IS NOT NULL").fetchall()
    return {int(row[0]) for row in rows}


def load_safe_candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    query = """
        WITH audit AS (
            SELECT
                lead_id,
                MAX(
                    CASE severity
                        WHEN 'critical' THEN 4
                        WHEN 'high' THEN 3
                        WHEN 'medium' THEN 2
                        WHEN 'low' THEN 1
                        ELSE 0
                    END
                ) AS audit_severity_rank,
                GROUP_CONCAT(DISTINCT issue_type_norm) AS audit_issue_types,
                GROUP_CONCAT(DISTINCT issue_description) AS audit_issue_descriptions
            FROM leadops_v_audit_findings_actionable
            WHERE next_action = 'draft_from_verified_issue'
            GROUP BY lead_id
        )
        SELECT
            s.lead_id,
            s.name,
            s.email,
            s.website,
            s.audience_family,
            s.audience_type,
            s.primary_send_hook,
            s.next_action,
            s.send_priority,
            COALESCE(a.audit_severity_rank, 0) AS audit_severity_rank,
            COALESCE(a.audit_issue_types, '') AS audit_issue_types,
            COALESCE(a.audit_issue_descriptions, '') AS audit_issue_descriptions
        FROM leadops_v_send_now_mailbox_safe s
        LEFT JOIN audit a
          ON a.lead_id = s.lead_id
        ORDER BY
            COALESCE(a.audit_severity_rank, 0) DESC,
            CASE WHEN s.primary_send_hook = 'website_audit' THEN 1 ELSE 0 END DESC,
            s.lead_id ASC
    """
    return list(conn.execute(query))


def rank_candidates(rows: list[sqlite3.Row], drafted_ids: set[int], mailbox_ids: set[int], limit: int) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    for row in rows:
        lead_id = int(row["lead_id"])
        name = str(row["name"] or "")
        lowered_name = name.lower()
        if lead_id in FAMILY_EXCLUDE_IDS:
            continue
        if any(pattern in lowered_name for pattern in FAMILY_EXCLUDE_PATTERNS):
            continue
        if lead_id in drafted_ids:
            continue
        if lead_id in mailbox_ids:
            continue

        audit_rank = int(row["audit_severity_rank"] or 0)
        primary_send_hook = str(row["primary_send_hook"] or "")
        score = 0
        reason_parts: list[str] = []

        if audit_rank:
            score += 100 + audit_rank * 10
            reason_parts.append(f"verified_issue:{row['audit_issue_types'] or 'unknown'}")
        if primary_send_hook == "website_audit":
            score += 15
            reason_parts.append("website_audit")
        elif primary_send_hook:
            score += 5
            reason_parts.append(primary_send_hook)

        candidates.append(
            {
                "lead_id": lead_id,
                "name": name,
                "email": row["email"],
                "website": row["website"],
                "audience_family": row["audience_family"],
                "audience_type": row["audience_type"],
                "primary_send_hook": primary_send_hook,
                "audit_severity_rank": audit_rank,
                "audit_issue_types": row["audit_issue_types"],
                "audit_issue_descriptions": row["audit_issue_descriptions"],
                "score": score,
                "reason": "; ".join(reason_parts) if reason_parts else "send_safe",
            }
        )

    candidates.sort(key=lambda item: (-int(item["score"]), int(item["lead_id"])))
    return candidates[:limit]


def main() -> None:
    args = build_parser().parse_args()
    with connect(Path(args.db).resolve()) as conn:
        repo_drafted = parse_draft_lead_ids()
        mailbox_drafted = mailbox_draft_lead_ids(conn)
        safe_candidates = load_safe_candidates(conn)
        recommended = rank_candidates(safe_candidates, repo_drafted, mailbox_drafted, args.limit)

        payload = {
            "recommended_count": len(recommended),
            "repo_drafted_ids_count": len(repo_drafted),
            "mailbox_drafted_ids_count": len(mailbox_drafted),
            "family_excluded_ids": sorted(FAMILY_EXCLUDE_IDS),
            "recommendations": recommended,
        }
        emit(payload, args.json)


if __name__ == "__main__":
    main()

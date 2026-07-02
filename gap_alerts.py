"""
gap_alerts.py — Persistent Knowledge Gap Faculty Alert System

Implements:
  Patent Claim 5  (system):  Knowledge-gap alert module —
      aggregates per-topic query frequencies across students,
      identifies topics exceeding a faculty-configurable threshold,
      and generates/stores persistent faculty alert records.

  Patent Claim 19 (method):  Longitudinally-informed Socratic execution —
      exposes get_student_gap_profile() so the Socratic agent can retrieve
      a structured gap list and seed its first question toward the student's
      most significant identified knowledge gap.

Storage:
  local  → gap_alerts.json  (development / single-server)
  cloud  → Supabase gap_alerts table (multi-tenant production)
"""

import os
import json
from datetime import datetime, timedelta
from collections import defaultdict

# ─── File path for local mode ─────────────────────────────────────────────────
GAP_ALERTS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "gap_alerts.json"
)

# ─── Default threshold: faculty-configurable at runtime ───────────────────────
DEFAULT_THRESHOLD = 5  # unique students asking about same topic → alert fires


# ═══════════════════════════════════════════════════════════════════════════════
#  Storage helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _load_alerts_local() -> list:
    try:
        if not os.path.exists(GAP_ALERTS_FILE):
            return []
        with open(GAP_ALERTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_alerts_local(alerts: list) -> None:
    try:
        with open(GAP_ALERTS_FILE, "w", encoding="utf-8") as f:
            json.dump(alerts, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _get_supabase():
    from supabase import create_client
    return create_client(
        os.getenv("SUPABASE_URL", ""),
        os.getenv("SUPABASE_KEY", ""),
    )


def load_gap_alerts(mode: str = "local") -> list:
    """Return all stored faculty alert records, newest first."""
    if mode == "cloud":
        try:
            sb = _get_supabase()
            result = (
                sb.table("gap_alerts")
                  .select("*")
                  .order("created_at", desc=True)
                  .execute()
            )
            return result.data or []
        except Exception:
            return []
    return sorted(
        _load_alerts_local(),
        key=lambda a: a.get("created_at", ""),
        reverse=True,
    )


def _upsert_alert(topic: str, student_emails: list, mode: str = "local") -> None:
    """Create or refresh the alert record for a topic."""
    alert = {
        "topic":          topic,
        "student_count":  len(student_emails),
        "student_emails": student_emails[:50],   # cap — never store unbounded PII
        "created_at":     datetime.now().isoformat(),
        "resolved":       False,
    }
    if mode == "cloud":
        try:
            sb = _get_supabase()
            # Replace any existing record for this topic
            sb.table("gap_alerts").delete().eq("topic", topic).execute()
            sb.table("gap_alerts").insert(alert).execute()
        except Exception:
            pass
        return
    # Local
    existing = [a for a in _load_alerts_local() if a.get("topic") != topic]
    existing.append(alert)
    _save_alerts_local(existing)


def resolve_alert(topic: str, mode: str = "local") -> None:
    """Mark a faculty alert as resolved (acknowledged)."""
    if mode == "cloud":
        try:
            sb = _get_supabase()
            sb.table("gap_alerts").update({"resolved": True}).eq("topic", topic).execute()
        except Exception:
            pass
        return
    alerts = _load_alerts_local()
    for a in alerts:
        if a.get("topic") == topic:
            a["resolved"] = True
    _save_alerts_local(alerts)


# ═══════════════════════════════════════════════════════════════════════════════
#  Core computation — Claim 5
# ═══════════════════════════════════════════════════════════════════════════════

def compute_gap_alerts(
    query_logs: list,
    threshold: int = DEFAULT_THRESHOLD,
    days: int = 30,
    mode: str = "local",
) -> list:
    """
    Aggregate per-topic query frequencies across all students (Claim 5, step a).
    Identify topics where unique-student count >= threshold (step b).
    Upsert a faculty alert record for each (step c).

    Args:
        query_logs:  Full log list as returned by load_query_logs().
        threshold:   Faculty-configurable minimum unique-student count.
        days:        Look-back window in days (0 = all time).
        mode:        "local" | "cloud"

    Returns:
        List of topic strings for which alerts were (re)fired.
    """
    if not query_logs:
        return []

    cutoff = ""
    if days > 0:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    # topic → set of unique student emails
    topic_students: dict = defaultdict(set)
    for entry in query_logs:
        ts    = entry.get("timestamp", "")
        email = entry.get("user_email", "unknown")
        if cutoff and ts < cutoff:
            continue
        for topic in entry.get("topics", []):
            topic_students[topic].add(email)

    fired = []
    for topic, students in topic_students.items():
        if len(students) >= threshold:
            _upsert_alert(topic, sorted(students), mode=mode)
            fired.append(topic)

    return fired


# ═══════════════════════════════════════════════════════════════════════════════
#  Per-student gap profile — supports Claim 4 / Claim 19 (Socratic targeting)
# ═══════════════════════════════════════════════════════════════════════════════

def get_student_gap_profile(
    user_email: str,
    query_logs: list,
    min_repeats: int = 3,
    days: int = 90,
) -> list:
    """
    Return an ordered list of topic strings that this student has asked about
    repeatedly — indicating persistent knowledge gaps (Claim 4 / 19).

    A topic qualifies as a "gap" when the same student has asked about it
    at least `min_repeats` times across separate log entries.

    Returns: list of topic strings, highest-frequency first.
    """
    if not query_logs or not user_email:
        return []

    cutoff = ""
    if days > 0:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    topic_counts: dict = defaultdict(int)
    for entry in query_logs:
        if entry.get("user_email", "") != user_email:
            continue
        ts = entry.get("timestamp", "")
        if cutoff and ts < cutoff:
            continue
        for topic in entry.get("topics", []):
            topic_counts[topic] += 1

    gaps = [
        topic for topic, count in
        sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)
        if count >= min_repeats
    ]
    return gaps

"""
DentAI — Master Dashboard (operator-only)
─────────────────────────────────────────
Cross-school metrics for DentAI LLC. One row per school instance, pulling live
from each school's own Supabase project.

NOT for faculty — this is the company control panel. Gate: MASTER_PASSWORD.

Run locally:
  streamlit run master_dashboard.py --server.port 8503

Schools are read from master_schools.json (gitignored — contains service keys).
If the file is missing, falls back to the current .env (single-school NSU mode).
"""

import os
import json
import hashlib
from datetime import datetime, timedelta
from collections import Counter

import streamlit as st
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

st.set_page_config(page_title="DentAI — Master Dashboard", layout="wide",
                   initial_sidebar_state="collapsed")

# ── Styles (matches DentAI dark brand) ────────────────────────────────────────
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Space+Grotesk:wght@500;600;700&display=swap');
  :root { --bg:#050a12; --surface:#0c1524; --surface2:#111e30;
          --border:rgba(0,200,255,0.12); --cyan:#00c8ff; --text:#e8f0fe; --muted:#7a90b0; }
  [data-testid="stAppViewContainer"], .main { background: var(--bg) !important; }
  #MainMenu, footer, [data-testid="stHeader"] { visibility: hidden; }
  h1,h2,h3 { font-family:'Space Grotesk',sans-serif !important; color: var(--text) !important; }
  p, span, label, div, td, th { color: var(--text); font-family:'Inter',sans-serif; }
  [data-testid="stMetric"] { background: var(--surface); border:1px solid var(--border);
                             border-radius:12px; padding:14px 18px; }
  [data-testid="stMetricLabel"] { color: var(--muted) !important; }
  [data-testid="stMetricValue"] { color: var(--cyan) !important; }
  [data-testid="stTextInput"] input { background: var(--surface) !important;
      border:1px solid var(--border) !important; color: var(--text) !important; border-radius:10px !important; }
  .stButton > button[kind="primary"] { background: linear-gradient(135deg,#00c8ff,#0090cc) !important;
      color:#000 !important; font-weight:700 !important; border:none !important; border-radius:10px !important; }
  [data-testid="stExpander"] { background: var(--surface); border:1px solid var(--border) !important;
      border-radius:12px; }
</style>
""", unsafe_allow_html=True)

# ── Auth gate ─────────────────────────────────────────────────────────────────
MASTER_PASSWORD = os.getenv("MASTER_PASSWORD", "") or os.getenv("ADMIN_PASSWORD", "")

def _token() -> str:
    return hashlib.sha256(f"dentai_master:{MASTER_PASSWORD}".encode()).hexdigest()[:24]

if MASTER_PASSWORD and not st.session_state.get("master_auth"):
    if st.query_params.get("t", "") == _token():
        st.session_state["master_auth"] = True

if MASTER_PASSWORD and not st.session_state.get("master_auth"):
    st.markdown("## 🔐 DentAI Master Dashboard")
    st.caption("Operator access only.")
    pw = st.text_input("Password", type="password", label_visibility="collapsed",
                       placeholder="Master password")
    if st.button("Sign in", type="primary"):
        if pw == MASTER_PASSWORD:
            st.session_state["master_auth"] = True
            st.query_params["t"] = _token()
            st.rerun()
        st.error("Incorrect password.")
    st.stop()
elif not MASTER_PASSWORD:
    st.warning("MASTER_PASSWORD (or ADMIN_PASSWORD) not set — dashboard is UNPROTECTED. "
               "Set it in .env before deploying anywhere public.")

# ── School registry ───────────────────────────────────────────────────────────
SCHOOLS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "master_schools.json")

def load_schools() -> list:
    if os.path.exists(SCHOOLS_FILE):
        with open(SCHOOLS_FILE) as f:
            return json.load(f)["schools"]
    # Fallback: single school from the current .env (NSU)
    return [{
        "id": "nsu",
        "name": "NSU College of Dental Medicine",
        "supabase_url": os.getenv("SUPABASE_URL", ""),
        "supabase_key": os.getenv("SUPABASE_KEY", ""),
        "app_url": "https://app.dentaiassist.com",
        "admin_url": "https://admin.dentaiassist.com",
    }]

# ── Metrics fetch (cached 5 min so reloads are cheap) ─────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def school_metrics(supabase_url: str, supabase_key: str) -> dict:
    out = {"ok": False, "error": ""}
    try:
        sb = create_client(supabase_url, supabase_key)
        now = datetime.now()

        # Profile counts come from the master_stats() SQL function (security
        # definer) so the anon key works despite RLS. Falls back to raw counts.
        try:
            stats = sb.rpc("master_stats").execute().data or {}
            n_students, n_users = stats.get("students", 0), stats.get("users", 0)
        except Exception:
            n_students = (sb.table("profiles").select("id", count="exact")
                            .eq("role", "student").execute()).count or 0
            n_users = (sb.table("profiles").select("id", count="exact").execute()).count or 0
        sessions = sb.table("chat_sessions").select("id", count="exact").execute()
        docs = sb.table("documents").select("id", count="exact").execute()

        logs = (sb.table("query_logs")
                  .select("user_email,topics,timestamp")
                  .order("timestamp", desc=True).limit(5000).execute()).data or []

        def since(days):
            cut = (now - timedelta(days=days)).isoformat()
            return [l for l in logs if (l.get("timestamp") or "") >= cut]

        last7, last30 = since(7), since(30)
        topics7 = Counter(t for l in last7 for t in (l.get("topics") or []))

        # queries per day, last 14 days
        per_day = Counter((l.get("timestamp") or "")[:10] for l in since(14))
        days14 = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(13, -1, -1)]

        out.update(
            ok=True,
            students=n_students,
            users=n_users,
            sessions=sessions.count or 0,
            docs=docs.count or 0,
            q_total=len(logs),
            q_7d=len(last7),
            q_30d=len(last30),
            active_7d=len({l.get("user_email") for l in last7}),
            avg_day=round(len(last7) / 7, 1),
            last_activity=(logs[0].get("timestamp", "")[:16].replace("T", " ") if logs else "—"),
            top_topics=topics7.most_common(5),
            per_day={d: per_day.get(d, 0) for d in days14},
            recent=[{"when": l.get("timestamp", "")[:16].replace("T", " "),
                     "who": (l.get("user_email") or "").split("@")[0]} for l in logs[:8]],
        )
    except Exception as e:
        out["error"] = str(e)
    return out

# ── Page ──────────────────────────────────────────────────────────────────────
head_l, head_r = st.columns([4, 1])
with head_l:
    st.markdown("# DentAI — Master Dashboard")
    st.caption(f"All school instances · refreshed {datetime.now().strftime('%Y-%m-%d %H:%M')} · cache 5 min")
with head_r:
    if st.button("↻ Refresh now", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

schools = load_schools()
data = {s["id"]: school_metrics(s["supabase_url"], s["supabase_key"]) for s in schools}
ok = [s for s in schools if data[s["id"]]["ok"]]

# ── Company-wide totals ───────────────────────────────────────────────────────
st.markdown("## Platform Totals")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Schools", len(schools))
c2.metric("Students", sum(data[s["id"]]["students"] for s in ok))
c3.metric("Queries (7d)", sum(data[s["id"]]["q_7d"] for s in ok))
c4.metric("Active students (7d)", sum(data[s["id"]]["active_7d"] for s in ok))
c5.metric("Documents indexed", sum(data[s["id"]]["docs"] for s in ok))

st.markdown("---")

# ── Per-school sections ───────────────────────────────────────────────────────
for s in schools:
    m = data[s["id"]]
    if not m["ok"]:
        st.error(f"**{s['name']}** — couldn't reach its Supabase: {m['error']}")
        continue

    st.markdown(f"## {s['name']}")
    st.caption(f"[student app]({s.get('app_url','')}) · [faculty admin]({s.get('admin_url','')}) "
               f"· last activity: {m['last_activity']}")

    a, b, c, d, e, f = st.columns(6)
    a.metric("Students", m["students"])
    b.metric("Queries 7d", m["q_7d"], delta=f"{m['avg_day']}/day")
    c.metric("Queries 30d", m["q_30d"])
    d.metric("Active 7d", m["active_7d"])
    e.metric("Chat sessions", m["sessions"])
    f.metric("Docs", m["docs"])

    g1, g2 = st.columns([2, 1])
    with g1:
        st.markdown("**Queries per day — last 14 days**")
        st.bar_chart(m["per_day"], height=200)
    with g2:
        st.markdown("**Top topics (7d)**")
        if m["top_topics"]:
            for topic, n in m["top_topics"]:
                st.markdown(f"- {topic} — **{n}**")
        else:
            st.caption("No queries in the last 7 days.")

    with st.expander("Recent activity"):
        for r in m["recent"]:
            st.markdown(f"`{r['when']}` — {r['who']}")
        if not m["recent"]:
            st.caption("No logged queries yet.")

    st.markdown("---")

st.caption("Add schools in master_schools.json (see master_schools.json.example). "
           "Costs, uptime, and Pinecone usage are candidates for v2.")

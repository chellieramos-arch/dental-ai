"""
DentAI — Faculty & Admin Dashboard
────────────────────────────────────
Standalone Streamlit app for NSU dental faculty to manage the
DentAI knowledge base.

Run locally:
  streamlit run admin_app.py --server.port 8502
"""

import os
import io
import json
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse
from collections import Counter

from gap_alerts import (
    compute_gap_alerts,
    load_gap_alerts,
    resolve_alert,
    DEFAULT_THRESHOLD,
)

import streamlit as st
from dotenv import load_dotenv

# ── Secrets → env ─────────────────────────────────────────────────────────────
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, str):
            os.environ.setdefault(_k, _v)
except Exception:
    pass

load_dotenv()
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")

# Per-school configuration (env-driven; NSU defaults)
import school as SCHOOL

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="DentAI Admin",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Global styles ─────────────────────────────────────────────────────────────
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Space+Grotesk:wght@400;500;600;700&display=swap');

  :root {
    --bg: #ffffff;
    --surface: #f4f7fb;
    --surface2: #e8eef7;
    --border: rgba(0,144,204,0.18);
    --cyan: #0090cc;
    --cyan-dark: #006fa0;
    --purple: #7b5ea7;
    --text: #0f1f35;
    --muted: #5a7090;
  }

  /* ── Base ── */
  [data-testid="stAppViewContainer"], .main {
    background: var(--bg) !important;
    font-family: 'Inter', sans-serif;
  }
  [data-testid="stHeader"] { background: transparent; display: none; }
  #MainMenu, footer { visibility: hidden; }
  * { color: var(--text); }

  /* ── Sidebar ── */
  [data-testid="stSidebar"] {
    background: #f0f4f8 !important;
    border-right: 1px solid var(--border) !important;
  }
  [data-testid="stSidebar"] .sidebar-logo {
    padding: 28px 20px 20px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 8px;
  }
  [data-testid="stSidebar"] hr {
    border-color: var(--border) !important;
  }

  /* ── Nav buttons in sidebar ── */
  [data-testid="stSidebar"] .stButton > button {
    background: transparent !important;
    border: none !important;
    color: var(--muted) !important;
    text-align: left !important;
    width: 100% !important;
    padding: 10px 16px !important;
    border-radius: 8px !important;
    font-size: 0.88rem !important;
    font-weight: 500 !important;
    transition: all 0.15s !important;
    margin-bottom: 2px !important;
  }
  [data-testid="stSidebar"] .stButton > button:hover {
    background: rgba(0,144,204,0.08) !important;
    color: var(--cyan) !important;
  }
  [data-testid="stSidebar"] .nav-active > button {
    background: rgba(0,144,204,0.10) !important;
    color: var(--cyan) !important;
    border-left: 2px solid var(--cyan) !important;
  }


  /* ── Selectbox dropdown: white bg, black text ── */
  div[data-baseweb="popover"] *,
  div[data-baseweb="menu"] *,
  ul[role="listbox"],
  ul[role="listbox"] * {
    color: #111 !important;
  }

  /* ── Cards ── */
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 28px 32px;
    margin-bottom: 20px;
  }
  .card h4 {
    margin: 0 0 4px 0;
    font-size: 1rem;
    font-weight: 700;
    color: var(--text);
    font-family: 'Space Grotesk', sans-serif;
  }
  .card-caption {
    font-size: 0.82rem;
    color: var(--muted);
    margin-bottom: 16px;
  }

  /* ── Stat cards ── */
  .stat-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 22px 24px;
    display: flex;
    align-items: center;
    gap: 16px;
  }
  .stat-icon {
    width: 48px; height: 48px;
    display: flex; align-items: center; justify-content: center;
    border-radius: 10px;
    flex-shrink: 0;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.03em;
    text-transform: uppercase;
  }
  .stat-icon.blue   { background: rgba(0,144,204,0.12); color: var(--cyan); }
  .stat-icon.green  { background: rgba(0,160,90,0.12);  color: #1a8a5e; }
  .stat-icon.purple { background: rgba(123,94,167,0.12); color: #7b5ea7; }
  .stat-num { font-size: 1.8rem; font-weight: 700; color: var(--text); line-height: 1; font-family: 'Space Grotesk', sans-serif; }
  .stat-lbl { font-size: 0.78rem; color: var(--muted); margin-top: 2px; }

  /* ── Source rows ── */
  .src-item {
    display: flex; align-items: center; gap: 12px;
    padding: 11px 0;
    border-bottom: 1px solid var(--border);
  }
  .src-item:last-child { border-bottom: none; }
  .file-type-badge {
    width: 38px; height: 34px;
    border-radius: 6px;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.62rem; font-weight: 700;
    letter-spacing: 0.04em; flex-shrink: 0;
    text-transform: uppercase;
  }
  .file-type-badge.pdf  { background: rgba(220,38,38,0.10);  color: #dc2626; }
  .file-type-badge.doc  { background: rgba(0,144,204,0.12);  color: var(--cyan); }
  .file-type-badge.ppt  { background: rgba(234,88,12,0.12);  color: #ea580c; }
  .file-type-badge.web  { background: rgba(22,163,74,0.12);  color: #16a34a; }
  .file-type-badge.txt  { background: rgba(0,0,0,0.06);      color: var(--muted); }
  .src-name { font-size: 0.88rem; color: var(--text); font-weight: 500; flex: 1; }
  .src-date { font-size: 0.75rem; color: var(--muted); }

  /* ── Page title ── */
  .page-title {
    font-size: 1.5rem; font-weight: 700; color: var(--text);
    margin-bottom: 2px; font-family: 'Space Grotesk', sans-serif;
  }
  .page-sub {
    font-size: 0.85rem; color: var(--muted);
    margin-bottom: 24px;
  }

  /* ── Inputs ── */
  [data-testid="stFileUploader"] {
    border: 1.5px dashed rgba(0,144,204,0.3) !important;
    border-radius: 10px !important;
    background: rgba(0,144,204,0.03) !important;
    padding: 8px !important;
  }
  [data-testid="stTextInput"] input,
  [data-testid="stTextArea"] textarea {
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: 8px !important;
    color: var(--text) !important;
  }
  [data-testid="stTextInput"] input:focus,
  [data-testid="stTextArea"] textarea:focus {
    border-color: var(--cyan) !important;
    box-shadow: 0 0 0 3px rgba(0,144,204,0.10) !important;
  }

  /* ── Primary button ── */
  .stButton > button[kind="primary"] {
    background: linear-gradient(135deg, var(--cyan), var(--cyan-dark)) !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 800 !important;
    font-size: 1rem !important;
    padding: 10px 20px !important;
    opacity: 1 !important;
  }
  .stButton > button[kind="primary"] p {
    color: #ffffff !important;
    font-weight: 800 !important;
    opacity: 1 !important;
  }
  .stButton > button[kind="primary"]:hover {
    box-shadow: 0 4px 20px rgba(0,144,204,0.3) !important;
    transform: translateY(-1px) !important;
  }
  .stButton > button[kind="secondary"] {
    background: transparent !important;
    border: 1px solid var(--border) !important;
    color: var(--text) !important;
    border-radius: 8px !important;
  }
  .stButton > button[kind="secondary"]:hover {
    border-color: var(--cyan) !important;
    color: var(--cyan) !important;
  }

  /* ── Input placeholder ── */
  [data-testid="stTextInput"] input::placeholder { color: var(--muted) !important; opacity: 1 !important; }
  [data-testid="stTextInput"] input { color: var(--text) !important; font-size: 0.95rem !important; }

  /* ── Login ── */
  .login-container {
    max-width: 400px;
    margin: 60px auto;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 44px 40px;
    text-align: center;
    box-shadow: 0 4px 24px rgba(0,144,204,0.08);
  }

  /* ── Badge ── */
  .badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 999px;
    font-size: 0.72rem;
    font-weight: 600;
    background: rgba(22,163,74,0.10);
    color: #16a34a;
    border: 1px solid rgba(22,163,74,0.2);
  }

  /* ── Scrollable list ── */
  .scroll-list {
    max-height: 420px;
    overflow-y: auto;
    padding-right: 4px;
  }
  .scroll-list::-webkit-scrollbar { width: 4px; }
  .scroll-list::-webkit-scrollbar-track { background: transparent; }
  .scroll-list::-webkit-scrollbar-thumb { background: rgba(0,144,204,0.25); border-radius: 4px; }

  /* ── Streamlit overrides ── */
  .stAlert { border-radius: 10px !important; }
  [data-testid="stMarkdownContainer"] p { color: var(--muted); }
  .stProgress > div > div { background: var(--cyan) !important; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
EMBED_MODEL   = "text-embedding-ada-002"
CHUNK_SIZE    = 1500
CHUNK_OVERLAP = 200
BATCH_SIZE    = 96

# ── Supabase client ───────────────────────────────────────────────────────────
@st.cache_resource
def _get_supabase():
    from supabase import create_client
    return create_client(
        os.getenv("SUPABASE_URL", ""),
        os.getenv("SUPABASE_KEY", ""),
    )

# ── Restore session from Supabase access token in URL ────────────────────────
def _try_restore_session():
    token = st.query_params.get("s")
    if token and not st.session_state.get("admin_auth"):
        try:
            sb   = _get_supabase()
            data = sb.auth.get_user(token)
            if data and data.user:
                email = data.user.email
                prof  = sb.table("profiles").select("role,full_name").eq("email", email).execute()
                row   = prof.data[0] if prof.data else {}
                if row.get("role") == "faculty":
                    st.session_state["admin_auth"]    = True
                    st.session_state["faculty_email"] = email
                    st.session_state["faculty_name"]  = row.get("full_name", email)
        except Exception:
            pass

_try_restore_session()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def file_type_badge(name: str) -> str:
    """Return an HTML badge reflecting the file type."""
    ext = os.path.splitext(name)[1].lower()
    if ext == ".pdf":
        return '<div class="file-type-badge pdf">PDF</div>'
    elif ext == ".docx":
        return '<div class="file-type-badge doc">DOC</div>'
    elif ext == ".pptx":
        return '<div class="file-type-badge ppt">PPT</div>'
    elif ext == ".txt":
        return '<div class="file-type-badge txt">TXT</div>'
    else:
        return '<div class="file-type-badge web">WEB</div>'


def chunk_text(text):
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start : start + CHUNK_SIZE])
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return [c.strip() for c in chunks if c.strip()]

def extract_pdf(b):
    """Returns list of (page_num, page_text) — page boundaries are preserved so
    each indexed chunk can record which page it came from. This is what lets
    the student app later pull the correct page's image for a retrieved chunk;
    flattening the whole doc into one string (the old behavior) threw that away."""
    import fitz
    doc = fitz.open(stream=b, filetype="pdf")
    pages = [(i + 1, p.get_text()) for i, p in enumerate(doc)]
    doc.close()
    return pages

def extract_docx(b):
    from docx import Document
    return "\n".join(p.text for p in Document(io.BytesIO(b)).paragraphs if p.text.strip())

def extract_pptx(b):
    """Returns list of (slide_num, slide_text) — same page-tracking rationale as extract_pdf."""
    from pptx import Presentation
    slides = []
    for i, slide in enumerate(Presentation(io.BytesIO(b)).slides):
        lines = [shape.text.strip() for shape in slide.shapes
                 if hasattr(shape, "text") and shape.text.strip()]
        slides.append((i + 1, "\n".join(lines)))
    return slides

def extract_url(url):
    import requests
    from bs4 import BeautifulSoup
    r = requests.get(url, timeout=15, headers={"User-Agent": "DentAI-Admin/1.0"})
    r.raise_for_status()
    soup  = BeautifulSoup(r.text, "html.parser")
    title = soup.title.string.strip() if soup.title else urlparse(url).netloc
    for t in soup(["script","style","nav","footer","header"]): t.decompose()
    lines = [l.strip() for l in soup.get_text("\n").splitlines() if l.strip()]
    return title, "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Pinecone
# ─────────────────────────────────────────────────────────────────────────────

def store_file_in_supabase(file_name: str, raw_bytes: bytes) -> bool:
    """
    Upload the original file to Supabase Storage bucket 'course-files'.
    This lets the student-facing app download source files on-demand for
    image extraction — the documents/ folder is excluded from Docker builds.
    Returns True on success, False on any failure (non-fatal).
    """
    try:
        from supabase import create_client
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_SERVICE_KEY", "") or os.getenv("SUPABASE_KEY", "")
        if not url or not key:
            return False
        sb = create_client(url, key)
        # Detect MIME type for the upload
        ext = os.path.splitext(file_name)[1].lower()
        mime_map = {
            ".pdf": "application/pdf",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ".ppt": "application/vnd.ms-powerpoint",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
        content_type = mime_map.get(ext, "application/octet-stream")
        # Remove existing file first (upsert not always available)
        try:
            sb.storage.from_("course-files").remove([file_name])
        except Exception:
            pass
        sb.storage.from_("course-files").upload(
            file_name,
            raw_bytes,
            {"content-type": content_type},
        )
        return True
    except Exception:
        return False


def log_upload_to_supabase(file_name: str, source_type: str, vector_count: int):
    """Log a successful upload to the Supabase documents table."""
    try:
        from supabase import create_client
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_KEY", "")
        if not url or not key:
            st.warning("⚠️ Supabase credentials not found — upload not logged.")
            return
        sb = create_client(url, key)
        resp = sb.table("documents").insert({
            "file_name":    file_name,
            "source_type":  source_type,
            "vector_count": vector_count,
        }).execute()
        # supabase-py v1 returns errors in the response instead of raising
        if hasattr(resp, "error") and resp.error:
            st.warning(f"⚠️ Supabase insert error: {resp.error}")
        elif not resp.data:
            st.warning(f"⚠️ Supabase insert returned no data — check RLS policies.")
    except Exception as e:
        st.warning(f"⚠️ Indexed to Pinecone but could not log to Supabase: {e}")

@st.cache_resource
def get_index():
    from pinecone import Pinecone
    pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY",""))
    return pc.Index(os.getenv("PINECONE_INDEX","dentai"))

def embed_and_upsert(source_name, content):
    """
    content: either a flat string (docx/txt/url — no page concept), or a list
    of (page_num, page_text) tuples (pdf/pptx — from extract_pdf/extract_pptx).
    Paged content gets a "page_label" in each chunk's metadata so the student
    app can later find the right page for image extraction; flat content is
    indexed the same as before (no page concept applies to those formats).
    """
    import openai
    idx = get_index()
    oai = openai.OpenAI()

    tagged_chunks = []   # (page_num_or_None, chunk_text)
    if isinstance(content, str):
        for c in chunk_text(content):
            tagged_chunks.append((None, c))
    else:
        for page_num, page_text in content:
            if not page_text or not page_text.strip():
                continue
            for c in chunk_text(page_text):
                tagged_chunks.append((page_num, c))

    if not tagged_chunks:
        return 0

    # Re-uploading/replacing this file? Clear its old vectors first so stale
    # chunks (e.g. from before page tracking existed, or just fewer/more chunks
    # than last time) don't linger alongside the new ones.
    try:
        idx.delete(filter={"file_name": {"$eq": source_name}})
    except Exception:
        pass   # delete-by-metadata isn't supported on every Pinecone plan/index type — non-fatal

    total = 0
    for i in range(0, len(tagged_chunks), BATCH_SIZE):
        batch = tagged_chunks[i:i + BATCH_SIZE]
        texts = [c for _, c in batch]
        resp  = oai.embeddings.create(input=texts, model=EMBED_MODEL)
        vectors = []
        for j, item in enumerate(resp.data):
            page_num, chunk = batch[j]
            meta = {
                "text": chunk, "file_name": source_name,
                "chunk_index": i + j,
                "uploaded_at": datetime.utcnow().isoformat(),
            }
            if page_num is not None:
                meta["page_label"] = str(page_num)
            vectors.append({
                "id": f"{source_name}::{i+j}",
                "values": item.embedding,
                "metadata": meta,
            })
        idx.upsert(vectors=vectors)
        total += len(batch)
    return total

def fetch_stats():
    try:
        s = get_index().describe_index_stats()
        return s.get("total_vector_count", 0)
    except: return 0

def list_sources():
    try:
        res = get_index().query(vector=[0.0]*1536, top_k=200, include_metadata=True)
        seen, out = set(), []
        for m in res.get("matches",[]):
            meta = m.get("metadata",{}); name = meta.get("file_name","unknown")
            if name not in seen:
                seen.add(name)
                out.append({"name": name, "uploaded_at": meta.get("uploaded_at","")})
        return sorted(out, key=lambda x: x["uploaded_at"], reverse=True)
    except: return []

def delete_source(name):
    try:
        idx = get_index()
        res = idx.query(vector=[0.0]*1536, top_k=1000, include_metadata=True,
                        filter={"file_name":{"$eq": name}})
        ids = [m["id"] for m in res.get("matches",[])]
        if ids: idx.delete(ids=ids)
        return len(ids)
    except: return 0


# ─────────────────────────────────────────────────────────────────────────────
# Login
# ─────────────────────────────────────────────────────────────────────────────

def show_login():
    _, col, _ = st.columns([1, 1.2, 1])
    with col:
        st.markdown(f"""
        <div class="login-container">
          <div style="width:56px;height:56px;background:linear-gradient(135deg,#0090cc,#7b5ea7);
                      border-radius:14px;margin:0 auto 18px;display:flex;align-items:center;
                      justify-content:center;font-family:'Space Grotesk',sans-serif;
                      font-size:22px;font-weight:900;color:#fff;letter-spacing:-1px;
                      box-shadow:0 8px 28px rgba(0,144,204,0.2);">D+</div>
          <div style="font-size:1.4rem;font-weight:700;color:#0f1f35;font-family:'Space Grotesk',sans-serif;">DentAI Admin</div>
          <div style="font-size:0.85rem;color:#0090cc;margin-top:6px;margin-bottom:28px;font-weight:500;letter-spacing:0.04em;">
            {SCHOOL.SCHOOL_SHORT} — Faculty &amp; Admin Portal
          </div>
        </div>
        """, unsafe_allow_html=True)

        sb = _get_supabase()
        tab_login, tab_signup = st.tabs(["Sign In", "Create Faculty Account"])

        # ── Sign In ───────────────────────────────────────────────────────────
        with tab_login:
            f_email = st.text_input(
                "Faculty Email", placeholder=SCHOOL.EMAIL_PLACEHOLDER,
                key="fac_login_email",
            )
            f_pw = st.text_input(
                "Password", type="password", placeholder="Your password",
                key="fac_login_pw",
            )
            if st.button("Sign In", use_container_width=True, type="primary", key="btn_fac_login"):
                _email = f_email.strip().lower()
                _pw    = f_pw.strip()
                if not _email or not _pw:
                    st.error("Please enter your email and password.")
                elif not SCHOOL.is_school_email(_email):
                    st.error(f"Please use your {SCHOOL.SCHOOL_SHORT} email ({SCHOOL.DOMAINS_READABLE}).")
                else:
                    try:
                        resp = sb.auth.sign_in_with_password({"email": _email, "password": _pw})
                        # Verify faculty role
                        prof = sb.table("profiles").select("role,full_name").eq("email", _email).execute()
                        row  = prof.data[0] if prof.data else {}
                        if row.get("role") != "faculty":
                            sb.auth.sign_out()
                            st.error("This portal is for faculty only. Please use the student portal at app.dentaiassist.com.")
                        else:
                            st.session_state["admin_auth"]    = True
                            st.session_state["faculty_email"] = _email
                            st.session_state["faculty_name"]  = row.get("full_name", _email)
                            st.session_state.pop("sources_cache", None)
                            if resp.session and resp.session.access_token:
                                st.query_params["s"] = resp.session.access_token
                            st.rerun()
                    except Exception as e:
                        st.error(f"Sign in failed. Check your email and password. ({e})")

        # ── Create Faculty Account ────────────────────────────────────────────
        with tab_signup:
            su_email = st.text_input(
                "Faculty Email", placeholder=SCHOOL.EMAIL_PLACEHOLDER,
                key="fac_su_email",
            )
            su_pw = st.text_input(
                "Password", type="password", placeholder="At least 8 characters",
                key="fac_su_pw",
            )
            su_pw2 = st.text_input(
                "Confirm Password", type="password", placeholder="Repeat password",
                key="fac_su_pw2",
            )
            su_name = st.text_input(
                "Full Name", placeholder="Dr. First Last",
                key="fac_su_name",
            )
            col_title, col_dept = st.columns(2)
            with col_title:
                su_title = st.selectbox(
                    "Title", key="fac_su_title",
                    options=["Clinical Professor", "Associate Professor", "Assistant Professor",
                             "Clinical Instructor", "Adjunct Faculty", "Program Director",
                             "Department Chair", "Other"],
                )
            with col_dept:
                su_dept = st.text_input(
                    "Department", placeholder="e.g. Oral Surgery",
                    key="fac_su_dept",
                )

            if st.button("Create Faculty Account", use_container_width=True,
                         type="primary", key="btn_fac_signup"):
                _email = su_email.strip().lower()
                _pw    = su_pw.strip()
                _name  = su_name.strip()

                if not all([_email, _pw, su_pw2.strip(), _name, su_dept.strip()]):
                    st.error("Please fill in all fields.")
                elif not SCHOOL.is_school_email(_email):
                    st.error(f"Please use your {SCHOOL.SCHOOL_SHORT} email ({SCHOOL.DOMAINS_READABLE}).")
                elif len(_pw) < 8:
                    st.error("Password must be at least 8 characters.")
                elif _pw != su_pw2.strip():
                    st.error("Passwords do not match.")
                else:
                    try:
                        resp  = sb.auth.sign_up({"email": _email, "password": _pw})
                        _user = resp.user
                        if _user:
                            sb.table("profiles").insert({
                                "id":         _user.id,
                                "email":      _email,
                                "full_name":  _name,
                                "school_id":  f"{SCHOOL.SCHOOL_ID}-{su_title.lower().replace(' ','-')}",
                                "role":       "faculty",
                                "program_year": su_dept.strip(),
                            }).execute()
                            if resp.session and resp.session.access_token:
                                st.session_state["admin_auth"]    = True
                                st.session_state["faculty_email"] = _email
                                st.session_state["faculty_name"]  = _name
                                st.session_state.pop("sources_cache", None)
                                st.query_params["s"] = resp.session.access_token
                                st.rerun()
                            else:
                                st.success(
                                    f"Account created! Check **{_email}** for a confirmation link, "
                                    "then return here to sign in."
                                )
                        else:
                            st.error("Signup failed — this email may already be registered.")
                    except Exception as e:
                        st.error(f"Could not create account. ({e})")

        st.markdown(
            "<p style='text-align:center;font-size:0.78rem;color:#5a7090;margin-top:20px;letter-spacing:0.04em;font-weight:500;'>"
            "🔒 Faculty &amp; staff access only</p>",
            unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Top nav
# ─────────────────────────────────────────────────────────────────────────────

def show_topnav():
    st.markdown(f"""
    <div style="display:flex;align-items:center;justify-content:space-between;
                padding:18px 0 20px;border-bottom:1px solid rgba(0,144,204,0.18);
                margin-bottom:28px;">
      <div style="display:flex;align-items:center;gap:10px;">
        <div style="width:32px;height:32px;background:linear-gradient(135deg,#0090cc,#7b5ea7);
                    border-radius:8px;display:flex;align-items:center;justify-content:center;
                    font-size:14px;font-weight:900;color:#fff;font-family:'Space Grotesk',sans-serif;">D+</div>
        <div>
          <div style="font-size:1rem;font-weight:700;color:#0f1f35;font-family:'Space Grotesk',sans-serif;">
            Dent<span style="color:#0090cc;">AI</span> Assist
          </div>
          <div style="font-size:0.72rem;color:#5a7090;">{SCHOOL.SCHOOL_SHORT} Faculty Dashboard</div>
        </div>
      </div>
      <div style="font-size:0.82rem;color:#5a7090;">
        👤 {st.session_state.get("faculty_name", "")}
      </div>
    </div>
    """, unsafe_allow_html=True)

    pages = ["Overview", "Student Insights", "Gap Alerts", "Upload Content", "Knowledge Base"]
    cols = st.columns(len(pages) + 1)
    for i, label in enumerate(pages):
        with cols[i]:
            if st.button(label, key=f"nav_{label}", use_container_width=True,
                         type="primary" if st.session_state.get("page") == label else "secondary"):
                st.session_state["page"] = label
                st.rerun()
    with cols[-1]:
        if st.button("Sign out", use_container_width=True):
            try:
                _get_supabase().auth.sign_out()
            except Exception:
                pass
            for k in ["admin_auth", "faculty_email", "faculty_name", "sources_cache", "page"]:
                st.session_state.pop(k, None)
            st.query_params.clear()
            st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Pages
# ─────────────────────────────────────────────────────────────────────────────

def page_overview():
    sources    = st.session_state.get("sources_cache") or list_sources()
    st.session_state["sources_cache"] = sources
    total_vecs = fetch_stats()

    st.markdown('<div class="page-title">Overview</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">Your knowledge base at a glance.</div>', unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3, gap="medium")
    with c1:
        st.markdown(f"""
        <div class="stat-card">
          <div class="stat-icon blue">Docs</div>
          <div>
            <div class="stat-num">{len(sources)}</div>
            <div class="stat-lbl">Documents indexed</div>
          </div>
        </div>""", unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
        <div class="stat-card">
          <div class="stat-icon green">Vec</div>
          <div>
            <div class="stat-num">{total_vecs:,}</div>
            <div class="stat-lbl">Total chunks in Pinecone</div>
          </div>
        </div>""", unsafe_allow_html=True)
    with c3:
        st.markdown(f"""
        <div class="stat-card">
          <div class="stat-icon purple">Stu</div>
          <div>
            <div class="stat-num">~500</div>
            <div class="stat-lbl">Students with access</div>
          </div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<div style='margin-top:28px;'></div>", unsafe_allow_html=True)

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("<h4>Recently Added</h4>", unsafe_allow_html=True)
    st.markdown('<div class="card-caption">The last 5 documents added to the knowledge base.</div>', unsafe_allow_html=True)

    recent = sources[:5]
    if not recent:
        st.info("No documents indexed yet.")
    else:
        for src in recent:
            name     = src["name"]
            date_str = src["uploaded_at"][:10] if src["uploaded_at"] else "—"
            badge    = file_type_badge(name)
            st.markdown(f"""
            <div class="src-item">
              {badge}
              <div class="src-name">{name}</div>
              <div class="src-date">{date_str}</div>
              <span class="badge">Live</span>
            </div>""", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)


def page_upload():
    st.markdown('<div class="page-title">Upload Content</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">Add course materials to the DentAI knowledge base.</div>', unsafe_allow_html=True)

    left, right = st.columns([1.1, 1], gap="large")

    with left:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("<h4>Upload Files</h4>", unsafe_allow_html=True)
        st.markdown('<div class="card-caption">Supported formats: PDF, Word (.docx), PowerPoint (.pptx), plain text (.txt)</div>', unsafe_allow_html=True)

        uploaded = st.file_uploader(
            "Drop files here",
            accept_multiple_files=True,
            type=["pdf","docx","pptx","txt"],
            label_visibility="collapsed",
        )

        if uploaded:
            st.markdown(
                f"<div style='font-size:0.83rem;color:#374151;margin:8px 0;'>"
                f"<b>{len(uploaded)}</b> file(s) ready to index</div>",
                unsafe_allow_html=True,
            )

        if st.button("Index Files", disabled=not uploaded,
                     use_container_width=True, type="primary"):
            bar  = st.progress(0, text="Starting…")
            logs = []
            for i, f in enumerate(uploaded):
                bar.progress(i / len(uploaded), text=f"Processing {f.name}…")
                try:
                    raw = f.read()
                    ext = os.path.splitext(f.name)[1].lower()
                    if ext == ".pdf":    text = extract_pdf(raw)     # list of (page_num, text)
                    elif ext == ".docx": text = extract_docx(raw)    # flat string
                    elif ext == ".pptx": text = extract_pptx(raw)    # list of (slide_num, text)
                    elif ext == ".txt":  text = raw.decode("utf-8","replace")  # flat string
                    else:
                        logs.append(("warn", f"Skipped unsupported format: {f.name}")); continue
                    _has_content = text.strip() if isinstance(text, str) else any(t.strip() for _, t in text)
                    if not _has_content:
                        logs.append(("warn", f"No text found in: {f.name}")); continue
                    n = embed_and_upsert(f.name, text)
                    log_upload_to_supabase(f.name, "file", n)
                    store_file_in_supabase(f.name, raw)  # save original for image extraction
                    logs.append(("ok", f"{f.name} — {n} chunks indexed"))
                except Exception as e:
                    logs.append(("err", f"{f.name} — {e}"))
            bar.progress(1.0, text="Done!")
            time.sleep(0.4); bar.empty()
            for kind, msg in logs:
                if kind == "ok":     st.success(msg)
                elif kind == "warn": st.warning(msg)
                else:                st.error(msg)
            st.session_state.pop("sources_cache", None)

        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("<h4>Index a Web URL</h4>", unsafe_allow_html=True)
        st.markdown(f'<div class="card-caption">ADA guidelines, {SCHOOL.SCHOOL_SHORT} pages, clinical protocols — any publicly accessible URL.</div>', unsafe_allow_html=True)

        url_val = st.text_input("URL", placeholder="https://www.ada.org/…",
                                label_visibility="collapsed")
        if st.button("Index URL", disabled=not url_val.strip(),
                     use_container_width=True, type="primary"):
            with st.spinner("Fetching page…"):
                try:
                    title, text = extract_url(url_val.strip())
                    n = embed_and_upsert(title or urlparse(url_val).netloc, text)
                    log_upload_to_supabase(title or urlparse(url_val).netloc, "url", n)
                    st.success(f"'{title}' indexed — {n} chunks added.")
                    st.session_state.pop("sources_cache", None)
                except Exception as e:
                    st.error(str(e))

        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("<h4>How it works</h4>", unsafe_allow_html=True)
        st.markdown("""
        <div class="card-caption" style="margin-bottom:20px;">
          Once uploaded, content is immediately available to students.
        </div>
        <div style="display:flex;flex-direction:column;gap:18px;">
          <div style="display:flex;gap:14px;align-items:flex-start;">
            <div style="width:28px;height:28px;border-radius:50%;background:#f3f4f6;
                        display:flex;align-items:center;justify-content:center;
                        font-size:0.8rem;font-weight:700;color:#374151;flex-shrink:0;">1</div>
            <div>
              <div style="font-size:0.88rem;font-weight:600;color:#111827;">Upload your file</div>
              <div style="font-size:0.8rem;color:#6b7280;margin-top:2px;">
                Drag a PDF, Word doc, or PowerPoint onto the upload area.
              </div>
            </div>
          </div>
          <div style="display:flex;gap:14px;align-items:flex-start;">
            <div style="width:28px;height:28px;border-radius:50%;background:#f3f4f6;
                        display:flex;align-items:center;justify-content:center;
                        font-size:0.8rem;font-weight:700;color:#374151;flex-shrink:0;">2</div>
            <div>
              <div style="font-size:0.88rem;font-weight:600;color:#111827;">Text is extracted and chunked</div>
              <div style="font-size:0.8rem;color:#6b7280;margin-top:2px;">
                The document is split into searchable sections.
              </div>
            </div>
          </div>
          <div style="display:flex;gap:14px;align-items:flex-start;">
            <div style="width:28px;height:28px;border-radius:50%;background:#f3f4f6;
                        display:flex;align-items:center;justify-content:center;
                        font-size:0.8rem;font-weight:700;color:#374151;flex-shrink:0;">3</div>
            <div>
              <div style="font-size:0.88rem;font-weight:600;color:#111827;">Indexed into Pinecone</div>
              <div style="font-size:0.8rem;color:#6b7280;margin-top:2px;">
                Sections are embedded and stored in the vector database.
              </div>
            </div>
          </div>
          <div style="display:flex;gap:14px;align-items:flex-start;">
            <div style="width:28px;height:28px;border-radius:50%;background:#f3f4f6;
                        display:flex;align-items:center;justify-content:center;
                        font-size:0.8rem;font-weight:700;color:#374151;flex-shrink:0;">4</div>
            <div>
              <div style="font-size:0.88rem;font-weight:600;color:#111827;">Live for students</div>
              <div style="font-size:0.8rem;color:#6b7280;margin-top:2px;">
                Students can immediately ask questions about the new material.
              </div>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)


def page_knowledge_base():
    st.markdown('<div class="page-title">Knowledge Base</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">All documents currently available to students.</div>', unsafe_allow_html=True)

    sources = st.session_state.get("sources_cache") or list_sources()
    st.session_state["sources_cache"] = sources

    t_left, t_right = st.columns([3, 1])
    with t_left:
        search = st.text_input("Search", placeholder="Filter by filename…",
                               label_visibility="collapsed")
    with t_right:
        if st.button("Refresh", use_container_width=True):
            st.session_state.pop("sources_cache", None)
            st.rerun()

    filtered = [s for s in sources if not search or search.lower() in s["name"].lower()]

    st.markdown('<div class="card">', unsafe_allow_html=True)

    if not filtered:
        st.info("No documents found." if search else "No documents indexed yet.")
    else:
        st.markdown(
            f"<div style='font-size:0.82rem;color:#6b7280;margin-bottom:12px;'>"
            f"{len(filtered)} document(s)</div>",
            unsafe_allow_html=True,
        )
        st.markdown("""
        <div style="display:flex;padding:0 4px 8px;border-bottom:1px solid #f3f4f6;
                    font-size:0.72rem;font-weight:600;color:#9ca3af;text-transform:uppercase;
                    letter-spacing:0.06em;">
          <div style="flex:1;">Document</div>
          <div style="width:100px;">Date Added</div>
          <div style="width:80px;"></div>
        </div>""", unsafe_allow_html=True)

        st.markdown('<div class="scroll-list">', unsafe_allow_html=True)
        for src in filtered:
            name     = src["name"]
            date_str = src["uploaded_at"][:10] if src["uploaded_at"] else "—"
            badge    = file_type_badge(name)

            row_l, row_c, row_r = st.columns([5, 1.2, 0.8])
            with row_l:
                st.markdown(
                    f"<div class='src-item' style='border:none;padding:8px 0;'>"
                    f"{badge}"
                    f"<div>"
                    f"<div class='src-name'>{name}</div>"
                    f"<span class='badge'>Live</span>"
                    f"</div></div>",
                    unsafe_allow_html=True,
                )
            with row_c:
                st.markdown(
                    f"<div style='font-size:0.78rem;color:#9ca3af;padding-top:14px;'>{date_str}</div>",
                    unsafe_allow_html=True,
                )
            with row_r:
                if st.button("Remove", key=f"del_{name}"):
                    with st.spinner("Removing…"):
                        n = delete_source(name)
                    st.warning(f"Removed '{name}' ({n} chunks).")
                    st.session_state.pop("sources_cache", None)
                    st.rerun()

            st.markdown(
                "<hr style='margin:0;border:none;border-top:1px solid #f9fafb;'>",
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Query Log Helpers
# ─────────────────────────────────────────────────────────────────────────────

QUERY_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "query_logs.json")

def _load_logs() -> list:
    """Load all query logs from local JSON file."""
    try:
        if not os.path.exists(QUERY_LOG_FILE):
            return []
        with open(QUERY_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _load_logs_cloud() -> list:
    """Load query logs from Supabase (cloud mode)."""
    try:
        from supabase import create_client
        sb = create_client(os.getenv("SUPABASE_URL",""), os.getenv("SUPABASE_KEY",""))
        result = sb.table("query_logs").select("*").order("timestamp", desc=True).limit(5000).execute()
        return result.data or []
    except Exception:
        return []

def load_query_logs() -> list:
    mode = os.getenv("MODE", "local").lower()
    if mode == "cloud":
        return _load_logs_cloud()
    return _load_logs()

def filter_logs_by_date(logs: list, days: int) -> list:
    if days <= 0:
        return logs
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    return [l for l in logs if l.get("timestamp", "") >= cutoff]


# ─────────────────────────────────────────────────────────────────────────────
# Insights Page
# ─────────────────────────────────────────────────────────────────────────────

def page_insights():
    st.markdown('<div class="page-title">Student Insights</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="page-sub">What your students are asking — aggregated across all sessions.</div>',
        unsafe_allow_html=True,
    )

    # ── Date range selector ──
    col_range, col_refresh = st.columns([3, 1])
    with col_range:
        range_label = st.selectbox(
            "Date range",
            ["This week (7 days)", "Last 30 days", "Last 90 days", "All time"],
            label_visibility="collapsed",
        )
    with col_refresh:
        if st.button("↻  Refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    days_map = {
        "This week (7 days)": 7,
        "Last 30 days": 30,
        "Last 90 days": 90,
        "All time": 0,
    }
    days = days_map[range_label]

    all_logs  = load_query_logs()
    logs      = filter_logs_by_date(all_logs, days)

    if not logs:
        st.info("No query data yet. Students need to submit questions in the app first.")
        return

    # ── Compute aggregates ──
    total_queries   = len(logs)
    unique_students = len(set(l.get("user_email", "unknown") for l in logs))

    topic_counts: Counter = Counter()
    for l in logs:
        for t in l.get("topics", ["General / Other"]):
            topic_counts[t] += 1
    top_topic = topic_counts.most_common(1)[0][0] if topic_counts else "—"

    # Queries per day (last 14 days for the chart, regardless of filter)
    day_counts: Counter = Counter()
    for l in logs:
        ts = l.get("timestamp", "")
        if ts:
            day_counts[ts[:10]] += 1

    # ── Stat cards ──
    c1, c2, c3, c4 = st.columns(4, gap="medium")
    cards = [
        (c1, "blue",   "Qry",    str(total_queries),   "Total queries"),
        (c2, "green",  "Stu",    str(unique_students),  "Active students"),
        (c3, "purple", "Top",    top_topic.split("/")[0].strip()[:8], "Leading topic"),
        (c4, "blue",   "Day",    f"{total_queries // max(days, 1)}", "Avg queries/day"),
    ]
    for col, color, icon, num, label in cards:
        with col:
            st.markdown(f"""
            <div class="stat-card">
              <div class="stat-icon {color}">{icon}</div>
              <div>
                <div class="stat-num">{num}</div>
                <div class="stat-lbl">{label}</div>
              </div>
            </div>""", unsafe_allow_html=True)

    st.markdown("<div style='margin-top:28px;'></div>", unsafe_allow_html=True)

    left, right = st.columns([1.1, 1], gap="large")

    # ── Topic breakdown ──
    with left:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("<h4>Topics by Volume</h4>", unsafe_allow_html=True)
        st.markdown(
            '<div class="card-caption">How many questions were asked about each clinical area.</div>',
            unsafe_allow_html=True,
        )

        sorted_topics = topic_counts.most_common()
        max_count = sorted_topics[0][1] if sorted_topics else 1

        for topic, count in sorted_topics[:12]:
            pct = int((count / max_count) * 100)
            bar_color = "#0090cc"
            st.markdown(f"""
            <div style="margin-bottom:12px;">
              <div style="display:flex;justify-content:space-between;
                          font-size:0.83rem;margin-bottom:4px;">
                <span style="color:#0f1f35;font-weight:500;">{topic}</span>
                <span style="color:#5a7090;">{count} {'query' if count == 1 else 'queries'}</span>
              </div>
              <div style="background:rgba(255,255,255,0.06);border-radius:4px;height:6px;">
                <div style="width:{pct}%;background:{bar_color};border-radius:4px;
                             height:6px;transition:width 0.3s;"></div>
              </div>
            </div>""", unsafe_allow_html=True)

        st.markdown("</div>", unsafe_allow_html=True)

    # ── Top questions + daily activity ──
    with right:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("<h4>Most Recent Questions</h4>", unsafe_allow_html=True)
        st.markdown(
            '<div class="card-caption">Last 15 questions submitted by students.</div>',
            unsafe_allow_html=True,
        )

        recent = sorted(logs, key=lambda x: x.get("timestamp",""), reverse=True)[:15]
        st.markdown('<div class="scroll-list">', unsafe_allow_html=True)
        for entry in recent:
            q      = entry.get("question", "")[:120]
            ts     = entry.get("timestamp", "")[:10]
            email  = entry.get("user_email", "unknown")
            topics = ", ".join(entry.get("topics", []))
            st.markdown(f"""
            <div class="src-item" style="flex-direction:column;align-items:flex-start;gap:4px;">
              <div style="font-size:0.86rem;color:#0f1f35;font-weight:500;line-height:1.4;">
                {q}{"…" if len(entry.get("question","")) > 120 else ""}
              </div>
              <div style="display:flex;gap:12px;">
                <span style="font-size:0.72rem;color:#5a7090;">{email}</span>
                <span style="font-size:0.72rem;color:#5a7090;">{ts}</span>
                <span style="font-size:0.72rem;color:#0090cc;">{topics}</span>
              </div>
            </div>""", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        # ── Daily activity sparkline ──
        if day_counts:
            st.markdown('<div class="card" style="margin-top:0;">', unsafe_allow_html=True)
            st.markdown("<h4>Daily Activity</h4>", unsafe_allow_html=True)

            # Build sorted date series for st.bar_chart
            sorted_days = sorted(day_counts.items())[-14:]  # last 14 days
            chart_data  = {d: c for d, c in sorted_days}

            import streamlit as _st
            _st.bar_chart(
                chart_data,
                color="#0090cc",
                height=120,
            )
            st.markdown("</div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Gap Alerts Page — Patent Claim 5
# ─────────────────────────────────────────────────────────────────────────────

def page_gap_alerts():
    st.markdown('<div class="page-title">Knowledge Gap Alerts</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="page-sub">Topics where multiple students show repeated difficulty — '
        'aggregated from student query history.</div>',
        unsafe_allow_html=True,
    )

    _mode = os.getenv("MODE", "local").lower()

    # ── Controls row ──────────────────────────────────────────────────────────
    col_thresh, col_days, col_run, col_spacer = st.columns([1.2, 1.2, 1, 2])
    with col_thresh:
        threshold = st.number_input(
            "Alert threshold (unique students)",
            min_value=2, max_value=50,
            value=DEFAULT_THRESHOLD,
            help="An alert fires when this many distinct students have asked about the same topic.",
        )
    with col_days:
        days = st.selectbox(
            "Look-back window",
            options=[7, 30, 90, 0],
            format_func=lambda d: {7: "7 days", 30: "30 days", 90: "90 days", 0: "All time"}[d],
            index=1,
        )
    with col_run:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        run_analysis = st.button("▶  Run Analysis", type="primary", use_container_width=True)

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ── Run analysis on demand ────────────────────────────────────────────────
    if run_analysis:
        all_logs = load_query_logs()
        with st.spinner("Analyzing student query history…"):
            fired = compute_gap_alerts(
                query_logs=all_logs,
                threshold=int(threshold),
                days=int(days),
                mode=_mode,
            )
        if fired:
            st.success(f"✅  Analysis complete — {len(fired)} gap alert(s) detected or refreshed.")
        else:
            st.info("No topics exceeded the threshold in the selected window.")

    # ── Load and display alerts ───────────────────────────────────────────────
    alerts = load_gap_alerts(mode=_mode)
    active  = [a for a in alerts if not a.get("resolved")]
    resolved = [a for a in alerts if a.get("resolved")]

    if not alerts:
        st.markdown("""
        <div style="background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);
                    border-radius:12px;padding:32px;text-align:center;margin-top:20px;">
          <div style="font-size:2rem;margin-bottom:12px;">📊</div>
          <div style="color:#0f1f35;font-weight:600;margin-bottom:6px;">No alerts yet</div>
          <div style="color:#5a7090;font-size:0.87rem;">
            Click <strong>Run Analysis</strong> to scan student query history for persistent knowledge gaps.
          </div>
        </div>
        """, unsafe_allow_html=True)
        return

    # ── Active alerts ─────────────────────────────────────────────────────────
    if active:
        st.markdown(
            f"<div style='font-size:0.95rem;font-weight:700;color:#0f1f35;"
            f"margin-bottom:16px;'>🔴  Active Alerts ({len(active)})</div>",
            unsafe_allow_html=True,
        )
        for alert in active:
            topic    = alert.get("topic", "—")
            count    = alert.get("student_count", 0)
            ts       = alert.get("created_at", "")[:10]
            students = alert.get("student_emails", [])

            col_card, col_resolve = st.columns([5, 1])
            with col_card:
                st.markdown(f"""
                <div style="background:rgba(255,80,80,0.07);border:1px solid rgba(255,80,80,0.25);
                            border-left:4px solid #ff5050;border-radius:10px;
                            padding:16px 20px;margin-bottom:10px;">
                  <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                    <div>
                      <div style="font-size:1rem;font-weight:700;color:#ff8080;margin-bottom:4px;">
                        {topic}
                      </div>
                      <div style="font-size:0.82rem;color:#5a7090;">
                        {count} student{'s' if count != 1 else ''} affected &nbsp;·&nbsp; Detected {ts}
                      </div>
                    </div>
                    <div style="background:rgba(255,80,80,0.18);border-radius:20px;
                                padding:4px 14px;font-size:0.78rem;font-weight:700;color:#ff8080;">
                      {count} students
                    </div>
                  </div>
                  {"" if not students else
                    "<div style='margin-top:10px;font-size:0.76rem;color:#5a7090;'>"
                    + "  ".join(f"<span style='background:rgba(255,255,255,0.06);"
                                f"border-radius:4px;padding:2px 7px;margin:2px 2px 0 0;"
                                f"display:inline-block;'>{e}</span>" for e in students[:8])
                    + ("…" if len(students) > 8 else "")
                    + "</div>"
                  }
                </div>
                """, unsafe_allow_html=True)
            with col_resolve:
                st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
                if st.button("✓ Resolve", key=f"resolve_{topic}", use_container_width=True):
                    resolve_alert(topic, mode=_mode)
                    st.rerun()

    # ── Resolved alerts ───────────────────────────────────────────────────────
    if resolved:
        with st.expander(f"✅  Resolved alerts ({len(resolved)})", expanded=False):
            for alert in resolved:
                topic = alert.get("topic", "—")
                count = alert.get("student_count", 0)
                ts    = alert.get("created_at", "")[:10]
                st.markdown(f"""
                <div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.07);
                            border-radius:8px;padding:12px 16px;margin-bottom:8px;opacity:0.65;">
                  <span style="color:#0f1f35;font-weight:600;">{topic}</span>
                  <span style="color:#5a7090;font-size:0.8rem;margin-left:12px;">
                    {count} students &nbsp;·&nbsp; {ts} &nbsp;·&nbsp; resolved
                  </span>
                </div>
                """, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if not st.session_state.get("admin_auth"):
    show_login()
else:
    if "page" not in st.session_state:
        st.session_state["page"] = "Overview"
    show_topnav()
    page = st.session_state.get("page", "Overview")
    if page == "Overview":            page_overview()
    elif page == "Student Insights":  page_insights()
    elif page == "Gap Alerts":        page_gap_alerts()
    elif page == "Upload Content":    page_upload()
    elif page == "Knowledge Base":    page_knowledge_base()

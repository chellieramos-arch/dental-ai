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
    --bg: #050a12;
    --surface: #0c1524;
    --surface2: #111e30;
    --border: rgba(0,200,255,0.12);
    --cyan: #00c8ff;
    --purple: #7b5ea7;
    --text: #e8f0fe;
    --muted: #7a90b0;
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
    background: #030810 !important;
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
    background: rgba(0,200,255,0.08) !important;
    color: var(--cyan) !important;
  }
  [data-testid="stSidebar"] .nav-active > button {
    background: rgba(0,200,255,0.10) !important;
    color: var(--cyan) !important;
    border-left: 2px solid var(--cyan) !important;
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
  .stat-icon.blue   { background: rgba(0,200,255,0.12); color: var(--cyan); }
  .stat-icon.green  { background: rgba(0,200,100,0.12); color: #00c851; }
  .stat-icon.purple { background: rgba(123,94,167,0.18); color: #a78bfa; }
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
  .file-type-badge.pdf  { background: rgba(239,68,68,0.15); color: #f87171; }
  .file-type-badge.doc  { background: rgba(0,200,255,0.12); color: var(--cyan); }
  .file-type-badge.ppt  { background: rgba(251,146,60,0.15); color: #fb923c; }
  .file-type-badge.web  { background: rgba(0,200,100,0.12); color: #00c851; }
  .file-type-badge.txt  { background: rgba(255,255,255,0.06); color: var(--muted); }
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
    border: 1.5px dashed rgba(0,200,255,0.25) !important;
    border-radius: 10px !important;
    background: rgba(0,200,255,0.03) !important;
    padding: 8px !important;
  }
  [data-testid="stTextInput"] input,
  [data-testid="stTextArea"] textarea {
    background: var(--bg) !important;
    border: 1px solid var(--border) !important;
    border-radius: 8px !important;
    color: var(--text) !important;
  }
  [data-testid="stTextInput"] input:focus,
  [data-testid="stTextArea"] textarea:focus {
    border-color: var(--cyan) !important;
    box-shadow: 0 0 0 3px rgba(0,200,255,0.08) !important;
  }

  /* ── Primary button ── */
  .stButton > button[kind="primary"] {
    background: linear-gradient(135deg, var(--cyan), #0090cc) !important;
    color: #000000 !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 800 !important;
    font-size: 1rem !important;
    padding: 10px 20px !important;
    opacity: 1 !important;
  }
  .stButton > button[kind="primary"] p {
    color: #000000 !important;
    font-weight: 800 !important;
    opacity: 1 !important;
  }
  .stButton > button[kind="primary"]:hover {
    box-shadow: 0 4px 20px rgba(0,200,255,0.35) !important;
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
  [data-testid="stTextInput"] input::placeholder { color: #7a90b0 !important; opacity: 1 !important; }
  [data-testid="stTextInput"] input { color: #e8f0fe !important; font-size: 0.95rem !important; }

  /* ── Login ── */
  .login-container {
    max-width: 400px;
    margin: 60px auto;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 44px 40px;
    text-align: center;
  }

  /* ── Badge ── */
  .badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 999px;
    font-size: 0.72rem;
    font-weight: 600;
    background: rgba(0,200,100,0.12);
    color: #00c851;
    border: 1px solid rgba(0,200,100,0.2);
  }

  /* ── Scrollable list ── */
  .scroll-list {
    max-height: 420px;
    overflow-y: auto;
    padding-right: 4px;
  }
  .scroll-list::-webkit-scrollbar { width: 4px; }
  .scroll-list::-webkit-scrollbar-track { background: transparent; }
  .scroll-list::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }

  /* ── Streamlit overrides ── */
  .stAlert { border-radius: 10px !important; }
  [data-testid="stMarkdownContainer"] p { color: var(--muted); }
  .stProgress > div > div { background: var(--cyan) !important; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
EMBED_MODEL    = "text-embedding-ada-002"
CHUNK_SIZE     = 1500
CHUNK_OVERLAP  = 200
BATCH_SIZE     = 96
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

def _admin_token() -> str:
    """Deterministic token derived from the admin password — stored in URL to survive refresh."""
    import hashlib
    return hashlib.sha256(f"dentai_admin:{ADMIN_PASSWORD}".encode()).hexdigest()[:24]

# ── Auto-restore admin session from URL token ─────────────────────────────────
if ADMIN_PASSWORD and not st.session_state.get("admin_auth"):
    if st.query_params.get("t") == _admin_token():
        st.session_state["admin_auth"] = True


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
    import fitz
    doc = fitz.open(stream=b, filetype="pdf")
    text = "".join(p.get_text() for p in doc); doc.close(); return text

def extract_docx(b):
    from docx import Document
    return "\n".join(p.text for p in Document(io.BytesIO(b)).paragraphs if p.text.strip())

def extract_pptx(b):
    from pptx import Presentation
    lines = []
    for slide in Presentation(io.BytesIO(b)).slides:
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text.strip():
                lines.append(shape.text.strip())
    return "\n".join(lines)

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

def log_upload_to_supabase(file_name: str, source_type: str, vector_count: int):
    """Log a successful upload to the Supabase documents table."""
    try:
        from supabase import create_client
        url = st.secrets.get("SUPABASE_URL") or os.getenv("SUPABASE_URL", "")
        key = st.secrets.get("SUPABASE_KEY") or os.getenv("SUPABASE_KEY", "")
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

def embed_and_upsert(source_name, text):
    import openai
    idx    = get_index()
    oai    = openai.OpenAI()
    chunks = chunk_text(text)
    if not chunks: return 0
    total = 0
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i:i+BATCH_SIZE]
        resp  = oai.embeddings.create(input=batch, model=EMBED_MODEL)
        idx.upsert(vectors=[{
            "id": f"{source_name}::{i+j}",
            "values": item.embedding,
            "metadata": {
                "text": batch[j], "file_name": source_name,
                "chunk_index": i+j,
                "uploaded_at": datetime.utcnow().isoformat(),
            },
        } for j, item in enumerate(resp.data)])
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
        st.markdown("""
        <div class="login-container">
          <div style="width:56px;height:56px;background:linear-gradient(135deg,#00c8ff,#7b5ea7);
                      border-radius:14px;margin:0 auto 18px;display:flex;align-items:center;
                      justify-content:center;font-family:'Space Grotesk',sans-serif;
                      font-size:22px;font-weight:900;color:#fff;letter-spacing:-1px;">D+</div>
          <div style="font-size:1.4rem;font-weight:700;color:#e8f0fe;font-family:'Space Grotesk',sans-serif;">DentAI Admin</div>
          <div style="font-size:0.85rem;color:#00c8ff;margin-top:6px;margin-bottom:28px;font-weight:500;letter-spacing:0.04em;">
            Faculty &amp; Admin Portal
          </div>
        </div>
        """, unsafe_allow_html=True)
        pw = st.text_input("Password", type="password",
                           placeholder="Admin password",
                           label_visibility="collapsed")
        if st.button("Sign in", use_container_width=True, type="primary"):
            if ADMIN_PASSWORD and pw == ADMIN_PASSWORD:
                st.session_state["admin_auth"] = True
                st.session_state.pop("sources_cache", None)
                st.query_params["t"] = _admin_token()
                st.rerun()
            elif not ADMIN_PASSWORD:
                st.error("ADMIN_PASSWORD not set in secrets.")
            else:
                st.error("Incorrect password.")
        st.markdown(
            "<p style='text-align:center;font-size:0.78rem;color:#7a90b0;margin-top:14px;letter-spacing:0.04em;'>"
            "🔒 Faculty &amp; staff access only</p>",
            unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Top nav
# ─────────────────────────────────────────────────────────────────────────────

def show_topnav():
    st.markdown("""
    <div style="display:flex;align-items:center;justify-content:space-between;
                padding:18px 0 20px;border-bottom:1px solid rgba(0,200,255,0.12);
                margin-bottom:28px;">
      <div style="display:flex;align-items:center;gap:10px;">
        <div style="width:32px;height:32px;background:linear-gradient(135deg,#00c8ff,#7b5ea7);
                    border-radius:8px;display:flex;align-items:center;justify-content:center;
                    font-size:14px;font-weight:900;color:#fff;font-family:'Space Grotesk',sans-serif;">D+</div>
        <div>
          <div style="font-size:1rem;font-weight:700;color:#e8f0fe;font-family:'Space Grotesk',sans-serif;">
            Dent<span style="color:#00c8ff;">AI</span> Assist
          </div>
          <div style="font-size:0.72rem;color:#7a90b0;">Faculty Dashboard</div>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    pages = ["Overview", "Student Insights", "Upload Content", "Knowledge Base"]
    cols = st.columns(len(pages) + 1)
    for i, label in enumerate(pages):
        with cols[i]:
            if st.button(label, key=f"nav_{label}", use_container_width=True,
                         type="primary" if st.session_state.get("page") == label else "secondary"):
                st.session_state["page"] = label
                st.rerun()
    with cols[-1]:
        if st.button("Sign out", use_container_width=True):
            for k in ["admin_auth", "sources_cache", "page"]:
                st.session_state.pop(k, None)
            st.query_params.pop("t", None)
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
                    if ext == ".pdf":    text = extract_pdf(raw)
                    elif ext == ".docx": text = extract_docx(raw)
                    elif ext == ".pptx": text = extract_pptx(raw)
                    elif ext == ".txt":  text = raw.decode("utf-8","replace")
                    else:
                        logs.append(("warn", f"Skipped unsupported format: {f.name}")); continue
                    if not text.strip():
                        logs.append(("warn", f"No text found in: {f.name}")); continue
                    n = embed_and_upsert(f.name, text)
                    log_upload_to_supabase(f.name, "file", n)
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
        st.markdown('<div class="card-caption">ADA guidelines, NSU pages, clinical protocols — any publicly accessible URL.</div>', unsafe_allow_html=True)

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
            bar_color = "#00c8ff"
            st.markdown(f"""
            <div style="margin-bottom:12px;">
              <div style="display:flex;justify-content:space-between;
                          font-size:0.83rem;margin-bottom:4px;">
                <span style="color:#e8f0fe;font-weight:500;">{topic}</span>
                <span style="color:#7a90b0;">{count} {'query' if count == 1 else 'queries'}</span>
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
              <div style="font-size:0.86rem;color:#e8f0fe;font-weight:500;line-height:1.4;">
                {q}{"…" if len(entry.get("question","")) > 120 else ""}
              </div>
              <div style="display:flex;gap:12px;">
                <span style="font-size:0.72rem;color:#7a90b0;">{email}</span>
                <span style="font-size:0.72rem;color:#7a90b0;">{ts}</span>
                <span style="font-size:0.72rem;color:#00c8ff;">{topics}</span>
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
                color="#00c8ff",
                height=120,
            )
            st.markdown("</div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if not ADMIN_PASSWORD:
    st.error("ADMIN_PASSWORD is not set. Add it to .streamlit/secrets.toml.")
    st.stop()

if not st.session_state.get("admin_auth"):
    show_login()
else:
    if "page" not in st.session_state:
        st.session_state["page"] = "Overview"
    show_topnav()
    page = st.session_state.get("page", "Overview")
    if page == "Overview":            page_overview()
    elif page == "Student Insights":  page_insights()
    elif page == "Upload Content":    page_upload()
    elif page == "Knowledge Base":    page_knowledge_base()

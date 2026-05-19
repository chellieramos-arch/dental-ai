"""
DentAI — Faculty Content Upload Portal
────────────────────────────────────────
Password-protected admin page for uploading course materials into the
DentAI knowledge base (Pinecone vector store).

Supported content types:
  • PDF (.pdf)
  • Word documents (.docx)
  • PowerPoint presentations (.pptx)
  • Plain text (.txt)
  • Web URLs (fetched and indexed)

Access: Faculty/admin only — requires ADMIN_PASSWORD from Streamlit secrets.
"""

import os
import io
import hashlib
import time
from datetime import datetime
from urllib.parse import urlparse

import streamlit as st
from dotenv import load_dotenv

# ── Copy Streamlit secrets → os.environ ──────────────────────────────────────
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
    page_title="DentAI — Admin Upload",
    page_icon="🦷",
    layout="centered",
)

# ── Constants ─────────────────────────────────────────────────────────────────
EMBED_MODEL   = "text-embedding-ada-002"
CHUNK_SIZE    = 1500
CHUNK_OVERLAP = 200
BATCH_SIZE    = 96

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def chunk_text(text: str) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start : start + CHUNK_SIZE])
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return [c.strip() for c in chunks if c.strip()]


def extract_pdf(file_bytes: bytes) -> str:
    import fitz
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    text = "".join(page.get_text() for page in doc)
    doc.close()
    return text


def extract_docx(file_bytes: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(file_bytes))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def extract_pptx(file_bytes: bytes) -> str:
    from pptx import Presentation
    prs = Presentation(io.BytesIO(file_bytes))
    lines = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text.strip():
                lines.append(shape.text.strip())
    return "\n".join(lines)


def extract_url(url: str) -> tuple[str, str]:
    """Fetch a URL and return (title, text). Raises on failure."""
    import requests
    from bs4 import BeautifulSoup

    resp = requests.get(url, timeout=15, headers={"User-Agent": "DentAI-Indexer/1.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    title = soup.title.string.strip() if soup.title else urlparse(url).netloc
    # Remove script/style noise
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Collapse blank lines
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return title, "\n".join(lines)


def get_pinecone_index():
    from pinecone import Pinecone
    pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY", ""))
    return pc.Index(os.getenv("PINECONE_INDEX", "dentai"))


def embed_and_upsert(index, source_name: str, text: str) -> int:
    """Chunk text, embed via OpenAI, upsert to Pinecone. Returns vector count."""
    import openai
    oai    = openai.OpenAI()
    chunks = chunk_text(text)
    if not chunks:
        return 0

    total = 0
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i : i + BATCH_SIZE]
        resp  = oai.embeddings.create(input=batch, model=EMBED_MODEL)
        vectors = [
            {
                "id": f"{source_name}::{i + j}",
                "values": item.embedding,
                "metadata": {
                    "text":        batch[j],
                    "file_name":   source_name,
                    "chunk_index": i + j,
                    "uploaded_at": datetime.utcnow().isoformat(),
                },
            }
            for j, item in enumerate(resp.data)
        ]
        index.upsert(vectors=vectors)
        total += len(vectors)

    return total


def list_indexed_sources(index) -> list[str]:
    """Return unique source names currently in the index (via metadata scan)."""
    try:
        # Fetch a sample of vectors to get unique file_name values
        result = index.query(
            vector=[0.0] * 1536,
            top_k=200,
            include_metadata=True,
        )
        sources = sorted({
            m["metadata"].get("file_name", "unknown")
            for m in result.get("matches", [])
            if m.get("metadata")
        })
        return sources
    except Exception:
        return []


def delete_source(index, source_name: str) -> int:
    """Delete all vectors for a given source_name. Returns count deleted."""
    try:
        result = index.query(
            vector=[0.0] * 1536,
            top_k=1000,
            include_metadata=True,
            filter={"file_name": {"$eq": source_name}},
        )
        ids = [m["id"] for m in result.get("matches", [])]
        if ids:
            index.delete(ids=ids)
        return len(ids)
    except Exception as e:
        st.error(f"Delete failed: {e}")
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# Auth gate
# ─────────────────────────────────────────────────────────────────────────────

def auth_gate():
    st.markdown("## 🔐 Admin Login")
    st.caption("Faculty and staff only. Enter the admin password to continue.")
    pw = st.text_input("Password", type="password", placeholder="Enter admin password")
    if st.button("Login", use_container_width=True):
        if ADMIN_PASSWORD and pw == ADMIN_PASSWORD:
            st.session_state["admin_auth"] = True
            st.rerun()
        else:
            st.error("Incorrect password. Please try again.")


# ─────────────────────────────────────────────────────────────────────────────
# Main admin UI
# ─────────────────────────────────────────────────────────────────────────────

def admin_ui():
    st.markdown("# 🦷 DentAI — Content Upload Portal")
    st.caption("Upload course materials for NSU dental students to query.")

    if st.button("🔓 Logout", key="logout"):
        st.session_state.pop("admin_auth", None)
        st.rerun()

    st.divider()

    # ── Upload section ────────────────────────────────────────────────────────
    st.markdown("### 📤 Upload Documents")
    st.markdown(
        "Accepted formats: **PDF**, **Word (.docx)**, **PowerPoint (.pptx)**, **Plain text (.txt)**"
    )

    uploaded_files = st.file_uploader(
        "Choose files",
        accept_multiple_files=True,
        type=["pdf", "docx", "pptx", "txt"],
        label_visibility="collapsed",
    )

    if uploaded_files and st.button("⚡ Index Selected Files", use_container_width=True, type="primary"):
        index = get_pinecone_index()
        progress = st.progress(0)
        status   = st.empty()

        for idx, f in enumerate(uploaded_files):
            source_name = f.name
            status.info(f"Processing **{source_name}**…")
            try:
                raw = f.read()
                ext = os.path.splitext(f.name)[1].lower()

                if ext == ".pdf":
                    text = extract_pdf(raw)
                elif ext == ".docx":
                    text = extract_docx(raw)
                elif ext == ".pptx":
                    text = extract_pptx(raw)
                elif ext == ".txt":
                    text = raw.decode("utf-8", errors="replace")
                else:
                    st.warning(f"Skipped unsupported format: {f.name}")
                    continue

                if not text.strip():
                    st.warning(f"⚠️ No text extracted from **{f.name}** — skipped.")
                    continue

                n = embed_and_upsert(index, source_name, text)
                st.success(f"✅ **{f.name}** — {n} vectors indexed.")

            except Exception as e:
                st.error(f"❌ **{f.name}** failed: {e}")

            progress.progress((idx + 1) / len(uploaded_files))
            time.sleep(0.2)

        status.empty()
        progress.empty()
        st.success("🎉 All files processed!")

    st.divider()

    # ── URL section ───────────────────────────────────────────────────────────
    st.markdown("### 🌐 Index a Web URL")
    st.caption("Paste any publicly accessible URL — DentAI will fetch and index the page text.")

    url_input = st.text_input("URL", placeholder="https://example.com/dental-protocol")
    if st.button("⚡ Index URL", use_container_width=True):
        if not url_input.strip():
            st.warning("Please enter a URL.")
        else:
            with st.spinner(f"Fetching {url_input}…"):
                try:
                    title, text = extract_url(url_input.strip())
                    source_name = title or urlparse(url_input).netloc
                    index = get_pinecone_index()
                    n = embed_and_upsert(index, source_name, text)
                    st.success(f"✅ **{source_name}** — {n} vectors indexed.")
                except Exception as e:
                    st.error(f"❌ Failed to index URL: {e}")

    st.divider()

    # ── Indexed sources ───────────────────────────────────────────────────────
    st.markdown("### 📚 Currently Indexed Sources")
    st.caption("These sources are live in the knowledge base and searchable by students.")

    if st.button("🔄 Refresh Source List"):
        st.session_state.pop("indexed_sources", None)

    if "indexed_sources" not in st.session_state:
        with st.spinner("Scanning index…"):
            index = get_pinecone_index()
            st.session_state["indexed_sources"] = list_indexed_sources(index)

    sources = st.session_state.get("indexed_sources", [])

    if not sources:
        st.info("No sources found in the index yet.")
    else:
        st.markdown(f"**{len(sources)} source(s) indexed:**")
        for src in sources:
            col1, col2 = st.columns([5, 1])
            col1.markdown(f"📄 {src}")
            if col2.button("🗑️", key=f"del_{src}", help=f"Remove {src} from index"):
                index = get_pinecone_index()
                n = delete_source(index, src)
                st.warning(f"Removed **{n}** vectors for **{src}**.")
                st.session_state.pop("indexed_sources", None)
                st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if not ADMIN_PASSWORD:
    st.error(
        "⚠️ `ADMIN_PASSWORD` is not set in Streamlit secrets. "
        "Add it to `.streamlit/secrets.toml` before using this page."
    )
    st.stop()

if not st.session_state.get("admin_auth"):
    auth_gate()
else:
    admin_ui()

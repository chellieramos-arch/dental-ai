import os
import json
import base64
from datetime import datetime
from dotenv import load_dotenv
import streamlit as st
import streamlit.components.v1 as components

# ─── Copy Streamlit secrets → os.environ (needed for config.py on Cloud) ─────
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, str):
            os.environ.setdefault(_k, _v)
except Exception:
    pass  # running locally without secrets.toml is fine

import anthropic
from llama_index.core import VectorStoreIndex
from llama_index.core import StorageContext

# ─── Config (controls local vs cloud mode) ───────────────────────────────────
from config import IS_LOCAL, IS_CLOUD, CHROMA_PATH, CHROMA_COLLECTION

# Local-only imports
if IS_LOCAL:
    import chromadb
    from llama_index.vector_stores.chroma import ChromaVectorStore

# Cloud-only imports
if IS_CLOUD:
    from pinecone import Pinecone as PineconeClient
    from supabase import create_client as create_supabase_client

try:
    import fitz  # PyMuPDF
    PYMUPDF_OK = True
except ImportError:
    PYMUPDF_OK = False

# Load API keys
load_dotenv()
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY")

# ─── Session Cache (local = JSON file, cloud = Supabase) ─────────────────────
# user_ctx is never saved (too large — contains full PDF excerpts).

CACHE_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_cache.json")
MAX_SESSIONS = 50

def _get_supabase():
    """Return a cached Supabase client (cloud mode only)."""
    if "supabase_client" not in st.session_state:
        from config import SUPABASE_URL, SUPABASE_KEY
        st.session_state.supabase_client = create_supabase_client(SUPABASE_URL, SUPABASE_KEY)
    return st.session_state.supabase_client

def _current_user_email() -> str:
    """Return the logged-in user's email (cloud) or 'local' (local mode)."""
    if IS_CLOUD:
        return st.session_state.get("user_email", "unknown@nsu.edu")
    return "local"

# ── Local helpers (JSON file) ─────────────────────────────────────────────────
def _read_all_sessions() -> list:
    if not os.path.exists(CACHE_FILE):
        return []
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("sessions", [])
    except Exception:
        return []

def _write_all_sessions(sessions: list) -> None:
    try:
        sessions = sessions[-MAX_SESSIONS:]
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"sessions": sessions}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# ── Public session API (works for both modes) ─────────────────────────────────
def _build_exchanges(history: list) -> list:
    return [
        {
            "user":      ex["user"],
            "assistant": ex["assistant"],
            "sources":   ex.get("sources", []),
            "timestamp": ex.get("timestamp", datetime.now().isoformat()),
        }
        for ex in history
    ]

def save_session(session_id: str, history: list) -> None:
    """Upsert the current session. Skips user_ctx to save space."""
    exchanges = _build_exchanges(history)
    title = history[0]["user"][:60] if history else "Untitled"

    if IS_CLOUD:
        try:
            import json as _json
            sb = _get_supabase()
            exchanges_safe = _json.loads(_json.dumps(exchanges, default=str))
            result = sb.table("chat_sessions").upsert({
                "id":         session_id,
                "user_email": _current_user_email(),
                "title":      title,
                "exchanges":  exchanges_safe,
            }).execute()
        except Exception:
            pass
        return

    # Local: JSON file
    sessions = _read_all_sessions()
    for s in sessions:
        if s["id"] == session_id:
            s["exchanges"] = exchanges
            s["title"]     = title
            _write_all_sessions(sessions)
            return
    sessions.append({
        "id":         session_id,
        "title":      title,
        "created_at": history[0].get("timestamp", datetime.now().isoformat()) if history else datetime.now().isoformat(),
        "exchanges":  exchanges,
    })
    _write_all_sessions(sessions)

def load_session(session_id: str) -> list:
    """Return the exchange list for a session."""
    if IS_CLOUD:
        try:
            sb = _get_supabase()
            result = sb.table("chat_sessions").select("exchanges").eq("id", session_id).execute()
            if result.data:
                return result.data[0]["exchanges"]
        except Exception:
            pass
        return []

    for s in _read_all_sessions():
        if s["id"] == session_id:
            return s["exchanges"]
    return []

def delete_session(session_id: str) -> None:
    if IS_CLOUD:
        try:
            _get_supabase().table("chat_sessions").delete().eq("id", session_id).execute()
        except Exception:
            pass
        return
    sessions = [s for s in _read_all_sessions() if s["id"] != session_id]
    _write_all_sessions(sessions)

def rename_session(session_id: str, new_title: str) -> None:
    """Update just the title of a session."""
    if IS_CLOUD:
        try:
            _get_supabase().table("chat_sessions").update({"title": new_title}).eq("id", session_id).execute()
        except Exception:
            pass
        return
    sessions = _read_all_sessions()
    for s in sessions:
        if s["id"] == session_id:
            s["title"] = new_title
            break
    _write_all_sessions(sessions)

def list_all_sessions() -> list:
    """Return all sessions for the current user."""
    if IS_CLOUD:
        try:
            sb = _get_supabase()
            result = (
                sb.table("chat_sessions")
                  .select("id, title, created_at")
                  .eq("user_email", _current_user_email())
                  .order("created_at", desc=True)
                  .limit(MAX_SESSIONS)
                  .execute()
            )
            return result.data or []
        except Exception:
            return []
    return _read_all_sessions()

def new_session_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")

# ─── Supabase Email OTP Login Gate (cloud mode only) ─────────────────────────
# Students enter their NSU email → receive a 6-digit code → enter it → done.
# No Azure, no passwords, no IT department required.

_NSU_DOMAINS = ("@mynsu.nova.edu", "@nova.edu", "@health.snova.edu")

def _is_nsu_email(email: str) -> bool:
    return any(email.strip().lower().endswith(d) for d in _NSU_DOMAINS)

if IS_CLOUD and "user_email" not in st.session_state:
    st.set_page_config(page_title="DentAI – NSU Login", page_icon="🦷", layout="centered")

    st.markdown("""
        <div style="text-align:center; padding:3rem 0 1.5rem;">
            <h1 style="font-size:2.5rem; margin-bottom:0.25rem;">🦷 Dent<span style="color:#2563eb;">AI</span></h1>
            <p style="color:#6b7280; font-size:1.05rem;">
                NSU College of Dental Medicine · Clinical Study Assistant
            </p>
        </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        sb = _get_supabase()

        # ── Step 1: collect email and send OTP code ───────────────────────────
        if "otp_sent_to" not in st.session_state:
            email_input = st.text_input(
                "NSU Email Address",
                placeholder="yourname@mynsu.nova.edu",
                label_visibility="collapsed",
            )
            if st.button("Send Login Code", use_container_width=True, type="primary"):
                email_input = email_input.strip().lower()
                if not email_input:
                    st.error("Please enter your NSU email address.")
                elif not _is_nsu_email(email_input):
                    st.error("Please use your NSU email address (@mynsu.nova.edu or @nova.edu).")
                else:
                    try:
                        sb.auth.sign_in_with_otp({"email": email_input})
                        st.session_state.otp_sent_to = email_input
                        st.rerun()
                    except Exception as e:
                        st.error(f"Could not send code. Please try again. ({e})")

        # ── Step 2: user enters the numeric code from their email ─────────────
        else:
            sent_to = st.session_state.otp_sent_to
            st.info(f"A login code was sent to **{sent_to}**. Check your Outlook inbox and enter the code below.")
            code_input = st.text_input(
                "Login Code",
                placeholder="Enter the code from your email",
                label_visibility="collapsed",
                max_chars=8,
            )
            if st.button("Verify Code", use_container_width=True, type="primary"):
                code_input = code_input.strip()
                if not code_input:
                    st.error("Please enter the code from your email.")
                else:
                    try:
                        resp = sb.auth.verify_otp({
                            "email": sent_to,
                            "token": code_input,
                            "type": "email",
                        })
                        st.session_state.user_email = resp.user.email
                        if "otp_sent_to" in st.session_state:
                            del st.session_state.otp_sent_to
                        st.rerun()
                    except Exception as e:
                        st.error(f"Invalid or expired code. Please try again. ({e})")
            if st.button("Use a different email", use_container_width=True):
                del st.session_state.otp_sent_to
                st.rerun()

    st.stop()

# ─── Handle session-load query param (from sidebar HTML links) ───────────────────
if "load_sess" in st.query_params:
    _load_id = st.query_params.get("load_sess", "")
    st.query_params.clear()
    if _load_id:
        st.session_state.current_session_id = _load_id
        st.session_state.chat_history       = load_session(_load_id)
        st.session_state.latest_images      = []
        st.rerun()

# ─── Session State ────────────────────────────────────────────────────────────────
if "current_session_id" not in st.session_state:
    # Resume the most recent session automatically on first load
    _all = list_all_sessions()
    if _all:
        st.session_state.current_session_id = _all[-1]["id"]
    else:
        st.session_state.current_session_id = new_session_id()

if "chat_history" not in st.session_state:
    st.session_state.chat_history = load_session(st.session_state.current_session_id)

if "latest_images" not in st.session_state:
    st.session_state.latest_images = []  # images from the most recent response only
if "lang" not in st.session_state:
    st.session_state.lang = "en"         # "en" or "es"

# ─── UI Text Strings (bilingual) ─────────────────────────────────────────────────
_UI = {
    "en": {
        "page_title":       "DentAI – NSU College of Dental Medicine",
        "eyebrow":          "Powered by DentAI",
        "hero_title":       "Dent<span>AI</span> Assistant",
        "hero_sub":         "Intelligent clinical search",
        "card_title_new":   "Describe Your Patient Case",
        "card_title_followup": "Follow-up Question",
        "card_subtitle":    "Include tooth number, procedure, material, and relevant clinical details",
        "placeholder_new":  ("Example: I am preparing tooth #19 for a zirconia crown. "
                             "The patient has a large existing amalgam restoration and limited interocclusal space."),
        "placeholder_followup": "Ask a follow-up question…",
        "btn_submit":       "🔍  Get Clinical Guidance",
        "btn_new_chat":     "🔄 New Chat",
        "badge":            "Clinical Guidance",
        "response_title":   "Based on your NSU materials",
        "sources_head":     "📚 Sources pulled from your school materials",
        "img_panel":        "📸 From Your Materials",
        "shark_label":      "Searching your NSU materials",
        "warning_empty":    "⚠️  Please describe your patient case before requesting guidance.",
        "sidebar_how":      "How to Use",
        "sidebar_add":      "Add Materials",
        "how_steps": [
            ("1", "Describe your patient case with as much detail as possible"),
            ("2", "Include the tooth number — e.g. #14, #19"),
            ("3", "Specify the procedure and material — e.g. zirconia crown, Class II composite"),
            ("4", "Click <strong>Get Clinical Guidance</strong>"),
            ("5", "Review the clinical summary and cited sources"),
        ],
        "add_steps": [
            ("1", "Drop new PDFs into the <code>documents/</code> folder"),
            ("2", "Run <code>python ingest.py</code> to index them"),
            ("3", "Restart the app — new materials will be searchable"),
        ],
        "sb_footer":        ("🦈 <strong>NSU College of Dental Medicine</strong><br>"
                             "Powered by Claude AI &middot; Responses are study aids,<br>not clinical directives"),
        "lang_label":       "🌐 Language / Idioma",
        "you_asked":        "You asked",
        "lang_toggle":      "Español 🇪🇸",
    },
    "es": {
        "page_title":       "DentAI – NSU Colegio de Medicina Dental",
        "eyebrow":          "Impulsado por DentAI",
        "hero_title":       "Dent<span>AI</span> Assistant",
        "hero_sub":         "Búsqueda clínica inteligente",
        "card_title_new":   "Describe tu Caso Clínico",
        "card_title_followup": "Pregunta de Seguimiento",
        "card_subtitle":    "Incluye número de diente, procedimiento, material y detalles clínicos relevantes",
        "placeholder_new":  ("Ejemplo: Estoy preparando el diente #19 para una corona de zirconia. "
                             "El paciente tiene una restauración de amalgama grande y espacio interoclusal limitado."),
        "placeholder_followup": "Escribe una pregunta de seguimiento…",
        "btn_submit":       "🔍  Obtener Guía Clínica",
        "btn_new_chat":     "🔄 Nueva Consulta",
        "badge":            "Guía Clínica",
        "response_title":   "Basado en tus materiales de NSU",
        "sources_head":     "📚 Fuentes de tus materiales académicos",
        "img_panel":        "📸 De tus Materiales",
        "shark_label":      "Buscando en tus materiales de NSU",
        "warning_empty":    "⚠️  Por favor describe tu caso clínico antes de solicitar orientación.",
        "sidebar_how":      "Cómo Usar",
        "sidebar_add":      "Agregar Materiales",
        "how_steps": [
            ("1", "Describe tu caso clínico con el mayor detalle posible"),
            ("2", "Incluye el número de diente — ej. #14, #19"),
            ("3", "Especifica el procedimiento y material — ej. corona de zirconia, composite Clase II"),
            ("4", "Haz clic en <strong>Obtener Guía Clínica</strong>"),
            ("5", "Revisa el resumen clínico y las fuentes citadas"),
        ],
        "add_steps": [
            ("1", "Coloca nuevos PDFs en la carpeta <code>documents/</code>"),
            ("2", "Ejecuta <code>python ingest.py</code> para indexarlos"),
            ("3", "Reinicia la app — los nuevos materiales serán buscables"),
        ],
        "sb_footer":        ("🦈 <strong>NSU Colegio de Medicina Dental</strong><br>"
                             "Impulsado por Claude AI &middot; Las respuestas son ayudas de estudio,<br>no directivas clínicas"),
        "lang_label":       "🌐 Language / Idioma",
        "you_asked":        "Tú preguntaste",
        "lang_toggle":      "English 🇺🇸",
    },
}

# ─── PDF Image Extractor ──────────────────────────────────────────────────────────
DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "documents")

def extract_page_images(source_nodes, max_images=5, min_px=100):
    """
    Pull visual content from the PDF pages cited in the retrieved nodes.

    Strategy:
      1. Try extracting embedded image objects from the page (fast, high quality).
      2. If a page has no embedded objects (common in PowerPoint-exported slides),
         fall back to rendering the whole page as a PNG — this always works.

    Returns a list of dicts: {bytes, ext, caption, area}
    """
    if not PYMUPDF_OK:
        return []

    # ── Collect unique (filename → set of 0-based page indices) from top nodes ──
    seen: dict = {}
    debug_meta = []
    for node in source_nodes[:15]:
        fname = node.metadata.get("file_name", "")
        # LlamaIndex stores "page_label" (1-indexed str). Try several fallbacks.
        page_raw = (
            node.metadata.get("page_label")
            or node.metadata.get("page_number")
            or node.metadata.get("page")
            or "1"
        )
        debug_meta.append({"file": fname, "page_raw": page_raw,
                            "keys": list(node.metadata.keys())})
        if not fname.lower().endswith(".pdf"):
            continue
        try:
            page_idx = max(0, int(str(page_raw).strip()) - 1)
        except (ValueError, TypeError):
            page_idx = 0
        seen.setdefault(fname, set()).add(page_idx)

    images = []

    for fname, page_set in seen.items():
        fpath = os.path.join(DOCS_DIR, fname)
        if not os.path.exists(fpath):
            continue
        try:
            doc = fitz.open(fpath)
        except Exception:
            continue

        for page_idx in sorted(page_set):
            if page_idx >= len(doc):
                continue
            page = doc[page_idx]

            # ── Pass 1: embedded image objects ──────────────────────────────
            embedded = []
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                try:
                    base = doc.extract_image(xref)
                except Exception:
                    continue
                w, h = base["width"], base["height"]
                if w < min_px or h < min_px:
                    continue
                if max(w, h) / max(min(w, h), 1) > 8:   # skip thin decorative bars
                    continue
                embedded.append({
                    "bytes":   base["image"],
                    "ext":     base["ext"],
                    "caption": f"📄 {fname}  ·  p.{page_idx + 1}",
                    "area":    w * h,
                })

            if embedded:
                images.extend(embedded)
            else:
                # ── Pass 2: render the whole page (always works for slide PDFs) ──
                try:
                    mat = fitz.Matrix(1.5, 1.5)          # ~108 DPI — crisp but not huge
                    pix = page.get_pixmap(matrix=mat, alpha=False)
                    img_bytes = pix.tobytes("png")
                    images.append({
                        "bytes":   img_bytes,
                        "ext":     "png",
                        "caption": f"📄 {fname}  ·  p.{page_idx + 1}",
                        "area":    pix.width * pix.height,
                    })
                except Exception:
                    pass

            if len(images) >= max_images * 2:
                break

        doc.close()
        if len(images) >= max_images * 2:
            break

    images.sort(key=lambda x: x["area"], reverse=True)
    return images[:max_images], debug_meta   # return debug info alongside images

# ─── Underwater Sunrays HTML ─────────────────────────────────────────────────────
# (angle°, width px, sway duration s, sway delay s, op-lo, op-hi, pulse dur s, pulse delay s)
_RAY_PARAMS = [
    (-58, 26, 8.4, 0.0,  0.08, 0.22,  9.0, 1.2),
    (-48, 38, 7.2, 1.6,  0.12, 0.35,  7.5, 3.1),
    (-38, 34, 9.6, 0.8,  0.15, 0.42, 11.0, 0.4),
    (-28, 52, 6.9, 2.4,  0.20, 0.50,  8.0, 2.7),
    (-19, 44, 8.8, 1.2,  0.22, 0.54,  6.5, 1.8),
    (-10, 64, 7.5, 3.3,  0.26, 0.60, 10.0, 0.0),
    ( -3, 88, 6.3, 0.5,  0.30, 0.68,  7.0, 4.0),
    (  3, 88, 6.3, 2.0,  0.30, 0.68,  7.0, 0.5),
    ( 10, 64, 7.5, 2.8,  0.26, 0.60, 10.0, 2.2),
    ( 19, 44, 8.8, 1.9,  0.22, 0.54,  6.5, 3.5),
    ( 28, 52, 6.9, 0.9,  0.20, 0.50,  8.0, 1.0),
    ( 38, 34, 9.6, 2.5,  0.15, 0.42, 11.0, 2.8),
    ( 48, 38, 7.2, 3.7,  0.12, 0.35,  7.5, 0.8),
    ( 58, 26, 8.4, 1.4,  0.08, 0.22,  9.0, 3.6),
]

def make_rays_html():
    html = "<div class='rays-container'>"
    for angle, width, sdur, sdel, op_lo, op_hi, pdur, pdel in _RAY_PARAMS:
        # sway beam
        html += (
            f"<div class='ray' style='"
            f"width:{width}px;"
            f"--r:{angle}deg;"
            f"--op-lo:{op_lo};"
            f"--op-hi:{op_hi};"
            f"animation-duration:{sdur}s;"
            f"animation-delay:{sdel}s;'></div>"
        )
        # gold pulse overlay on every other ray
        if abs(angle) < 40:
            html += (
                f"<div class='ray-pulse' style='"
                f"width:{max(10, width - 20)}px;"
                f"--r:{angle}deg;"
                f"--op-lo:{op_lo * 0.6:.2f};"
                f"--op-hi:{op_hi * 0.7:.2f};"
                f"animation-duration:{pdur}s;"
                f"animation-delay:{pdel}s;'></div>"
            )
    html += "</div>"
    return html


# ─── Load NSU Seal as base64 ─────────────────────────────────────────────────────
def get_logo_tag():
    seal_path = os.path.join(os.path.dirname(__file__), "nsu_seal.png")
    if os.path.exists(seal_path):
        with open(seal_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return f"<img src='data:image/png;base64,{b64}' alt='NSU Seal' />"
    # fallback: text badge
    return "<div class='nsu-text-badge'>NSU</div>"

# ─── Page Config ────────────────────────────────────────────────────────────────
_t = _UI[st.session_state.lang]   # shortcut to current-language strings

st.set_page_config(
    page_title=_t["page_title"],
    page_icon="🦷",
    layout="wide",
    initial_sidebar_state="expanded"
)



# ─── CSS: Animations + NSU Brand ────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

/* ══════════════════════════════════════
   KEYFRAME ANIMATIONS
══════════════════════════════════════ */

@keyframes gradientShift {
    0%   { background-position: 0% 50%; }
    50%  { background-position: 100% 50%; }
    100% { background-position: 0% 50%; }
}

@keyframes float {
    0%   { transform: translateY(0px) translateX(0px); opacity: 0.15; }
    33%  { transform: translateY(-28px) translateX(12px); opacity: 0.4; }
    66%  { transform: translateY(-14px) translateX(-8px); opacity: 0.25; }
    100% { transform: translateY(0px) translateX(0px); opacity: 0.15; }
}

@keyframes floatB {
    0%   { transform: translateY(0px) translateX(0px); opacity: 0.1; }
    50%  { transform: translateY(-40px) translateX(-18px); opacity: 0.35; }
    100% { transform: translateY(0px) translateX(0px); opacity: 0.1; }
}

@keyframes floatC {
    0%   { transform: translateY(0px) scale(1); opacity: 0.2; }
    40%  { transform: translateY(-20px) scale(1.3); opacity: 0.45; }
    100% { transform: translateY(0px) scale(1); opacity: 0.2; }
}

@keyframes slideUp {
    from { opacity: 0; transform: translateY(24px); }
    to   { opacity: 1; transform: translateY(0); }
}

@keyframes fadeIn {
    from { opacity: 0; }
    to   { opacity: 1; }
}

@keyframes popIn {
    0%   { opacity: 0; transform: scale(0.8) translateY(6px); }
    70%  { transform: scale(1.04) translateY(-1px); }
    100% { opacity: 1; transform: scale(1) translateY(0); }
}

@keyframes pulseGlow {
    0%, 100% { box-shadow: 0 4px 20px rgba(0,48,135,0.35); }
    50%       { box-shadow: 0 4px 36px rgba(253,185,19,0.5), 0 0 0 4px rgba(253,185,19,0.12); }
}

@keyframes shimmer {
    0%   { background-position: -400px 0; }
    100% { background-position: 400px 0; }
}

@keyframes waveSway {
    0%, 100% { d: path("M0,20 C150,40 350,0 500,20 C650,40 850,0 1000,20 L1000,60 L0,60 Z"); }
    50%       { d: path("M0,30 C120,10 380,50 500,30 C620,10 880,50 1000,30 L1000,60 L0,60 Z"); }
}

@keyframes rotateSlow {
    from { transform: rotate(0deg); }
    to   { transform: rotate(360deg); }
}

@keyframes borderPulse {
    0%, 100% { border-color: rgba(0,48,135,0.15); }
    50%       { border-color: rgba(253,185,19,0.5); }
}

/* ══════════════════════════════════════
   GLOBAL
══════════════════════════════════════ */

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif !important;
}

.stApp {
    background: #eef1f8;
}

/* Content column sits on a clean light background */
.block-container {
    padding-top: 0 !important;
    padding-bottom: 40px !important;
    max-width: 920px !important;
    background: transparent;
}

/* ── Ensure all markdown text is clearly dark on the light bg ── */
/* Exclude elements inside the hero (.hero-wrapper) via :not scoping isn't possible,
   so we override hero text with !important in their own rules above.            */
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li,
[data-testid="stMarkdownContainer"] td,
[data-testid="stMarkdownContainer"] th {
    color: #1e293b !important;
    line-height: 1.75 !important;
}
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3,
[data-testid="stMarkdownContainer"] h4 {
    color: #003087 !important;
    font-weight: 700 !important;
}
[data-testid="stMarkdownContainer"] strong {
    color: #003087 !important;
    font-weight: 600 !important;
}
[data-testid="stMarkdownContainer"] em {
    color: #374151 !important;
}
[data-testid="stMarkdownContainer"] code {
    background: #e8edf8 !important;
    color: #003087 !important;
    border-radius: 4px !important;
    padding: 1px 5px !important;
    font-size: 0.88em !important;
}
[data-testid="stMarkdownContainer"] a {
    color: #003087 !important;
}
[data-testid="stMarkdownContainer"] blockquote {
    border-left: 3px solid #FDB913 !important;
    padding-left: 12px !important;
    color: #374151 !important;
}

/* ── Force hero text to always be visible on dark background ── */
.hero-wrapper * { color: inherit; }
.hero-wrapper p { color: rgba(255,255,255,0.92) !important; }
.hero-wrapper span { color: rgba(255,255,255,0.92) !important; }
.hero-wrapper .hero-title { color: #ffffff !important; }
.hero-wrapper .hero-title span { color: #FDB913 !important; }
.hero-wrapper .hero-sub { color: rgba(255,255,255,0.92) !important; }
.hero-wrapper .hero-eyebrow-label { color: #FDB913 !important; }
.hero-wrapper .stat-num { color: #FDB913 !important; }
.hero-wrapper .stat-label { color: rgba(255,255,255,0.85) !important; }

/* ── Force white text inside dark blue chat bubble ── */
.chat-user-bubble p,
.chat-user-bubble span,
.chat-user-bubble .chat-user-text,
[data-testid="stMarkdownContainer"] .chat-user-bubble p,
[data-testid="stMarkdownContainer"] .chat-user-bubble span,
[data-testid="stMarkdownContainer"] .chat-user-text {
    color: #ffffff !important;
}
[data-testid="stMarkdownContainer"] .chat-user-label {
    color: #FDB913 !important;
}

/* ── Force white text inside sidebar steps ── */
[data-testid="stMarkdownContainer"] .sb-text,
[data-testid="stMarkdownContainer"] .sb-text strong,
[data-testid="stMarkdownContainer"] .sb-text code,
section[data-testid="stSidebar"] .sb-text,
section[data-testid="stSidebar"] .sb-text strong {
    color: rgba(255,255,255,0.92) !important;
}
section[data-testid="stSidebar"] .sb-text code {
    color: #FDB913 !important;
    background: rgba(255,255,255,0.12) !important;
}

#MainMenu { visibility: hidden; }
footer    { visibility: hidden; }
header    { visibility: hidden; }

/* ── Sidebar collapse button (visible inside sidebar) ── */
[data-testid="stSidebarCollapseButton"] button {
    background: rgba(253,185,19,0.15) !important;
    border: 1px solid rgba(253,185,19,0.4) !important;
    border-radius: 8px !important;
    color: #FDB913 !important;
    opacity: 1 !important;
    visibility: visible !important;
}
[data-testid="stSidebarCollapseButton"] button:hover {
    background: rgba(253,185,19,0.3) !important;
}
[data-testid="stSidebarCollapseButton"] button svg {
    fill: #FDB913 !important;
    stroke: #FDB913 !important;
}

/* ── Expand button (shown on left edge when sidebar is collapsed) ── */
[data-testid="collapsedControl"] {
    display: flex !important;
    visibility: visible !important;
    opacity: 1 !important;
    position: fixed !important;
    top: 50% !important;
    left: 0 !important;
    z-index: 9999 !important;
}
[data-testid="collapsedControl"] button {
    display: flex !important;
    visibility: visible !important;
    opacity: 1 !important;
    background: #003087 !important;
    border: 2px solid #FDB913 !important;
    border-left: none !important;
    border-radius: 0 10px 10px 0 !important;
    color: #FDB913 !important;
    padding: 12px 8px !important;
    box-shadow: 2px 2px 12px rgba(0,0,0,0.25) !important;
}
[data-testid="collapsedControl"] button:hover {
    background: #0041b3 !important;
    border-color: #FDB913 !important;
}
[data-testid="collapsedControl"] button svg {
    fill: #FDB913 !important;
    stroke: #FDB913 !important;
    width: 18px !important;
    height: 18px !important;
}

/* ══════════════════════════════════════
   HERO SECTION
══════════════════════════════════════ */

.hero-wrapper {
    position: relative;
    overflow: hidden;
    border-radius: 0 0 28px 28px;
    background: linear-gradient(135deg, #00155a, #003087, #00205b, #001240);
    background-size: 400% 400%;
    animation: gradientShift 10s ease infinite;
    padding: 44px 48px 56px;
    margin: -28px -4rem 32px -4rem;
}

/* floating orbs */
.orb {
    position: absolute;
    border-radius: 50%;
    background: rgba(253,185,19,0.18);
    filter: blur(1px);
    animation: float 7s ease-in-out infinite;
    pointer-events: none;
}
.orb-1  { width:14px; height:14px; top:18%; left:8%;  animation-delay: 0s;   animation-duration: 8s; }
.orb-2  { width:8px;  height:8px;  top:60%; left:15%; animation-delay: 1.2s; animation-duration: 6s; animation-name: floatB; }
.orb-3  { width:20px; height:20px; top:30%; left:78%; animation-delay: 0.5s; animation-duration: 9s; animation-name: floatC; background: rgba(255,255,255,0.12); }
.orb-4  { width:10px; height:10px; top:72%; left:68%; animation-delay: 2s;   animation-duration: 7s; }
.orb-5  { width:6px;  height:6px;  top:12%; left:55%; animation-delay: 3s;   animation-duration: 5s; animation-name: floatB; }
.orb-6  { width:16px; height:16px; top:80%; left:88%; animation-delay: 0.8s; animation-duration: 8s; animation-name: floatC; }
.orb-7  { width:7px;  height:7px;  top:50%; left:92%; animation-delay: 1.8s; animation-duration: 6s; }
.orb-8  { width:12px; height:12px; top:22%; left:38%; animation-delay: 4s;   animation-duration: 9s; animation-name: floatB; background: rgba(255,255,255,0.08); }
.orb-9  { width:5px;  height:5px;  top:88%; left:30%; animation-delay: 2.5s; animation-duration: 7s; }
.orb-10 { width:18px; height:18px; top:45%; left:48%; animation-delay: 3.5s; animation-duration: 10s; animation-name: floatC; background: rgba(253,185,19,0.10); }

/* rotating ring accent */
.ring-accent {
    position: absolute;
    width: 220px;
    height: 220px;
    border-radius: 50%;
    border: 1px solid rgba(253,185,19,0.12);
    top: -60px;
    right: -60px;
    animation: rotateSlow 25s linear infinite;
    pointer-events: none;
}
.ring-accent-inner {
    position: absolute;
    width: 140px;
    height: 140px;
    border-radius: 50%;
    border: 1px solid rgba(255,255,255,0.06);
    top: -20px;
    right: -20px;
    animation: rotateSlow 18s linear infinite reverse;
    pointer-events: none;
}

/* wave bottom */
.hero-wave {
    position: absolute;
    bottom: 0;
    left: 0;
    width: 100%;
    line-height: 0;
    pointer-events: none;
}

/* hero content */
.hero-content {
    position: relative;
    z-index: 2;
    display: flex;
    align-items: center;
    gap: 28px;
}

.hero-logo-wrap {
    flex-shrink: 0;
}
.hero-logo-wrap img {
    height: 88px;
    width: 88px;
    border-radius: 50%;
    object-fit: cover;
    border: 2px solid rgba(253,185,19,0.45);
    box-shadow: 0 0 0 4px rgba(253,185,19,0.12), 0 4px 20px rgba(0,0,0,0.4);
    transition: transform 0.3s ease, box-shadow 0.3s ease;
}
.hero-logo-wrap img:hover {
    transform: scale(1.06);
    box-shadow: 0 0 0 6px rgba(253,185,19,0.25), 0 6px 28px rgba(0,0,0,0.5);
}
.nsu-text-badge {
    width: 88px;
    height: 88px;
    border-radius: 50%;
    background: rgba(253,185,19,0.15);
    border: 2px solid rgba(253,185,19,0.45);
    display: flex;
    align-items: center;
    justify-content: center;
    color: #FDB913;
    font-size: 1.6rem;
    font-weight: 800;
    letter-spacing: -1px;
}

.hero-divider {
    width: 2px;
    height: 64px;
    background: linear-gradient(to bottom, transparent, rgba(253,185,19,0.6), transparent);
    flex-shrink: 0;
}

.hero-text { flex: 1; }

.hero-eyebrow {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: rgba(253,185,19,0.15);
    border: 1px solid rgba(253,185,19,0.3);
    border-radius: 20px;
    padding: 4px 12px;
    margin-bottom: 12px;
}
.hero-eyebrow-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #FDB913;
    animation: pulseGlow 2s ease-in-out infinite;
}
.hero-eyebrow-label {
    color: #FDB913 !important;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 1px;
    text-transform: uppercase;
}

.hero-title {
    color: #ffffff !important;
    font-size: 2.2rem;
    font-weight: 800;
    margin: 0 0 6px 0;
    line-height: 1.1;
    letter-spacing: -0.5px;
    text-shadow: 0 2px 20px rgba(0,0,0,0.3);
}
.hero-title span {
    color: #FDB913 !important;
}

.hero-sub {
    color: rgba(255,255,255,0.92) !important;
    font-size: 0.95rem;
    font-weight: 500;
    margin: 0;
    letter-spacing: 0.2px;
    text-shadow: 0 1px 8px rgba(0,0,0,0.4);
}

.hero-stats {
    display: flex;
    flex-direction: column;
    gap: 10px;
    margin-left: auto;
    flex-shrink: 0;
}
.stat-chip {
    background: rgba(255,255,255,0.07);
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 10px;
    padding: 10px 18px;
    text-align: center;
    backdrop-filter: blur(4px);
    transition: background 0.2s, border-color 0.2s;
    cursor: default;
}
.stat-chip:hover {
    background: rgba(255,255,255,0.12);
    border-color: rgba(253,185,19,0.35);
}
.stat-num {
    color: #FDB913 !important;
    font-size: 1.3rem;
    font-weight: 700;
    display: block;
    line-height: 1;
}
.stat-label {
    color: rgba(255,255,255,0.85) !important;
    font-size: 0.68rem;
    font-weight: 500;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    display: block;
    margin-top: 3px;
}

/* ══════════════════════════════════════
   MAIN CARDS
══════════════════════════════════════ */

.main-card {
    background: #ffffff;
    border-radius: 16px;
    padding: 28px 32px;
    margin-bottom: 20px;
    border: 1px solid rgba(0,48,135,0.10);
    box-shadow: 0 4px 24px rgba(0,0,0,0.15);
    animation: slideUp 0.5s ease both;
}
.main-card:nth-child(2) { animation-delay: 0.1s; }
.main-card:nth-child(3) { animation-delay: 0.2s; }

.card-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 18px;
    padding-bottom: 14px;
    border-bottom: 1px solid #eef1f8;
}
.card-icon-wrap {
    width: 38px;
    height: 38px;
    border-radius: 10px;
    background: linear-gradient(135deg, #003087, #004db3);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 18px;
    flex-shrink: 0;
    box-shadow: 0 2px 8px rgba(0,48,135,0.3);
}
.card-title {
    color: #003087;
    font-size: 1rem;
    font-weight: 700;
    margin: 0;
    letter-spacing: -0.2px;
}
.card-subtitle {
    color: #6b7280;
    font-size: 0.8rem;
    margin: 2px 0 0 0;
}

/* ══════════════════════════════════════
   TEXTAREA OVERRIDE
══════════════════════════════════════ */

.stTextArea textarea {
    border: 2px solid #e2e8f0 !important;
    border-radius: 12px !important;
    font-family: 'Inter', sans-serif !important;
    font-size: 0.93rem !important;
    color: #1e293b !important;
    background: #f8faff !important;
    padding: 16px !important;
    transition: border-color 0.25s, box-shadow 0.25s !important;
    resize: vertical !important;
}
.stTextArea textarea:focus {
    border-color: #003087 !important;
    box-shadow: 0 0 0 4px rgba(0,48,135,0.08) !important;
    background: #ffffff !important;
}
.stTextArea textarea::placeholder {
    color: #94a3b8 !important;
}
.stTextArea label { display: none !important; }

/* ══════════════════════════════════════
   CTA BUTTON
══════════════════════════════════════ */

/* Primary submit button */
.stButton > button[kind="primary"],
div[data-testid="column"] .stButton > button:not(.new-chat-btn) {
    background: linear-gradient(135deg, #003087 0%, #0041b3 100%) !important;
    color: #ffffff !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 700 !important;
    font-size: 0.95rem !important;
    letter-spacing: 0.4px !important;
    border: none !important;
    border-radius: 12px !important;
    padding: 14px 40px !important;
    width: 100% !important;
    transition: transform 0.18s ease, box-shadow 0.18s ease !important;
    animation: pulseGlow 3s ease-in-out infinite !important;
    cursor: pointer !important;
}
.stButton > button p {
    color: #ffffff !important;
    font-weight: 700 !important;
}
.stButton > button:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 8px 28px rgba(0,48,135,0.45) !important;
    animation: none !important;
}
.stButton > button:active {
    transform: translateY(0) !important;
}

/* New Chat secondary button — small & compact */
.new-chat-col .stButton > button {
    background: rgba(0,48,135,0.08) !important;
    color: #003087 !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 600 !important;
    font-size: 0.8rem !important;
    letter-spacing: 0.3px !important;
    border: 1px solid rgba(0,48,135,0.22) !important;
    border-radius: 20px !important;
    padding: 6px 16px !important;
    width: auto !important;
    min-width: unset !important;
    animation: none !important;
    transition: background 0.18s, border-color 0.18s !important;
}
.new-chat-col .stButton > button p {
    color: #003087 !important;
    font-weight: 600 !important;
    font-size: 0.8rem !important;
}
.new-chat-col .stButton > button:hover {
    background: rgba(0,48,135,0.14) !important;
    border-color: rgba(0,48,135,0.4) !important;
    transform: none !important;
    box-shadow: none !important;
}

/* ══════════════════════════════════════
   RESPONSE AREA
══════════════════════════════════════ */

.response-wrapper {
    animation: fadeIn 0.6s ease both;
}

.response-card {
    background: #ffffff;
    border-radius: 16px;
    border-left: 5px solid #003087;
    padding: 28px 32px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.12);
    margin-bottom: 18px;
    animation: slideUp 0.45s ease both;
    border-top: 1px solid rgba(0,48,135,0.08);
    border-right: 1px solid rgba(0,48,135,0.08);
    border-bottom: 1px solid rgba(0,48,135,0.08);
}

.response-header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 20px;
    padding-bottom: 16px;
    border-bottom: 1px solid #eef1f8;
}
.response-badge {
    background: linear-gradient(135deg, #003087, #004db3);
    color: #FDB913;
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.6px;
    text-transform: uppercase;
}
.response-title {
    color: #003087;
    font-size: 1.05rem;
    font-weight: 700;
    margin: 0;
}

/* response markdown content */
.response-card p, .response-card li {
    color: #1e293b !important;
    line-height: 1.75 !important;
}
.response-card h2, .response-card h3 {
    color: #003087 !important;
    font-weight: 700 !important;
}
.response-card strong {
    color: #003087 !important;
}
.response-card code {
    background: #eef2ff !important;
    color: #003087 !important;
    border-radius: 4px !important;
    padding: 1px 5px !important;
}

/* ══════════════════════════════════════
   SOURCES
══════════════════════════════════════ */

.sources-wrapper {
    background: linear-gradient(135deg, #eef2ff 0%, #e8edff 100%);
    border-radius: 14px;
    padding: 20px 24px;
    border: 1px solid rgba(0,48,135,0.13);
    margin-bottom: 20px;
    animation: slideUp 0.55s ease both;
    animation-delay: 0.1s;
}
.sources-head {
    color: #003087;
    font-size: 0.88rem;
    font-weight: 700;
    margin: 0 0 14px 0;
    display: flex;
    align-items: center;
    gap: 6px;
    letter-spacing: 0.2px;
}
.pill-row { display: flex; flex-wrap: wrap; gap: 6px; }
.source-pill {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    background: #ffffff;
    border: 1px solid rgba(0,48,135,0.18);
    color: #003087;
    border-radius: 20px;
    padding: 5px 14px;
    font-size: 0.78rem;
    font-weight: 500;
    animation: popIn 0.35s ease both;
    transition: background 0.2s, transform 0.15s;
    cursor: default;
}
.source-pill:hover {
    background: #003087;
    color: #FDB913;
    border-color: #003087;
    transform: translateY(-1px);
}

/* ══════════════════════════════════════
   SHARK LOADER
══════════════════════════════════════ */

@keyframes sharkSwim {
    0%   { left: 3%;  transform: translateY(-50%) scaleX(1); }
    46%  { left: 83%; transform: translateY(-50%) scaleX(1); }
    50%  { left: 83%; transform: translateY(-50%) scaleX(-1); }
    96%  { left: 3%;  transform: translateY(-50%) scaleX(-1); }
    100% { left: 3%;  transform: translateY(-50%) scaleX(1); }
}
@keyframes bubbleFloat {
    0%   { bottom: 4px; opacity: 0.55; transform: translateX(0px); }
    100% { bottom: 56px; opacity: 0;   transform: translateX(6px); }
}
@keyframes dotPulse {
    0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
    40%            { transform: scale(1.0); opacity: 1.0; }
}

.shark-loader {
    background: #ffffff;
    border-radius: 16px;
    padding: 28px 28px 24px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.09);
    border: 1px solid rgba(0,48,135,0.10);
    margin: 20px 0;
}
.shark-ocean {
    background: linear-gradient(135deg, #00205b 0%, #003087 50%, #0041b3 100%);
    border-radius: 10px;
    height: 62px;
    position: relative;
    overflow: hidden;
    margin-bottom: 16px;
}
.shark-emoji {
    position: absolute;
    top: 50%;
    font-size: 2rem;
    line-height: 1;
    animation: sharkSwim 2.8s ease-in-out infinite;
    user-select: none;
}
.bubble {
    position: absolute;
    border-radius: 50%;
    background: rgba(255,255,255,0.25);
    animation: bubbleFloat linear infinite;
}
.shark-label {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    color: #003087;
    font-size: 0.95rem;
    font-weight: 600;
    letter-spacing: 0.2px;
}
.dot-row { display: inline-flex; gap: 4px; align-items: center; }
.dot {
    width: 5px; height: 5px;
    border-radius: 50%;
    background: #FDB913;
    animation: dotPulse 1.4s ease-in-out infinite both;
}
.dot:nth-child(1) { animation-delay: 0s; }
.dot:nth-child(2) { animation-delay: 0.2s; }
.dot:nth-child(3) { animation-delay: 0.4s; }

/* ══════════════════════════════════════
   WARNING
══════════════════════════════════════ */

.stAlert {
    border-radius: 12px !important;
    background: #fffbeb !important;
    border: 1px solid rgba(253,185,19,0.4) !important;
    border-left: 4px solid #FDB913 !important;
}

/* ══════════════════════════════════════
   SIDEBAR
══════════════════════════════════════ */

section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #00155a 0%, #003087 60%, #00205b 100%) !important;
    border-right: 1px solid rgba(253,185,19,0.15) !important;
}

/* all sidebar text white */
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] div,
section[data-testid="stSidebar"] li,
section[data-testid="stSidebar"] label {
    color: #ffffff !important;
}

section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {
    color: #FDB913 !important;
    font-weight: 700 !important;
}

section[data-testid="stSidebar"] hr {
    border-color: rgba(255,255,255,0.12) !important;
    margin: 18px 0 !important;
}

section[data-testid="stSidebar"] code {
    background: rgba(255,255,255,0.12) !important;
    color: #FDB913 !important;
    border-radius: 4px !important;
    padding: 1px 6px !important;
    font-size: 0.8rem !important;
}

.sb-step {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px;
    padding: 11px 14px;
    margin-bottom: 8px;
    transition: background 0.2s, border-color 0.2s;
}
.sb-step:hover {
    background: rgba(255,255,255,0.11);
    border-color: rgba(253,185,19,0.25);
}
.sb-num {
    background: #FDB913;
    color: #003087;
    border-radius: 50%;
    min-width: 22px;
    height: 22px;
    font-size: 0.72rem;
    font-weight: 800;
    display: flex;
    align-items: center;
    justify-content: center;
    margin-top: 1px;
}
.sb-text {
    font-size: 0.86rem;
    line-height: 1.45;
    color: rgba(255,255,255,0.92) !important;
    font-weight: 400;
}

.sb-footer {
    background: rgba(253,185,19,0.10);
    border: 1px solid rgba(253,185,19,0.22);
    border-radius: 10px;
    padding: 14px 16px;
    margin-top: 16px;
    text-align: center;
}
.sb-footer p {
    color: rgba(255,255,255,0.75) !important;
    font-size: 0.78rem !important;
    margin: 0 !important;
    line-height: 1.5 !important;
}
.sb-footer strong {
    color: #FDB913 !important;
}

/* ══════════════════════════════════════
   CHAT INPUT (native st.chat_input)
══════════════════════════════════════ */

/* Fixed bottom bar that Streamlit creates for chat_input */
[data-testid="stBottom"] > div {
    background: #eef1f8 !important;
    border-top: 1px solid rgba(0,48,135,0.09) !important;
    padding: 14px 0 10px !important;
}

/* The chat input box itself */
[data-testid="stChatInput"] {
    border-radius: 24px !important;
    border: 2px solid rgba(0,48,135,0.18) !important;
    background: #ffffff !important;
    box-shadow: 0 4px 20px rgba(0,48,135,0.10) !important;
    transition: border-color 0.25s, box-shadow 0.25s !important;
}
[data-testid="stChatInput"]:focus-within {
    border-color: #003087 !important;
    box-shadow: 0 4px 24px rgba(0,48,135,0.18) !important;
}
[data-testid="stChatInput"] textarea {
    font-family: 'Inter', sans-serif !important;
    font-size: 0.95rem !important;
    color: #1e293b !important;
    background: transparent !important;
}
[data-testid="stChatInput"] textarea::placeholder {
    color: #94a3b8 !important;
}
/* Send button inside chat_input — vertically centered */
[data-testid="stChatInput"] button {
    background: linear-gradient(135deg, #003087, #0041b3) !important;
    border-radius: 50% !important;
    color: #ffffff !important;
    border: none !important;
    align-self: center !important;
    margin-top: auto !important;
    margin-bottom: auto !important;
    position: relative !important;
    top: 0 !important;
    transform: translateY(0) !important;
}
[data-testid="stChatInput"] > div {
    align-items: center !important;
    display: flex !important;
}
[data-testid="stChatInput"] button:hover {
    background: linear-gradient(135deg, #0041b3, #0050d8) !important;
    transform: scale(1.05) !important;
}

/* Extra bottom padding so last message isn't hidden behind the input bar */
.block-container {
    padding-bottom: 120px !important;
}

/* ══════════════════════════════════════
   CONVERSATION HISTORY
══════════════════════════════════════ */

.chat-history-wrap {
    margin-bottom: 8px;
}
.chat-user-bubble {
    background: linear-gradient(135deg, #003087, #0041b3);
    border-radius: 12px 12px 12px 4px;
    padding: 14px 18px;
    margin: 18px 0 8px;
    display: inline-block;
    max-width: 85%;
    box-shadow: 0 2px 10px rgba(0,48,135,0.2);
    animation: slideUp 0.3s ease both;
}
.chat-user-label {
    font-size: 0.68rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: #FDB913 !important;
    margin: 0 0 5px 0;
    display: block;
}
.chat-user-text {
    color: #ffffff !important;
    font-size: 0.93rem;
    line-height: 1.55;
    margin: 0;
}
.chat-exchange-divider {
    border: none;
    border-top: 1px solid rgba(0,48,135,0.10);
    margin: 20px 0 8px;
}
.new-convo-btn-wrap {
    display: flex;
    justify-content: flex-end;
    margin-bottom: 6px;
}

/* ── Past session list items — minimal plain text style ── */
.sess-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 4px;
    border-radius: 6px;
    background: rgba(253,185,19,0.10);
}
.sess-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: #FDB913;
    flex-shrink: 0;
}
.sess-title {
    color: #FDB913 !important;
    font-size: 0.82rem;
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

/* Inactive session row — pure HTML div, no Streamlit button */
.sess-row {
    cursor: pointer;
    padding: 5px 8px;
    border-radius: 4px;
    color: rgba(255,255,255,0.82);
    font-size: 0.82rem;
    font-weight: 400;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    line-height: 1.4;
    transition: background 0.15s;
    user-select: none;
}
.sess-row:hover {
    background: rgba(255,255,255,0.08);
    color: #ffffff;
}
.sess-label {
    pointer-events: none;
}

/* Popover dropdown panel */
[data-testid="stPopoverBody"],
[data-testid="stPopoverBody"] > div,
[data-testid="stPopoverBody"] .stVerticalBlock {
    background: #0f1f45 !important;
    border: 1px solid rgba(253,185,19,0.35) !important;
    border-radius: 10px !important;
}
[data-testid="stPopoverBody"] .stButton > button,
[data-testid="stPopoverBody"] button {
    background: #162150 !important;
    color: #ffffff !important;
    border: 1px solid rgba(253,185,19,0.25) !important;
    border-radius: 6px !important;
    text-align: left !important;
    animation: none !important;
    box-shadow: none !important;
    font-weight: 500 !important;
}
[data-testid="stPopoverBody"] .stButton > button:hover,
[data-testid="stPopoverBody"] button:hover {
    background: rgba(253,185,19,0.2) !important;
    border-color: rgba(253,185,19,0.6) !important;
    color: #FDB913 !important;
    transform: none !important;
    box-shadow: none !important;
}

/* ⋮ popover trigger button in sidebar */
section[data-testid="stSidebar"] [data-testid="stPopover"] button {
    background: rgba(255,255,255,0.08) !important;
    border: 1px solid rgba(253,185,19,0.3) !important;
    border-radius: 6px !important;
    color: #FDB913 !important;
    font-size: 1rem !important;
    font-weight: 700 !important;
    padding: 2px 6px !important;
    min-height: 28px !important;
    height: 28px !important;
    box-shadow: none !important;
    animation: none !important;
}
section[data-testid="stSidebar"] [data-testid="stPopover"] button:hover {
    background: rgba(253,185,19,0.15) !important;
    border-color: rgba(253,185,19,0.6) !important;
    transform: none !important;
    box-shadow: none !important;
}

/* Edit + Delete buttons — small ghost (high-specificity to override sidebar globals) */
section[data-testid="stSidebar"] .sess-edit-wrap .stButton > button,
section[data-testid="stSidebar"] .sess-del-wrap .stButton > button {
    background: transparent !important;
    border: 1px solid rgba(255,255,255,0.18) !important;
    border-radius: 6px !important;
    padding: 2px 5px !important;
    font-size: 0.75rem !important;
    min-height: 26px !important;
    height: 26px !important;
    width: 26px !important;
    line-height: 1 !important;
    animation: none !important;
    color: rgba(255,255,255,0.45) !important;
    box-shadow: none !important;
    margin: 0 !important;
}
section[data-testid="stSidebar"] .sess-edit-wrap .stButton > button:hover {
    background: rgba(253,185,19,0.15) !important;
    border-color: rgba(253,185,19,0.5) !important;
    color: #FDB913 !important;
    transform: none !important;
    box-shadow: none !important;
}
section[data-testid="stSidebar"] .sess-del-wrap .stButton > button:hover {
    background: rgba(220,50,50,0.15) !important;
    border-color: rgba(220,50,50,0.4) !important;
    color: #ff6b6b !important;
    transform: none !important;
    box-shadow: none !important;
}

/* ══════════════════════════════════════
   IMAGE SIDE PANEL
══════════════════════════════════════ */

.img-panel-header {
    display: flex;
    align-items: center;
    gap: 8px;
    background: linear-gradient(135deg, #003087, #004db3);
    color: #FDB913;
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.6px;
    text-transform: uppercase;
    border-radius: 10px 10px 0 0;
    padding: 10px 14px;
    margin-bottom: 0;
}

.img-panel-body {
    background: #f0f4ff;
    border: 1px solid rgba(0,48,135,0.12);
    border-top: none;
    border-radius: 0 0 10px 10px;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 12px;
}

.img-card {
    background: #ffffff;
    border-radius: 8px;
    overflow: hidden;
    border: 1px solid rgba(0,48,135,0.10);
    box-shadow: 0 2px 8px rgba(0,0,0,0.07);
    animation: slideUp 0.4s ease both;
}

.img-caption {
    font-size: 0.68rem;
    color: #6b7280;
    padding: 6px 10px 8px;
    line-height: 1.3;
    border-top: 1px solid #eef1f8;
    word-break: break-word;
}

.img-panel-empty {
    background: #f8faff;
    border: 1px dashed rgba(0,48,135,0.18);
    border-radius: 10px;
    padding: 20px 14px;
    text-align: center;
    color: #94a3b8;
    font-size: 0.8rem;
}

/* ══════════════════════════════════════
   LANGUAGE TOGGLE
══════════════════════════════════════ */

/* All buttons inside the sidebar get the gold pill style */
section[data-testid="stSidebar"] .stButton > button {
    background: rgba(253,185,19,0.15) !important;
    color: #FDB913 !important;
    border: 1px solid rgba(253,185,19,0.45) !important;
    border-radius: 20px !important;
    padding: 7px 20px !important;
    font-size: 0.85rem !important;
    font-weight: 700 !important;
    letter-spacing: 0.3px !important;
    width: auto !important;
    min-width: unset !important;
    animation: none !important;
    transition: background 0.18s, border-color 0.18s !important;
    box-shadow: none !important;
}
section[data-testid="stSidebar"] .stButton > button p,
section[data-testid="stSidebar"] .stButton > button span {
    color: #FDB913 !important;
    font-weight: 700 !important;
    font-size: 0.85rem !important;
}
section[data-testid="stSidebar"] .stButton > button:hover {
    background: rgba(253,185,19,0.28) !important;
    border-color: rgba(253,185,19,0.7) !important;
    transform: none !important;
    box-shadow: none !important;
}
.lang-label {
    color: rgba(255,255,255,0.7) !important;
    font-size: 0.75rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.5px !important;
    text-transform: uppercase !important;
    margin-bottom: 6px !important;
    display: block !important;
}

/* ══════════════════════════════════════
   UNDERWATER SUNRAYS
══════════════════════════════════════ */

@keyframes raySway {
    0%   { transform: translateX(-50%) rotate(calc(var(--r) - 3deg)); opacity: var(--op-lo); }
    25%  { opacity: var(--op-hi); }
    50%  { transform: translateX(-50%) rotate(calc(var(--r) + 3deg)); opacity: var(--op-lo); }
    75%  { opacity: var(--op-hi); }
    100% { transform: translateX(-50%) rotate(calc(var(--r) - 3deg)); opacity: var(--op-lo); }
}

@keyframes rayPulse {
    0%, 100% { opacity: var(--op-lo); }
    30%       { opacity: var(--op-hi); }
    60%       { opacity: calc(var(--op-lo) * 1.4); }
}

.rays-container {
    position: absolute;
    inset: 0;
    overflow: hidden;
    pointer-events: none;
    z-index: 1;
}

.ray {
    position: absolute;
    top: -20px;
    left: 50%;
    height: 150%;
    transform-origin: top center;
    transform: translateX(-50%) rotate(var(--r));
    background: linear-gradient(
        to bottom,
        rgba(180, 215, 255, 0.00) 0%,
        rgba(200, 225, 255, 0.22) 3%,
        rgba(253, 185,  19, 0.10) 18%,
        rgba(180, 215, 255, 0.07) 42%,
        transparent 78%
    );
    border-radius: 0 0 60% 60%;
    animation: raySway ease-in-out infinite;
}

.ray-pulse {
    position: absolute;
    top: -20px;
    left: 50%;
    height: 130%;
    transform-origin: top center;
    transform: translateX(-50%) rotate(var(--r));
    background: linear-gradient(
        to bottom,
        rgba(253, 185, 19, 0.00) 0%,
        rgba(253, 185, 19, 0.14) 5%,
        rgba(253, 185, 19, 0.04) 30%,
        transparent 65%
    );
    border-radius: 0 0 50% 50%;
    animation: rayPulse ease-in-out infinite;
}
</style>
""", unsafe_allow_html=True)


# ─── Cloud retrieval: lightweight Pinecone + OpenAI wrapper ──────────────────
# Matches the llama-index node interface (node.text, node.metadata) so the
# rest of app.py needs zero changes.

class _PineconeNode:
    """Minimal stand-in for a llama-index NodeWithScore."""
    def __init__(self, text: str, metadata: dict):
        self.text     = text
        self.metadata = metadata

class _PineconeRetriever:
    def __init__(self, pinecone_index, top_k: int = 20):
        self._index = pinecone_index
        self._top_k = top_k

    def retrieve(self, query: str) -> list:
        import openai
        oai = openai.OpenAI()
        embedding = oai.embeddings.create(
            input=query,
            model="text-embedding-ada-002",
        ).data[0].embedding
        results = self._index.query(
            vector=embedding,
            top_k=self._top_k,
            include_metadata=True,
        )
        return [
            _PineconeNode(
                text     = m.metadata.get("text", ""),
                metadata = m.metadata,
            )
            for m in results.matches
        ]

class _PineconeIndex:
    def __init__(self, pinecone_index):
        self._index = pinecone_index

    def as_retriever(self, similarity_top_k: int = 20) -> _PineconeRetriever:
        return _PineconeRetriever(self._index, top_k=similarity_top_k)


# ─── Load Index ─────────────────────────────────────────────────────────────────
@st.cache_resource
def load_index():
    if IS_LOCAL:
        # ── Local: ChromaDB on disk via llama-index ──────────────────────────
        chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
        chroma_collection = chroma_client.get_or_create_collection(CHROMA_COLLECTION)
        vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        return VectorStoreIndex.from_vector_store(
            vector_store,
            storage_context=storage_context,
        )
    else:
        # ── Cloud: Pinecone directly (no llama-index needed) ─────────────────
        from config import PINECONE_API_KEY, PINECONE_INDEX
        pc = PineconeClient(api_key=PINECONE_API_KEY)
        return _PineconeIndex(pc.Index(PINECONE_INDEX))


# ─── Anthropic Client + System Prompt ───────────────────────────────────────────
anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ─── Hybrid Model Routing ────────────────────────────────────────────────────────
# Simple questions → Haiku (~$0.004/query, fast)
# Complex clinical reasoning → Sonnet (~$0.018/query, higher quality)
# Override both via config.py / .env if needed.

_COMPLEX_KEYWORDS = {
    # Diagnosis & reasoning
    "differential diagnosis", "differential dx", "ddx",
    "diagnosis", "diagnose",
    # Treatment
    "treatment plan", "treatment planning",
    "management", "prognosis",
    # Pharmacology
    "drug interaction", "drug-interaction",
    "pharmacology", "pharmacokinetics", "pharmacodynamics",
    "contraindication", "contraindicated",
    "medication", "prescribe", "prescription",
    "antibiotic", "analgesic", "sedation", "anesthesia",
    "nsaid", "opioid",
    # Systemic / medical
    "systemic", "medical history", "medical condition",
    "complication", "adverse", "risk factor",
    "hypertension", "diabetes", "anticoagulant", "warfarin",
    "blood pressure", "cardiac", "heart",
    # Surgical / endo / perio
    "surgical", "surgery", "extraction", "implant",
    "endodontic", "root canal", "perforation",
    "periodontal", "bone loss", "furcation",
    # Clinical reasoning phrases
    "why", "explain", "compare", "difference between",
    "when to", "should i", "is it safe",
}

def route_model(question: str) -> str:
    """
    Return the Claude model best suited for this question.

    If CLAUDE_MODEL is set in .env, that always wins (manual override).
    Otherwise: any complex-keyword match → Sonnet; everything else → Haiku.
    """
    from config import CLAUDE_MODEL, CLAUDE_MODEL_SIMPLE, CLAUDE_MODEL_COMPLEX

    # Manual override takes priority
    if CLAUDE_MODEL:
        return CLAUDE_MODEL

    q_lower = question.lower()
    for kw in _COMPLEX_KEYWORDS:
        if kw in q_lower:
            print(f"[DentAI] routing → Sonnet  (matched: '{kw}')")
            return CLAUDE_MODEL_COMPLEX

    print("[DentAI] routing → Haiku  (simple query)")
    return CLAUDE_MODEL_SIMPLE

_LANG_INSTRUCTION = {
    "en": (
        "LANGUAGE: The student is communicating in English. Respond entirely in English."
    ),
    "es": (
        "IDIOMA: El estudiante se está comunicando en español. Responde completamente en español. "
        "Usa terminología dental clínica en español (con el equivalente en inglés entre paréntesis "
        "la primera vez que aparezca un término técnico, por ejemplo: 'zirconia (zirconia)'). "
        "Si el estudiante escribe en inglés, responde igualmente en español."
    ),
}

def build_system_prompt(lang: str) -> str:
    base = (
        "You are a clinical study assistant for a dental student at NSU College of Dental Medicine. "
        "You were built to help them review and apply their own school materials during clinical work and study. "
        "The student is the clinician — you are their reference tool.\n\n"
        "INSTRUCTIONS:\n"
        "- Answer questions directly and clinically, drawing on any provided material excerpts AND your general dental knowledge.\n"
        "- Material excerpts are a representative sample — they may not cover every aspect. Do NOT refuse to answer just because a specific detail is absent.\n"
        "- When information comes from an excerpt, credit the file (e.g. 'According to your Fixed Pros notes…').\n"
        "- When filling gaps from general knowledge, say so naturally.\n"
        "- Be specific: measurements, materials, sequences, clinical reasoning.\n"
        "- Skip safety disclaimers — this tool exists to help the student think through cases.\n"
        "- In follow-up questions, use the conversation history to give contextually aware answers; "
        "do not re-introduce yourself or repeat context already established.\n"
        "- Structure answers clearly: preparation guidelines, key specs, sequencing, common pitfalls — "
        "but adapt the structure to what the question actually needs.\n\n"
    )
    return base + _LANG_INSTRUCTION[lang]


# ─── Hero Section ────────────────────────────────────────────────────────────────
_logo = get_logo_tag()
_rays = make_rays_html()
st.markdown(
    "<div class='hero-wrapper'>"
    + _rays +
    "<div class='orb orb-1'></div>"
    "<div class='orb orb-2'></div>"
    "<div class='orb orb-3'></div>"
    "<div class='orb orb-4'></div>"
    "<div class='orb orb-5'></div>"
    "<div class='orb orb-6'></div>"
    "<div class='orb orb-7'></div>"
    "<div class='orb orb-8'></div>"
    "<div class='orb orb-9'></div>"
    "<div class='orb orb-10'></div>"
    "<div class='ring-accent'></div>"
    "<div class='ring-accent-inner'></div>"
    "<div class='hero-content'>"
    f"<div class='hero-logo-wrap'>{_logo}</div>"
    "<div class='hero-divider'></div>"
    "<div class='hero-text'>"
    "<div class='hero-eyebrow'>"
    "<div class='hero-eyebrow-dot'></div>"
    f"<span class='hero-eyebrow-label'>{_t['eyebrow']}</span>"
    "</div>"
    f"<p class='hero-title'>{_t['hero_title']}</p>"
    f"<p class='hero-sub'>{_t['hero_sub']}</p>"
    "</div>"
    "<div class='hero-stats'>"
    "<div class='stat-chip'><span class='stat-num'>🦷</span><span class='stat-label'>DentAI</span></div>"
    "<div class='stat-chip'><span class='stat-num'>NSU</span><span class='stat-label'>CDM</span></div>"
    "</div>"
    "</div>"
    "<div class='hero-wave'>"
    "<svg viewBox='0 0 1440 54' xmlns='http://www.w3.org/2000/svg' preserveAspectRatio='none' style='display:block;width:100%;'>"
    "<path d='M0,20 C240,50 480,0 720,24 C960,48 1200,4 1440,24 L1440,54 L0,54 Z' fill='#eef1f8'/>"
    "</svg>"
    "</div>"
    "</div>",
    unsafe_allow_html=True
)

# ─── Floating sidebar toggle button (always visible) ─────────────────────────────
components.html("""
<script>
(function() {
    var doc = window.parent.document;

    function injectBtn() {
        if (doc.getElementById('dentai-sidebar-toggle')) return;

        var btn = doc.createElement('button');
        btn.id = 'dentai-sidebar-toggle';
        btn.title = 'Toggle sidebar';
        btn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#FDB913" stroke-width="2.5" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>';
        btn.style.cssText = [
            'position:fixed',
            'top:14px',
            'left:14px',
            'z-index:99999',
            'background:#003087',
            'border:2px solid #FDB913',
            'border-radius:8px',
            'padding:6px 8px',
            'cursor:pointer',
            'line-height:0',
            'box-shadow:0 2px 14px rgba(0,0,0,0.35)',
            'transition:background 0.2s'
        ].join(';');
        btn.onmouseenter = function() { btn.style.background = '#0041b3'; };
        btn.onmouseleave = function() { btn.style.background = '#003087'; };
        btn.onclick = function() {
            var native =
                doc.querySelector('[data-testid="stSidebarCollapseButton"] button') ||
                doc.querySelector('[data-testid="collapsedControl"] button');
            if (native) native.click();
        };
        doc.body.appendChild(btn);
    }

    injectBtn();
    new MutationObserver(injectBtn).observe(doc.body, { childList: true, subtree: false });
})();
</script>
""", height=0)


# ─── Conversation History ────────────────────────────────────────────────────────
def render_response(assistant_text, sources, images):
    """Render one assistant turn: response card + optional image panel + sources."""
    t = _UI[st.session_state.lang]
    st.markdown(
        "<div class='response-wrapper'>"
        "<div class='response-card'>"
        "<div class='response-header'>"
        f"<span class='response-badge'>{t['badge']}</span>"
        f"<p class='response-title'>{t['response_title']}</p>"
        "</div>"
        "</div>"
        "</div>",
        unsafe_allow_html=True
    )
    if images:
        col_text, col_imgs = st.columns([3, 1.4], gap="medium")
        with col_text:
            st.markdown(assistant_text)
        with col_imgs:
            st.markdown(
                f"<div class='img-panel-header'>{t['img_panel']}</div>"
                "<div class='img-panel-body'>",
                unsafe_allow_html=True
            )
            for img in images:
                st.markdown("<div class='img-card'>", unsafe_allow_html=True)
                st.image(img["bytes"], use_container_width=True)
                st.markdown(
                    f"<div class='img-caption'>{img['caption']}</div></div>",
                    unsafe_allow_html=True
                )
            st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.markdown(assistant_text)

    if sources:
        unique_sources = sorted(set(sources))
        pills_html = "".join(
            [f'<span class="source-pill" style="animation-delay:{i*0.07}s">📄 {s}</span>'
             for i, s in enumerate(unique_sources)]
        )
        st.markdown(
            "<div class='sources-wrapper'>"
            f"<p class='sources-head'>{t['sources_head']}</p>"
            f"<div class='pill-row'>{pills_html}</div>"
            "</div>",
            unsafe_allow_html=True
        )


# ─── Chat History (flows naturally, page scrolls) ────────────────────────────────
_has_history = bool(st.session_state.chat_history)

if _has_history:
    for i, exchange in enumerate(st.session_state.chat_history):
        if i > 0:
            st.markdown("<hr class='chat-exchange-divider'>", unsafe_allow_html=True)
        _ts = exchange.get("timestamp", "")
        _ts_display = ""
        if _ts:
            try:
                _ts_display = datetime.fromisoformat(_ts).strftime("%-m/%-d/%Y %-I:%M %p")
            except Exception:
                _ts_display = _ts[:16].replace("T", " ")
        st.markdown(
            "<div class='chat-user-bubble'>"
            f"<span class='chat-user-label'>{_t['you_asked']}"
            + (f"<span style='font-weight:400;opacity:0.6;margin-left:8px;font-size:0.65rem;'>{_ts_display}</span>" if _ts_display else "")
            + "</span>"
            f"<p class='chat-user-text'>{exchange['user']}</p>"
            "</div>",
            unsafe_allow_html=True
        )
        imgs = st.session_state.latest_images if i == len(st.session_state.chat_history) - 1 else []
        render_response(exchange["assistant"], exchange["sources"], imgs)

    # Scroll the page to the bottom so the latest reply is visible
    components.html("""
    <script>
    window.parent.scrollTo({top: window.parent.document.body.scrollHeight, behavior: 'smooth'});
    </script>
    """, height=0)

# ─── Fixed-bottom chat input (native Streamlit) ───────────────────────────────────
_placeholder = _t["placeholder_followup"] if _has_history else _t["placeholder_new"]
case_input = st.chat_input(_placeholder)


# ─── Process Submission ───────────────────────────────────────────────────────────
if case_input:
    _loader = st.empty()
    _loader.markdown(
            "<div class='shark-loader'>"
            "<div class='shark-ocean'>"
            "<span class='shark-emoji'>🦈</span>"
            "<div class='bubble' style='width:6px;height:6px;left:20%;animation-duration:1.8s;animation-delay:0s;'></div>"
            "<div class='bubble' style='width:4px;height:4px;left:35%;animation-duration:2.2s;animation-delay:0.4s;'></div>"
            "<div class='bubble' style='width:5px;height:5px;left:55%;animation-duration:1.6s;animation-delay:0.9s;'></div>"
            "<div class='bubble' style='width:3px;height:3px;left:70%;animation-duration:2.0s;animation-delay:0.2s;'></div>"
            "<div class='bubble' style='width:6px;height:6px;left:82%;animation-duration:1.9s;animation-delay:0.6s;'></div>"
            "</div>"
            f"<div class='shark-label'>{_t['shark_label']}"
            "<span class='dot-row'>"
            "<span class='dot'></span><span class='dot'></span><span class='dot'></span>"
            "</span></div>"
            "</div>",
            unsafe_allow_html=True
        )

    # ── Retrieve context ──
    index = load_index()
    retriever = index.as_retriever(similarity_top_k=20)
    relevant_nodes = retriever.retrieve(case_input)

    context = ""
    sources = []
    for node in relevant_nodes:
        fname = node.metadata.get("file_name", "Unknown source")
        context += f"--- Excerpt from: {fname} ---\n{node.text}\n\n"
        if node.metadata.get("file_name"):
            sources.append(node.metadata["file_name"])

    # ── Build user message with context ──
    user_ctx = (
        f"Relevant excerpts from my NSU dental school materials:\n\n{context}\n"
        f"My question / patient case:\n{case_input}"
    )

    # ── Build full message thread (history + current) ──
    # user_ctx holds the full PDF-augmented prompt; fall back to plain question
    # for exchanges loaded from cache (where user_ctx wasn't saved to save space)
    api_messages = []
    for ex in st.session_state.chat_history:
        api_messages.append({"role": "user",      "content": ex.get("user_ctx", ex["user"])})
        api_messages.append({"role": "assistant", "content": ex["assistant"]})
    api_messages.append({"role": "user", "content": user_ctx})

    # ── Route to the right model based on question complexity ──
    selected_model = route_model(case_input)

    response = anthropic_client.messages.create(
        model=selected_model,
        max_tokens=2500,
        system=build_system_prompt(st.session_state.lang),
        messages=api_messages
    )
    assistant_text = response.content[0].text

    _loader.empty()

    # ── Extract images (stored in session state for display) ──
    page_images, _ = extract_page_images(relevant_nodes, max_images=5)
    st.session_state.latest_images = page_images

    # ── Save exchange to history + persist to disk ──
    st.session_state.chat_history.append({
        "user":      case_input,
        "user_ctx":  user_ctx,        # kept in memory for API threading
        "assistant": assistant_text,
        "sources":   sources,
        "timestamp": datetime.now().isoformat(),
    })
    save_session(st.session_state.current_session_id, st.session_state.chat_history)
    st.rerun()


# ─── Sidebar ─────────────────────────────────────────────────────────────────────
with st.sidebar:
    # ── Language toggle (top of sidebar) ────────────────────────────────────────
    st.markdown(f"<span class='lang-label'>{_t['lang_label']}</span>", unsafe_allow_html=True)
    if st.button(_t["lang_toggle"]):
        st.session_state.lang = "es" if st.session_state.lang == "en" else "en"
        st.rerun()

    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
    if st.button(_t["btn_new_chat"]):
        st.session_state.current_session_id = new_session_id()
        st.session_state.chat_history       = []
        st.session_state.latest_images      = []
        st.rerun()

    # ── Past sessions browser ────────────────────────────────────────────────────
    _all_sessions = list_all_sessions()
    if _all_sessions:
        st.markdown("<hr>", unsafe_allow_html=True)
        st.markdown(
            "<p style='color:#FDB913;font-size:0.85rem;font-weight:700;"
            "letter-spacing:0.3px;margin:0 0 10px 0;'>🕘 Past Sessions</p>",
            unsafe_allow_html=True
        )
        # Show newest first
        for sess in reversed(_all_sessions):
            is_active   = sess["id"] == st.session_state.current_session_id
            is_renaming = st.session_state.get("_renaming_id") == sess["id"]
            _date  = sess.get("created_at", "")[:10]
            _count = len(sess.get("exchanges", []))
            _title = sess.get("title", "Untitled")
            _title_short = (_title[:44] + "…") if len(_title) > 44 else _title

            # ── Rename mode ──────────────────────────────────────────────────
            if is_renaming:
                new_name = st.text_input(
                    "Rename",
                    value=_title,
                    key=f"rename_input_{sess['id']}",
                    label_visibility="collapsed",
                )
                col_save, col_cancel = st.columns(2)
                with col_save:
                    if st.button("Save", key=f"rename_save_{sess['id']}", use_container_width=True):
                        if new_name.strip():
                            rename_session(sess["id"], new_name.strip())
                        del st.session_state["_renaming_id"]
                        st.rerun()
                with col_cancel:
                    if st.button("Cancel", key=f"rename_cancel_{sess['id']}", use_container_width=True):
                        del st.session_state["_renaming_id"]
                        st.rerun()
                continue

            # ── Normal display ───────────────────────────────────────────────
            col_card, col_menu = st.columns([6, 1])
            with col_card:
                if is_active:
                    st.markdown(
                        "<div class='sess-item'>"
                        "<div class='sess-dot'></div>"
                        f"<div class='sess-title'>{_title_short}</div>"
                        "</div>",
                        unsafe_allow_html=True
                    )
                else:
                    _sid = sess['id']
                    _safe_title = _title_short.replace("'", "&#39;").replace('"', "&quot;")
                    st.markdown(
                        f"<div class='sess-row' "
                        f"onclick=\"(function(){{var u=new URL(window.parent.location);"
                        f"u.searchParams.set('load_sess','{_sid}');"
                        f"window.parent.location=u;}})()\">"
                        f"<span class='sess-label'>{_safe_title}</span></div>",
                        unsafe_allow_html=True
                    )

            with col_menu:
                with st.popover("⋮", use_container_width=True):
                    if st.button("✏️  Rename", key=f"rename_btn_{sess['id']}", use_container_width=True):
                        st.session_state["_renaming_id"] = sess["id"]
                        st.rerun()
                    if st.button("🗑  Delete", key=f"del_btn_{sess['id']}", use_container_width=True):
                        if is_active:
                            st.session_state.current_session_id = new_session_id()
                            st.session_state.chat_history       = []
                            st.session_state.latest_images      = []
                        delete_session(sess["id"])
                        st.rerun()

    st.markdown("<hr>", unsafe_allow_html=True)

    # ── How to use ───────────────────────────────────────────────────────────────
    st.markdown(f"## {_t['sidebar_how']}")
    for num, text in _t["how_steps"]:
        st.markdown(
            f"<div class='sb-step'><div class='sb-num'>{num}</div><div class='sb-text'>{text}</div></div>",
            unsafe_allow_html=True
        )

    st.markdown("<hr>", unsafe_allow_html=True)

    # ── Add materials ────────────────────────────────────────────────────────────
    st.markdown(f"## {_t['sidebar_add']}")
    for num, text in _t["add_steps"]:
        st.markdown(
            f"<div class='sb-step'><div class='sb-num'>{num}</div><div class='sb-text'>{text}</div></div>",
            unsafe_allow_html=True
        )

    st.markdown(
        f"<div class='sb-footer'><p>{_t['sb_footer']}</p></div>",
        unsafe_allow_html=True
    )

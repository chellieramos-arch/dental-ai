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
import time

def _create_with_retry(client, max_retries=4, **kwargs):
    """Retry anthropic messages.create on 529 Overloaded errors with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return client.messages.create(**kwargs)
        except Exception as e:
            is_overloaded = (
                getattr(e, "status_code", None) == 529
                or "overloaded" in str(e).lower()
            )
            if not is_overloaded or attempt == max_retries - 1:
                raise
            wait = 2 ** attempt  # 1s, 2s, 4s, 8s
            time.sleep(wait)

from llama_index.core import VectorStoreIndex
from llama_index.core import StorageContext

# ─── Config (controls local vs cloud mode) ───────────────────────────────────
from config import IS_LOCAL, IS_CLOUD, CHROMA_PATH, CHROMA_COLLECTION
from agents import (
    get_mode_prompt, get_mode_meta, mode_keys, mode_labels, DEFAULT_MODE, AGENT_MODES,
    CLINICAL_DEPARTMENTS, DEFAULT_DEPARTMENT, get_department_context,
)

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

def _load_profile() -> dict:
    """Fetch and cache the current user's profile from the Supabase profiles table."""
    if not IS_CLOUD:
        return {}
    if "user_profile" in st.session_state:
        return st.session_state.user_profile
    try:
        sb = _get_supabase()
        result = (
            sb.table("profiles")
              .select("full_name,role,program_year,school_id")
              .eq("email", _current_user_email())
              .execute()
        )
        profile = result.data[0] if result.data else {}
        st.session_state.user_profile = profile
        return profile
    except Exception:
        st.session_state.user_profile = {}
        return {}

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

# ─── Supabase Auth Gate (cloud mode only) ────────────────────────────────────
# Two flows: Log In (email + password) and Create Account (signup with profile).
# Domain restriction: only NSU email addresses are accepted.

_NSU_DOMAINS = ("@mynsu.nova.edu", "@nova.edu", "@health.snova.edu")

_PROGRAM_YEAR_OPTIONS = ["D1", "D2", "D3", "D4", "Resident", "Faculty", "Staff", "Other"]
_ROLE_OPTIONS         = ["student", "faculty", "admin"]

def _is_nsu_email(email: str) -> bool:
    return any(email.strip().lower().endswith(d) for d in _NSU_DOMAINS)

# ─── Page Config — MUST be the first st.* call ───────────────────────────────────
# We pick layout here based on session_state so we don't need a second call later.
_user_is_logged_in = IS_LOCAL or ("user_email" in st.session_state)
st.set_page_config(
    page_title="DentAI – NSU College of Dental Medicine" if _user_is_logged_in else "DentAI – NSU Login",
    page_icon="🦷",
    layout="wide" if _user_is_logged_in else "centered",
    initial_sidebar_state="expanded",
)

# ─── Auto-restore session from URL token (survives page refresh) ─────────────────
# We store the Supabase access token in ?s= so a browser refresh restores auth
# without any cookie library. The token is verified server-side on each restore.
if IS_CLOUD and "user_email" not in st.session_state:
    _url_token = st.query_params.get("s", "")
    if _url_token:
        try:
            _sb = _get_supabase()
            _user_resp = _sb.auth.get_user(jwt=_url_token)
            if _user_resp and _user_resp.user:
                st.session_state.user_email = _user_resp.user.email
                st.rerun()
        except Exception:
            # Token expired or invalid — clear it and show login
            st.query_params.pop("s", None)

if IS_CLOUD and "user_email" not in st.session_state:
    st.markdown("""
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Space+Grotesk:wght@500;600;700&display=swap');

      :root {
        --bg: #050a12; --surface: #0c1524;
        --border: rgba(0,200,255,0.15); --cyan: #00c8ff;
        --purple: #7b5ea7; --text: #e8f0fe; --muted: #7a90b0;
      }

      [data-testid="stAppViewContainer"], .main {
        background: var(--bg) !important;
        font-family: 'Inter', sans-serif;
      }
      [data-testid="stHeader"] { display: none; }
      #MainMenu, footer { visibility: hidden; }

      /* inputs */
      [data-testid="stTextInput"] input {
        background: var(--surface) !important;
        border: 1px solid var(--border) !important;
        border-radius: 10px !important;
        color: var(--text) !important;
        font-size: 0.95rem !important;
        padding: 12px 16px !important;
      }
      [data-testid="stTextInput"] input::placeholder { color: var(--muted) !important; }
      [data-testid="stTextInput"] input:focus {
        border-color: var(--cyan) !important;
        box-shadow: 0 0 0 3px rgba(0,200,255,0.08) !important;
      }
      [data-testid="stTextInput"] label { color: var(--muted) !important; font-size: 0.82rem !important; }

      /* selectbox */
      [data-testid="stSelectbox"] > div > div {
        background: var(--surface) !important;
        border: 1px solid var(--border) !important;
        border-radius: 10px !important;
        color: var(--text) !important;
      }
      [data-testid="stSelectbox"] label { color: var(--muted) !important; font-size: 0.82rem !important; }

      /* tabs */
      [data-testid="stTabs"] [data-baseweb="tab-list"] {
        background: transparent !important;
        border-bottom: 1px solid var(--border) !important;
        margin-bottom: 1.5rem;
      }
      [data-testid="stTabs"] [data-baseweb="tab"] {
        color: var(--muted) !important;
        font-weight: 600 !important;
        font-size: 0.9rem !important;
        padding: 10px 20px !important;
      }
      [data-testid="stTabs"] [aria-selected="true"] {
        color: var(--cyan) !important;
        border-bottom: 2px solid var(--cyan) !important;
      }

      /* primary button */
      .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, var(--cyan), #0090cc) !important;
        color: #000 !important; font-weight: 800 !important;
        border: none !important; border-radius: 10px !important;
        font-size: 0.95rem !important; padding: 12px !important;
      }
      .stButton > button[kind="primary"] p { color: #000 !important; font-weight: 800 !important; }
      .stButton > button[kind="primary"]:hover {
        box-shadow: 0 4px 20px rgba(0,200,255,0.35) !important;
        transform: translateY(-1px) !important;
      }

      /* info/error boxes */
      [data-testid="stAlert"] {
        background: var(--surface) !important;
        border: 1px solid var(--border) !important;
        border-radius: 10px !important;
        color: var(--text) !important;
      }

      /* all text */
      p, span, label, div { color: var(--text); }
    </style>

    <div style="text-align:center; padding:3.5rem 0 2rem;">
      <div style="width:64px;height:64px;background:linear-gradient(135deg,#00c8ff,#7b5ea7);
                  border-radius:16px;margin:0 auto 20px;display:flex;align-items:center;
                  justify-content:center;font-family:'Space Grotesk',sans-serif;
                  font-size:24px;font-weight:900;color:#fff;letter-spacing:-1px;">D+</div>
      <div style="font-family:'Space Grotesk',sans-serif;font-size:2rem;font-weight:700;
                  color:#e8f0fe;letter-spacing:-0.02em;margin-bottom:8px;">
        Dent<span style="color:#00c8ff;">AI</span> Assist
      </div>
      <div style="font-size:0.9rem;color:#7a90b0;font-weight:500;">
        NSU College of Dental Medicine &nbsp;·&nbsp; Student Study Portal
      </div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        sb = _get_supabase()
        tab_login, tab_signup = st.tabs(["Log In", "Create Account"])

        # ── Log In tab ────────────────────────────────────────────────────────
        with tab_login:
            login_email = st.text_input(
                "NSU Email", placeholder="yourname@mynsu.nova.edu",
                key="login_email",
            )
            login_pw = st.text_input(
                "Password", type="password", placeholder="Your password",
                key="login_pw",
            )
            if st.button("Log In", use_container_width=True, type="primary", key="btn_login"):
                _email = login_email.strip().lower()
                _pw    = login_pw.strip()
                if not _email or not _pw:
                    st.error("Please enter your email and password.")
                elif not _is_nsu_email(_email):
                    st.error("Please use your NSU email (@mynsu.nova.edu, @nova.edu, or @health.snova.edu).")
                else:
                    try:
                        resp = sb.auth.sign_in_with_password({"email": _email, "password": _pw})
                        st.session_state.user_email = resp.user.email
                        if resp.session and resp.session.access_token:
                            st.query_params["s"] = resp.session.access_token
                        st.rerun()
                    except Exception as e:
                        st.error(f"Login failed. Check your email and password. ({e})")

            st.markdown(
                "<p style='text-align:center;font-size:0.8rem;color:#7a90b0;margin-top:12px;'>"
                "Forgot your password? Use the <b>Create Account</b> tab to reset via a new signup, "
                "or contact your program administrator."
                "</p>",
                unsafe_allow_html=True,
            )

        # ── Create Account tab ────────────────────────────────────────────────
        with tab_signup:
            su_email = st.text_input(
                "NSU Email", placeholder="yourname@mynsu.nova.edu",
                key="su_email",
            )
            su_pw = st.text_input(
                "Password", type="password",
                placeholder="At least 8 characters",
                key="su_pw",
            )
            su_pw2 = st.text_input(
                "Confirm Password", type="password",
                placeholder="Repeat your password",
                key="su_pw2",
            )
            su_name = st.text_input(
                "Full Name", placeholder="First Last",
                key="su_name",
            )
            su_school_id = st.text_input(
                "Student / Faculty ID", placeholder="NSU-issued ID number",
                key="su_school_id",
            )

            role_col, year_col = st.columns(2)
            with role_col:
                su_role = st.selectbox(
                    "Role", options=_ROLE_OPTIONS,
                    key="su_role",
                )
            with year_col:
                su_year = st.selectbox(
                    "Program / Year", options=_PROGRAM_YEAR_OPTIONS,
                    key="su_year",
                )

            if st.button("Create Account", use_container_width=True, type="primary", key="btn_signup"):
                _email = su_email.strip().lower()
                _pw    = su_pw.strip()
                _pw2   = su_pw2.strip()
                _name  = su_name.strip()
                _sid   = su_school_id.strip()

                # Validation
                if not all([_email, _pw, _pw2, _name, _sid]):
                    st.error("Please fill in all fields.")
                elif not _is_nsu_email(_email):
                    st.error("Please use your NSU email (@mynsu.nova.edu, @nova.edu, or @health.snova.edu).")
                elif len(_pw) < 8:
                    st.error("Password must be at least 8 characters.")
                elif _pw != _pw2:
                    st.error("Passwords do not match.")
                else:
                    try:
                        # 1. Create the auth user
                        resp = sb.auth.sign_up({"email": _email, "password": _pw})
                        _user = resp.user

                        if _user:
                            # 2. Insert the profile row
                            sb.table("profiles").insert({
                                "id":           _user.id,
                                "email":        _email,
                                "full_name":    _name,
                                "school_id":    _sid,
                                "role":         su_role,
                                "program_year": su_year,
                            }).execute()

                            # 3. Log in immediately if session returned (email confirmation off)
                            if resp.session and resp.session.access_token:
                                st.session_state.user_email = _user.email
                                st.query_params["s"] = resp.session.access_token
                                st.rerun()
                            else:
                                # Email confirmation is enabled in Supabase — ask user to verify
                                st.success(
                                    f"Account created! Check **{_email}** for a confirmation link, "
                                    "then return here to log in."
                                )
                        else:
                            st.error("Signup failed — this email may already be registered. Try logging in instead.")

                    except Exception as e:
                        st.error(f"Could not create account. ({e})")

    st.stop()

# ─── Handle session-load query param (from sidebar HTML links) ───────────────────
if "load_sess" in st.query_params:
    _load_id = st.query_params.get("load_sess", "")
    if _load_id and _load_id != st.session_state.get("current_session_id"):
        # Different session requested — load it (URL already has the right param)
        st.session_state.current_session_id = _load_id
        st.session_state.chat_history       = load_session(_load_id)
        st.session_state.latest_images      = []
        st.rerun()
    elif not _load_id:
        st.query_params.clear()

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
if "agent_mode" not in st.session_state:
    st.session_state.agent_mode = DEFAULT_MODE
if "clinical_dept" not in st.session_state:
    st.session_state.clinical_dept = DEFAULT_DEPARTMENT

# Keep the URL in sync so browser refresh restores the current session
st.query_params["load_sess"] = st.session_state.current_session_id

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
        "sb_footer":        ("Nova Southeastern University<br>College of Dental Medicine<br>"
                             "<span style='color:#FDB913;font-weight:600;'>Powered by DentAI</span>"),
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

# ─── Language shortcut ───────────────────────────────────────────────────────────
_t = _UI[st.session_state.lang]   # shortcut to current-language strings

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
.stat-chip-lg {
    padding: 18px 32px !important;
    border-radius: 14px !important;
}
.stat-num {
    color: #FDB913 !important;
    font-size: 1.3rem;
    font-weight: 700;
    display: block;
    line-height: 1;
}
.stat-num-lg {
    font-size: 2.4rem !important;
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
.stat-label-lg {
    font-size: 0.9rem !important;
    letter-spacing: 1px !important;
    margin-top: 6px !important;
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

/* Scrollable session list container — NSU themed box */
section[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] {
    background: rgba(0,20,70,0.35) !important;
    border: 1px solid rgba(253,185,19,0.25) !important;
    border-radius: 10px !important;
    padding: 4px 0 !important;
}
/* Scrollbar inside the session container */
section[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] * {
    scrollbar-width: thin;
    scrollbar-color: rgba(253,185,19,0.4) transparent;
}
section[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] ::-webkit-scrollbar {
    width: 4px;
}
section[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] ::-webkit-scrollbar-track {
    background: transparent;
}
section[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] ::-webkit-scrollbar-thumb {
    background: rgba(253,185,19,0.45);
    border-radius: 2px;
}
section[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] ::-webkit-scrollbar-thumb:hover {
    background: rgba(253,185,19,0.75);
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
    padding: 0 !important;
    width: 100% !important;
    height: 32px !important;
    min-height: 32px !important;
    box-shadow: none !important;
    animation: none !important;
    overflow: hidden !important;
    position: relative !important;
}
section[data-testid="stSidebar"] [data-testid="stPopover"] button:hover {
    background: rgba(253,185,19,0.15) !important;
    border-color: rgba(253,185,19,0.6) !important;
    transform: none !important;
    box-shadow: none !important;
}
/* Hide all auto-rendered children (label + chevron) */
section[data-testid="stSidebar"] [data-testid="stPopover"] button > * {
    display: none !important;
}
/* Inject ⋮ via pseudo-element so only one symbol shows */
section[data-testid="stSidebar"] [data-testid="stPopover"] button::after {
    content: "⋮";
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    width: 100% !important;
    height: 100% !important;
    color: #FDB913 !important;
    font-size: 1.2rem !important;
    font-weight: 700 !important;
    position: absolute !important;
    top: 0; left: 0;
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

/* ALL sidebar buttons → base: plain text, no pill (covers session buttons) */
section[data-testid="stSidebar"] .stButton > button {
    background: transparent !important;
    color: rgba(255,255,255,0.82) !important;
    border: none !important;
    border-radius: 4px !important;
    padding: 5px 8px !important;
    font-size: 0.82rem !important;
    font-weight: 400 !important;
    letter-spacing: 0 !important;
    text-align: left !important;
    animation: none !important;
    box-shadow: none !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
}
section[data-testid="stSidebar"] .stButton > button p,
section[data-testid="stSidebar"] .stButton > button span {
    color: rgba(255,255,255,0.82) !important;
    font-weight: 400 !important;
    font-size: 0.82rem !important;
}
section[data-testid="stSidebar"] .stButton > button:hover {
    background: rgba(255,255,255,0.08) !important;
    color: #ffffff !important;
    transform: none !important;
    box-shadow: none !important;
}

/* Primary buttons override → gold pill (New Chat, language toggle)
   More specific selector wins: .stButton > button[attr] beats .stButton > button */
section[data-testid="stSidebar"] .stButton > button[data-testid="baseButton-primary"] {
    background: rgba(253,185,19,0.15) !important;
    color: #FDB913 !important;
    border: 1px solid rgba(253,185,19,0.45) !important;
    border-radius: 20px !important;
    padding: 7px 20px !important;
    font-size: 0.85rem !important;
    font-weight: 700 !important;
    letter-spacing: 0.3px !important;
    text-align: center !important;
    white-space: normal !important;
    overflow: visible !important;
}
section[data-testid="stSidebar"] .stButton > button[data-testid="baseButton-primary"] p,
section[data-testid="stSidebar"] .stButton > button[data-testid="baseButton-primary"] span {
    color: #FDB913 !important;
    font-weight: 700 !important;
    font-size: 0.85rem !important;
}
section[data-testid="stSidebar"] .stButton > button[data-testid="baseButton-primary"]:hover {
    background: rgba(253,185,19,0.28) !important;
    border-color: rgba(253,185,19,0.7) !important;
    color: #FDB913 !important;
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
anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), max_retries=3)

# ─── Agent Tools ─────────────────────────────────────────────────────────────────
TOOLS = [
    {
        "name": "search_documents",
        "description": (
            "Search the student's NSU dental school materials for relevant clinical information. "
            "Call multiple times with different focused queries for complex or multi-part questions. "
            "Use a small n (3–6) for targeted lookups; larger n (10–15) for broad topic coverage."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query — be specific and clinical.",
                },
                "n": {
                    "type": "integer",
                    "description": "Number of results to retrieve (3–20). Default 8.",
                    "default": 8,
                },
                "faculty_tag": {
                    "type": "string",
                    "description": (
                        "Filter results to a specific corpus. "
                        "Options: 'abuna' (Restorative/Biomimetics), 'bendayan' (Fixed Prosthodontics), "
                        "'adex' (ADEX board exam practice materials). "
                        "Omit for a general search across all materials."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "annotate_image",
        "description": (
            "Annotate the attached radiograph with clinical findings. Call this when the student "
            "has uploaded a radiograph AND you have identified specific findings to mark — "
            "carious lesions, possible fractures, or bone level measurements. "
            "Provide coordinates as fractions of the image dimensions (0.0 to 1.0). "
            "For bone levels, measure the distance from the CEJ to the alveolar crest and "
            "estimate in millimeters using standard tooth anatomy as a scale reference "
            "(average crown height ~8-10mm, root ~14-17mm)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "description": "List of findings to annotate on the image.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["caries", "fracture", "bone_level", "pathology"],
                                "description": "Type of finding.",
                            },
                            "label": {
                                "type": "string",
                                "description": "Short label to display (e.g. 'Caries #14 mesial', 'Bone loss 4mm').",
                            },
                            "x": {
                                "type": "number",
                                "description": "Horizontal center of finding as fraction of image width (0.0=left, 1.0=right).",
                            },
                            "y": {
                                "type": "number",
                                "description": "Vertical center of finding as fraction of image height (0.0=top, 1.0=bottom).",
                            },
                            "x2": {
                                "type": "number",
                                "description": "For fracture lines and bone level measurements: end x coordinate (fraction).",
                            },
                            "y2": {
                                "type": "number",
                                "description": "For fracture lines and bone level measurements: end y coordinate (fraction).",
                            },
                            "measurement": {
                                "type": "string",
                                "description": "Optional measurement string (e.g. '3.5mm', '6mm bone loss').",
                            },
                        },
                        "required": ["type", "label", "x", "y"],
                    },
                },
            },
            "required": ["findings"],
        },
    },
    {
        "name": "ask_clarification",
        "description": (
            "Ask the student ONE targeted clarifying question when their query is missing critical "
            "clinical details needed for an accurate answer — e.g. tooth number, procedure type, "
            "material, or key patient factors. Only use this when the missing info would materially "
            "change your answer. Do NOT ask if you can give a useful answer without it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The single clarifying question to ask the student.",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "query_drug_interactions",
        "description": (
            "Look up drug-drug interactions and dental-relevant warnings using the NIH RxNav database. "
            "Call this when a patient's medication list is relevant to the clinical question — "
            "e.g. anticoagulants, antihypertensives, bisphosphonates, antibiotics, analgesics, "
            "or any drug that may affect treatment planning or medication prescribing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "drugs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of drug names to check for interactions (generic or brand names).",
                },
            },
            "required": ["drugs"],
        },
    },
    {
        "name": "get_clinical_guideline",
        "description": (
            "Query the DentAI clinical decision rules database for evidence-based guidance. "
            "Use this for questions involving: anticoagulants, diabetes, cardiac conditions, "
            "bisphosphonates, endocarditis prophylaxis, hypertension, pregnancy, renal/hepatic "
            "impairment, immunosuppression, bleeding disorders, allergies, or antibiotic/analgesic prescribing. "
            "Returns structured ADA-based recommendations with severity levels."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "condition": {
                    "type": "string",
                    "description": "The medical condition or drug class to look up (e.g. 'warfarin', 'diabetes', 'penicillin allergy', 'bisphosphonate').",
                },
                "procedure": {
                    "type": "string",
                    "description": "The dental procedure being considered (e.g. 'extraction', 'implant', 'prescribing antibiotics'). Optional.",
                },
            },
            "required": ["condition"],
        },
    },
    {
        "name": "provide_answer",
        "description": (
            "Provide the final clinical answer to the student. Call this once you have gathered "
            "sufficient information from your searches. Write the full answer in markdown."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "answer": {
                    "type": "string",
                    "description": "The complete clinical answer in markdown format.",
                },
                "needs_images": {
                    "type": "boolean",
                    "description": (
                        "True if images from source materials would help (procedural / technique questions). "
                        "False for definitions, pharmacology, or conceptual questions."
                    ),
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filenames of source documents cited in the answer.",
                },
            },
            "required": ["answer", "needs_images", "sources"],
        },
    },
]

# ─── ADEX Tool Extension (web search — ADEX mode only) ───────────────────────────
# Anthropic's native web_search_20250305 tool is server-side: Anthropic executes the
# search and returns results automatically in the tool-use loop. No external API key needed.
# Only injected into the tool list when agent_mode == "adex".

_WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 5,
}

TOOLS_ADEX = TOOLS + [_WEB_SEARCH_TOOL]

# ─── Faculty + Language helpers ──────────────────────────────────────────────────
# (Model routing is now handled inside run_agent — Sonnet for agent orchestration,
#  Haiku for the memory pre-analysis step. The sidebar "AI Model" selector is kept
#  for manual overrides and is read inside run_agent via session_state.)

# ─── Faculty Configuration ───────────────────────────────────────────────────────
FACULTY = {
    "general": {
        "label": "🎓 General (Default)",
        "name": None,
        "specialty": None,
        "style": None,
        "corpus_tag": None,
    },
    "abuna": {
        "label": "👨‍⚕️ Dr. Abuna — Restorative & Biomimetics",
        "name": "Dr. Abuna",
        "specialty": "Restorative Dentistry and Biomimetics",
        "style": (
            "Dr. Abuna specializes in Restorative Dentistry and Biomimetics — the science of restoring teeth "
            "to mimic natural tooth structure, function, and esthetics. When responding as Dr. Abuna, "
            "emphasize biomimetic principles: minimal intervention, preserving tooth structure, layering "
            "techniques that replicate natural enamel and dentin properties, adhesive protocols, and "
            "material selection that mimics natural biomechanics. Frame clinical decisions through the lens "
            "of long-term tooth preservation and biologic width respect."
        ),
        "corpus_tag": "abuna",
    },
    "bendayan": {
        "label": "👩‍⚕️ Dr. Bendayan — Fixed Prosthodontics",
        "name": "Dr. Bendayan",
        "specialty": "Fixed Prosthodontics",
        "style": (
            "Dr. Bendayan specializes in Fixed Prosthodontics — the design, fabrication, and placement of "
            "fixed restorations including crowns, bridges, and implant-supported prostheses. When responding "
            "as Dr. Bendayan, emphasize precision in preparation design, margin placement, occlusal schemes, "
            "provisionalization, and material science for fixed restorations. Frame answers with attention "
            "to long-term prosthetic success, cementation protocols, and the relationship between "
            "preparation design and final restoration outcome."
        ),
        "corpus_tag": "bendayan",
    },
}

# ─── DESIGNED FEATURE (not yet built): Faculty-Specific Knowledge Routing ────────
# Students will be able to select a faculty member from the sidebar.
# The system will:
#   1. Filter retrieval to documents tagged to that faculty member's corpus
#   2. Instruct Claude to reason in alignment with that faculty member's
#      published research and clinical philosophy
# This enables students to "learn from" specific professors on demand,
# grounded in their actual peer-reviewed work — not a generic AI voice.
# ─────────────────────────────────────────────────────────────────────────────────

# ─── Query Type Detection ────────────────────────────────────────────────────────
# Classifies a student query as PROCEDURAL or CLINICAL_REASONING.
# Procedural: step-by-step instructions for a known task
# Clinical Reasoning: diagnosis, treatment planning, differential, patient management


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

def analyze_student_memory(current_session_id: str, max_sessions: int = 10) -> str:
    """
    Pull prior session questions and run a Haiku call to produce a structured
    learning-profile analysis rather than dumping raw questions into the prompt.
    This gives the agent actionable insight — recurring gaps, focus areas, what to reinforce.
    """
    try:
        all_sessions = list_all_sessions()
        prior_sessions = [s for s in all_sessions if s["id"] != current_session_id]

        if not prior_sessions:
            return ""

        prior_questions = []
        for sess in prior_sessions[-max_sessions:]:
            exchanges = load_session(sess["id"])
            for ex in exchanges:
                q = ex.get("user", "").strip()
                if q:
                    prior_questions.append(q)

        if not prior_questions:
            return ""

        questions_text = "\n".join(f"- {q}" for q in prior_questions[-25:])

        # Haiku call: analyze patterns, don't just dump raw history
        try:
            analysis_resp = _create_with_retry(
                anthropic_client,
                model="claude-haiku-4-5-20251001",
                max_tokens=250,
                messages=[{
                    "role": "user",
                    "content": (
                        f"A dental student has asked these questions in past study sessions:\n{questions_text}\n\n"
                        "In 2–3 concise sentences: What are their recurring clinical focus areas? "
                        "What knowledge gaps appear? What should their study assistant proactively reinforce?"
                    ),
                }],
            )
            analysis = analysis_resp.content[0].text.strip()
        except Exception:
            # Fallback: use raw list if Haiku call fails
            analysis = f"This student has previously studied: {', '.join(set(prior_questions[-10:]))}."

        return (
            "\nSTUDENT LEARNING PROFILE (analyzed from prior sessions):\n"
            f"{analysis}\n"
            "Use this profile to tailor your responses — build on what they know, address recurring gaps, "
            "and proactively connect new questions to prior topics where relevant.\n\n"
        )
    except Exception:
        return ""


def build_faculty_context(faculty_key: str) -> str:
    """Return a faculty persona instruction block, or empty string for general mode."""
    faculty = FACULTY.get(faculty_key, FACULTY["general"])
    if not faculty["name"]:
        return ""
    return (
        f"FACULTY MODE — {faculty['name']} ({faculty['specialty']}):\n"
        f"You are responding in the clinical voice and style of {faculty['name']}. "
        f"{faculty['style']}\n"
        f"When citing materials, attribute them to {faculty['name']}'s curriculum where applicable. "
        f"Respond as this faculty member would teach — with their clinical priorities and philosophy "
        f"shaping how you frame every answer.\n\n"
    )


def build_system_prompt(lang: str, memory_context: str = "", faculty_key: str = "general",
                        has_image: bool = False, agent_mode: str = "direct",
                        clinical_dept: str = "general") -> str:
    base = (
        "You are a clinical study assistant for a dental student at NSU College of Dental Medicine. "
        "You were built to help them review and apply their own school materials during clinical work and study. "
        "The student is the clinician — you are their intelligent reference tool.\n\n"

        "AGENTIC TOOL USE GUIDELINES:\n"
        "- Reason from your clinical knowledge first. Reach for tools deliberately, not reflexively.\n"
        "- Call search_documents when the question requires NSU-curriculum-specific content "
        "(a particular prep design, faculty protocol, school-specific material or technique) "
        "or when ADEX exam material would strengthen a board-practice explanation. "
        "Do not search for general clinical knowledge you already have.\n"
        "- For complex or multi-part questions, call search_documents multiple times with different "
        "focused sub-queries rather than one broad search.\n"
        "- Call query_drug_interactions or get_clinical_guideline when a specific drug class or "
        "medical condition is central to the clinical decision.\n"
        "- Call ask_clarification only when a missing detail (tooth number, procedure type, material, "
        "key patient factor) would materially change your answer. Not as a default.\n"
        "- When you have enough information, call provide_answer with the full response.\n"
        "- Set needs_images=true in provide_answer for procedural or technique questions "
        "(preps, instrumentation, step-by-step). Set needs_images=false for definitions, "
        "pharmacology, and conceptual questions.\n"
        "- If faculty mode is active, prefer calling search_documents with the matching faculty_tag.\n\n"

        "CLINICAL ANSWER GUIDELINES:\n"
        "- Be specific: measurements, materials, sequences, clinical reasoning.\n"
        "- When information comes from a source excerpt, credit the file "
        "(e.g. 'According to your Fixed Pros notes…').\n"
        "- When filling gaps from general dental knowledge, say so naturally.\n"
        "- Skip safety disclaimers — this is a study tool for clinical students.\n"
        "- In follow-up questions, use conversation history for context; do not re-introduce yourself.\n"
        "- If the student's learning profile shows recurring topics or gaps, weave that awareness "
        "into your response naturally.\n\n"
    )

    radiograph_guidance = ""
    if has_image:
        radiograph_guidance = (
            "RADIOGRAPH / IMAGE ANALYSIS GUIDELINES:\n"
            "The student has attached a clinical image — likely a dental radiograph or intraoral photo. "
            "Analyze it thoroughly before searching documents or answering.\n\n"
            "For radiographs, systematically evaluate:\n"
            "- Image type and quality (periapical, bitewing, panoramic, CBCT)\n"
            "- Tooth identification and numbering\n"
            "- Bone levels: crestal bone height, horizontal/vertical bone loss, furcation involvement\n"
            "- Periapical status: PDL space widening, periapical lucency/opacity, root tip pathology\n"
            "- Caries: interproximal, occlusal, cervical, secondary/recurrent under restorations\n"
            "- Existing restorations: type, integrity, marginal fit, secondary caries\n"
            "- Root morphology: length, curvature, resorption, root canal visibility\n"
            "- Crown-to-root ratio\n"
            "- Any anomalies: calcifications, supernumerary teeth, pathologic lesions\n\n"
            "For intraoral photos, evaluate:\n"
            "- Gingival health: color, contour, texture, inflammation, recession\n"
            "- Visible caries, fractures, wear facets, erosion\n"
            "- Soft tissue lesions: describe location, size, color, borders, surface texture\n"
            "- Restoration condition\n\n"
            "Structure your radiographic/image findings clearly, then integrate with the student's "
            "question and any relevant curriculum materials.\n\n"
        )

    faculty_context = build_faculty_context(faculty_key)
    dept_context    = get_department_context(clinical_dept)
    mode_context    = get_mode_prompt(agent_mode)
    return base + radiograph_guidance + dept_context + faculty_context + memory_context + mode_context + _LANG_INSTRUCTION[lang]


# ─── Query Logging + Topic Extraction ────────────────────────────────────────────

QUERY_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "query_logs.json")

# Dental topic taxonomy — keyword → topic label
_TOPIC_TAXONOMY = {
    "Restorative / Composites": [
        "composite", "restoration", "class ii", "class iii", "class iv", "class v",
        "resin", "bonding", "cavity", "caries", "dentin", "enamel", "flowable",
        "incremental", "bulk fill", "icdas", "amalgam",
    ],
    "Crown & Bridge": [
        "crown", "bridge", "fpd", "fixed partial", "zirconia", "porcelain",
        "pfm", "all ceramic", "prep", "preparation", "margin", "finish line",
        "provisional", "temporary", "cementation", "abutment", "retainer",
        "prosthodontic", "fixed pros",
    ],
    "Endodontics": [
        "root canal", "endodontic", "pulp", "pulpitis", "apex", "apical",
        "periapical", "rct", "obturation", "gutta percha", "access", "endo",
        "perforation", "resorption", "cracked tooth", "vertical root fracture",
    ],
    "Periodontics": [
        "perio", "periodontal", "gingivitis", "gingival", "bone loss",
        "pocket depth", "scaling", "root planing", "furcation", "attachment loss",
        "calculus", "plaque", "supra", "subgingival", "charting", "probing",
        "flap", "osseous", "regeneration",
    ],
    "Oral Surgery": [
        "extraction", "surgery", "surgical", "impacted", "wisdom tooth",
        "third molar", "incision", "flap design", "suture", "biopsy",
        "pericoronitis", "alveolar", "dry socket", "osteitis",
    ],
    "Implants": [
        "implant", "osseointegration", "implant placement", "sinus lift",
        "bone graft", "titanium", "implant crown", "abutment implant",
        "guided bone", "cbct implant",
    ],
    "Removable Prosthodontics": [
        "denture", "partial denture", "rpd", "complete denture", "removable",
        "occlusal rest", "clasp", "framework", "tooth selection", "impression",
        "wax rim", "try-in",
    ],
    "Pharmacology / Medications": [
        "drug", "medication", "antibiotic", "amoxicillin", "clindamycin",
        "ibuprofen", "analgesic", "anesthesia", "anesthetic", "lidocaine",
        "epinephrine", "prescription", "warfarin", "nsaid", "opioid",
        "interaction", "contraindication", "dosage", "pharmacology",
    ],
    "Medical Conditions / Systemic": [
        "diabetes", "diabetic", "hypertension", "cardiac", "heart", "warfarin",
        "anticoagulant", "bisphosphonate", "pregnancy", "renal", "kidney",
        "liver", "hiv", "immunocompromised", "blood thinner", "systemic",
        "medical history", "endocarditis", "pacemaker", "asthma",
    ],
    "Radiology / Imaging": [
        "radiograph", "x-ray", "xray", "cbct", "periapical film", "bitewing",
        "panoramic", "radiolucent", "radiopaque", "j-shaped", "bone level",
        "imaging",
    ],
    "Occlusion": [
        "occlusion", "bite", "centric relation", "centric occlusion", "mip",
        "crossbite", "overbite", "overjet", "bruxism", "parafunctional",
        "articulator", "facebow", "tmj", "tmd", "occlusal scheme",
    ],
    "Materials Science": [
        "material", "ceramic", "zirconia material", "lithium disilicate",
        "e.max", "pvs", "polyvinyl", "impression material", "adhesive",
        "cement", "resin cement", "glass ionomer", "handout gi", "flowable material",
    ],
    "Clinical Procedures / Technique": [
        "rubber dam", "matrix", "sectional matrix", "wedge", "isolation",
        "retraction cord", "temporization", "prep tip", "technique", "step",
        "procedure", "protocol", "sequence", "how to", "steps",
    ],
    "Infection Control": [
        "sterilization", "disinfection", "infection control", "ppe", "gloves",
        "barrier", "autoclave", "cross contamination", "aseptic",
    ],
}

def extract_topics(question: str) -> list:
    """Map a student question to one or more dental topic labels."""
    q = question.lower()
    found = []
    for topic, keywords in _TOPIC_TAXONOMY.items():
        if any(kw in q for kw in keywords):
            found.append(topic)
    return found or ["General / Other"]


def log_query(user_email: str, question: str, topics: list) -> None:
    """
    Persist a query log entry.
    Local mode  → query_logs.json
    Cloud mode  → Supabase query_logs table
    """
    entry = {
        "user_email": user_email,
        "question":   question[:500],   # truncate very long questions
        "topics":     topics,
        "timestamp":  datetime.now().isoformat(),
    }

    if IS_CLOUD:
        try:
            _get_supabase().table("query_logs").insert(entry).execute()
        except Exception:
            pass
        return

    # Local: append to JSON array
    try:
        if os.path.exists(QUERY_LOG_FILE):
            with open(QUERY_LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        else:
            logs = []
        logs.append(entry)
        # Keep last 5000 entries
        logs = logs[-5000:]
        with open(QUERY_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ─── Clinical Database Handlers ──────────────────────────────────────────────────

import json as _json
import urllib.request
import urllib.parse

_CLINICAL_RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clinical_rules.json")

@st.cache_data(ttl=3600)
def _load_clinical_rules() -> list:
    """Load clinical_rules.json once and cache."""
    try:
        with open(_CLINICAL_RULES_PATH, "r", encoding="utf-8") as f:
            return _json.load(f).get("rules", [])
    except Exception:
        return []


def _tool_clinical_guideline(condition: str, procedure: str = "") -> str:
    """
    Search the local clinical rules database by keyword matching.
    Returns formatted guidance text, or a not-found message.
    """
    rules = _load_clinical_rules()
    if not rules:
        return "Clinical rules database unavailable."

    condition_lower = condition.lower()
    procedure_lower = procedure.lower()

    matched = []
    for rule in rules:
        score = 0
        # Match against condition keywords
        for kw in rule.get("keywords", []):
            if kw in condition_lower or (procedure_lower and kw in procedure_lower):
                score += 2
        # Match condition field directly
        if any(w in rule.get("condition", "").lower() for w in condition_lower.split()):
            score += 1
        # Match procedure field if provided
        if procedure_lower:
            for proc in rule.get("procedures", []):
                if any(w in proc for w in procedure_lower.split()):
                    score += 1
        if score > 0:
            matched.append((score, rule))

    if not matched:
        return (
            f"No specific clinical guideline found for '{condition}' in the database. "
            "Use your general dental knowledge and NSU materials to guide the answer."
        )

    matched.sort(key=lambda x: x[0], reverse=True)
    top = matched[:3]  # Return up to 3 most relevant rules

    output_parts = [f"CLINICAL GUIDELINES — {condition.upper()}:\n"]
    for _, rule in top:
        severity_labels = {
            "contraindicated":      "🔴 CONTRAINDICATED",
            "caution":              "🟡 CAUTION",
            "modification_required":"🟠 MODIFICATION REQUIRED",
            "prophylaxis_required": "🔵 PROPHYLAXIS REQUIRED",
        }
        severity = severity_labels.get(rule.get("severity", "caution"), "⚠️ CAUTION")
        output_parts.append(
            f"{severity} — {rule['condition'].title()}\n"
            f"Recommendation: {rule['recommendation']}\n"
            f"Source: {rule.get('source', 'ADA guidelines')}\n"
        )

    return "\n".join(output_parts)


def _tool_drug_interactions(drugs: list) -> str:
    """
    Look up drug interactions via the NIH RxNav API (free, no key required).
    Falls back to a clear error message if the API is unavailable.
    """
    if not drugs:
        return "No drugs specified."

    results = []

    for drug_name in drugs[:4]:  # limit to 4 drugs per call
        try:
            # Step 1: resolve drug name → RxCUI
            encoded = urllib.parse.quote(drug_name.strip())
            rxcui_url = f"https://rxnav.nlm.nih.gov/REST/rxcui.json?name={encoded}&search=1"
            with urllib.request.urlopen(rxcui_url, timeout=5) as resp:
                rxcui_data = _json.loads(resp.read().decode())

            rxcuis = (
                rxcui_data.get("idGroup", {}).get("rxnormId", [])
            )
            if not rxcuis:
                results.append(f"• {drug_name}: not found in RxNorm database.")
                continue

            rxcui = rxcuis[0]

            # Step 2: get interactions for this RxCUI
            interact_url = (
                f"https://rxnav.nlm.nih.gov/REST/interaction/interaction.json"
                f"?rxcui={rxcui}&sources=DrugBank"
            )
            with urllib.request.urlopen(interact_url, timeout=5) as resp:
                interact_data = _json.loads(resp.read().decode())

            interaction_pairs = interact_data.get("interactionTypeGroup", [])
            if not interaction_pairs:
                results.append(f"• {drug_name} (RxCUI {rxcui}): No significant interactions found in DrugBank.")
                continue

            drug_results = [f"• {drug_name} interactions:"]
            count = 0
            for group in interaction_pairs:
                for itype in group.get("interactionType", []):
                    for pair in itype.get("interactionPair", []):
                        if count >= 5:  # limit results per drug
                            break
                        desc = pair.get("description", "")
                        severity = pair.get("severity", "")
                        drugs_involved = " + ".join(
                            c.get("minConceptItem", {}).get("name", "")
                            for c in pair.get("interactionConcept", [])
                        )
                        if desc:
                            drug_results.append(
                                f"  – {drugs_involved}: {desc}"
                                + (f" [{severity}]" if severity else "")
                            )
                            count += 1

            results.append("\n".join(drug_results))

        except Exception as e:
            results.append(
                f"• {drug_name}: Could not retrieve interaction data (API unavailable). "
                "Use clinical judgment and consult a current drug reference."
            )

    if not results:
        return "No drug interaction data retrieved."

    return (
        "DRUG INTERACTION LOOKUP (NIH RxNav / DrugBank):\n\n"
        + "\n\n".join(results)
        + "\n\nNote: Always verify with a current drug reference and clinical judgment."
    )


# ─── Radiograph Annotation ───────────────────────────────────────────────────────

def _annotate_radiograph(image_bytes: bytes, findings: list) -> bytes:
    """
    Draw clinical annotations on a radiograph using Pillow.

    Finding types and their visual styles:
      caries      → red semi-transparent circle + label
      fracture    → orange dashed line between (x,y) and (x2,y2)
      bone_level  → blue horizontal measurement line + mm label
      pathology   → purple semi-transparent circle + label
    """
    from PIL import Image, ImageDraw, ImageFont
    import io as _io

    img = Image.open(_io.BytesIO(image_bytes)).convert("RGBA")
    w, h = img.size

    # Overlay layer for semi-transparent fills
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_ov = ImageDraw.Draw(overlay)
    # Crisp layer for lines and text
    draw    = ImageDraw.Draw(img)

    # Try to load a font; fall back to default
    try:
        font       = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", max(12, h // 40))
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",      max(10, h // 50))
    except Exception:
        font       = ImageFont.load_default()
        font_small = font

    COLORS = {
        "caries":     (220,  50,  50, 160),   # red, semi-transparent fill
        "fracture":   (255, 140,   0, 220),   # orange
        "bone_level": ( 30, 144, 255, 230),   # blue
        "pathology":  (148,   0, 211, 160),   # purple, semi-transparent fill
    }

    radius = max(14, min(w, h) // 28)   # scale marker size to image

    for f in findings:
        ftype = f.get("type", "pathology")
        label = f.get("label", "")
        fx    = int(f.get("x", 0.5) * w)
        fy    = int(f.get("y", 0.5) * h)
        color = COLORS.get(ftype, COLORS["pathology"])
        solid = color[:3] + (255,)  # fully opaque version for outlines/text

        if ftype == "fracture":
            # Dashed line from (x,y) to (x2,y2)
            fx2 = int(f.get("x2", f.get("x", 0.5) + 0.05) * w)
            fy2 = int(f.get("y2", f.get("y", 0.5)) * h)
            # Simulate dashes by drawing short segments
            import math
            length = math.hypot(fx2 - fx, fy2 - fy)
            steps  = max(1, int(length / 12))
            for seg in range(steps):
                if seg % 2 == 0:
                    sx1 = int(fx + (fx2 - fx) * seg / steps)
                    sy1 = int(fy + (fy2 - fy) * seg / steps)
                    sx2 = int(fx + (fx2 - fx) * (seg + 1) / steps)
                    sy2 = int(fy + (fy2 - fy) * (seg + 1) / steps)
                    draw.line([(sx1, sy1), (sx2, sy2)], fill=solid, width=3)
            # Label at midpoint
            mx, my = (fx + fx2) // 2, (fy + fy2) // 2
            draw.text((mx + 4, my - 14), label, fill=solid, font=font_small)

        elif ftype == "bone_level":
            # Horizontal measurement line with end ticks + label
            fx2 = int(f.get("x2", min(1.0, f.get("x", 0.5) + 0.12)) * w)
            fy2 = int(f.get("y2", f.get("y", 0.5)) * h)
            draw.line([(fx, fy), (fx2, fy2)], fill=solid, width=3)
            tick = radius // 2
            draw.line([(fx,  fy - tick), (fx,  fy + tick)], fill=solid, width=2)
            draw.line([(fx2, fy2 - tick), (fx2, fy2 + tick)], fill=solid, width=2)
            meas = f.get("measurement", label)
            draw.text((min(fx, fx2), min(fy, fy2) - 18), meas, fill=solid, font=font)

        else:
            # Circle marker (caries / pathology)
            bbox = [fx - radius, fy - radius, fx + radius, fy + radius]
            draw_ov.ellipse(bbox, fill=color, outline=solid[:3] + (255,))
            draw_ov.ellipse(  # inner ring for clarity
                [fx - radius + 3, fy - radius + 3, fx + radius - 3, fy + radius - 3],
                outline=(255, 255, 255, 180), width=1,
            )
            # Label above the circle
            draw.text((fx - radius, fy - radius - 18), label, fill=(255, 255, 255, 255), font=font_small)

    # Composite overlay onto image
    img = Image.alpha_composite(img, overlay).convert("RGB")

    # Legend (bottom-left)
    legend_items = [
        ("● Caries",    (220, 50,  50)),
        ("/ Fracture",  (255, 140,  0)),
        ("— Bone level",(30,  144, 255)),
        ("● Pathology", (148,   0, 211)),
    ]
    present_types = {f.get("type") for f in findings}
    legend_items  = [(lbl, col) for lbl, col in legend_items
                     if lbl.split()[1].lower().replace(".", "") in present_types
                     or ("bone" in lbl.lower() and "bone_level" in present_types)]
    if legend_items:
        draw_final = ImageDraw.Draw(img)
        lx, ly = 10, h - 10 - len(legend_items) * 20
        for lbl, col in legend_items:
            draw_final.rectangle([lx - 2, ly - 2, lx + 120, ly + 16], fill=(0, 0, 0, 180) if False else (20, 20, 20))
            draw_final.text((lx, ly), lbl, fill=col, font=font_small)
            ly += 20

    out = _io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


# ─── Agent Loop ──────────────────────────────────────────────────────────────────

def run_agent(
    question: str,
    history: list,
    memory_ctx: str,
    faculty_key: str,
    lang: str,
    image_bytes: bytes = None,
    image_media_type: str = "image/jpeg",
    agent_mode: str = "direct",
    clinical_dept: str = "general",
) -> tuple:
    """
    Run the agentic tool-use loop.

    Claude decides:
      - how many times to call search_documents (and with what queries)
      - whether to ask a clarifying question first
      - when to call provide_answer (and whether images are needed)

    Returns:
        (answer, sources, retrieved_nodes, clarification_question)
        - answer: str | None  (None if clarification was requested)
        - sources: list[str]
        - retrieved_nodes: list  (for image extraction)
        - clarification_question: str | None
    """
    from config import CLAUDE_MODEL_COMPLEX

    index = load_index()
    retriever = index.as_retriever(similarity_top_k=20)

    # Build message thread from session history
    messages = []
    for ex in history:
        messages.append({"role": "user",      "content": ex.get("user_ctx", ex["user"])})
        messages.append({"role": "assistant", "content": ex["assistant"]})
    # If an image is attached, send it as a vision content block alongside the question
    if image_bytes:
        import base64 as _b64
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image_media_type,
                        "data": _b64.standard_b64encode(image_bytes).decode("utf-8"),
                    },
                },
                {
                    "type": "text",
                    "text": question,
                },
            ],
        })
    else:
        messages.append({"role": "user", "content": question})

    system = build_system_prompt(lang, memory_ctx, faculty_key, has_image=bool(image_bytes),
                                 agent_mode=agent_mode, clinical_dept=clinical_dept)

    all_sources: list     = []
    retrieved_nodes: list = []
    annotated_image: bytes = None   # set when annotate_image tool is called

    # Select tool list based on agent mode — ADEX gets web search on top
    active_tools = TOOLS_ADEX if agent_mode == "adex" else TOOLS

    # Tool-use loop — continue until provide_answer or ask_clarification is called
    for _iteration in range(10):   # slightly higher cap for ADEX (web search adds turns)
        response = _create_with_retry(
            anthropic_client,
            model=CLAUDE_MODEL_COMPLEX,
            max_tokens=4000,
            system=system,
            tools=active_tools,
            messages=messages,
        )

        # If Claude finished without calling a tool, extract any text and return
        if response.stop_reason == "end_turn":
            text = " ".join(
                b.text for b in response.content if hasattr(b, "text")
            ).strip()
            return text or "I wasn't able to generate a response. Please try again.", all_sources, retrieved_nodes, None, annotated_image

        if response.stop_reason != "tool_use":
            break

        # Append Claude's response to the thread
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        terminal = False

        for block in response.content:
            if not hasattr(block, "type") or block.type != "tool_use":
                continue

            if block.name == "search_documents":
                query      = block.input.get("query", question)
                n          = min(int(block.input.get("n", 8)), 20)
                faculty_tag = block.input.get("faculty_tag")

                nodes = retriever.retrieve(query)

                # Faculty corpus filtering — fall back gracefully if no tagged docs found
                if faculty_tag:
                    tagged = [
                        node for node in nodes
                        if (
                            node.metadata.get("corpus_tag") == faculty_tag
                            or faculty_tag.lower() in node.metadata.get("file_name", "").lower()
                        )
                    ]
                    nodes = tagged if tagged else nodes

                nodes = nodes[:n]
                retrieved_nodes.extend(nodes)

                context = ""
                for node in nodes:
                    fname = node.metadata.get("file_name", "Unknown source")
                    context += f"--- Excerpt from: {fname} ---\n{node.text}\n\n"
                    if node.metadata.get("file_name"):
                        all_sources.append(node.metadata["file_name"])

                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     context or "No relevant materials found for that query.",
                })

            elif block.name == "annotate_image":
                findings = block.input.get("findings", [])
                if image_bytes and findings:
                    try:
                        annotated_image = _annotate_radiograph(image_bytes, findings)
                        result_msg = f"Annotated radiograph generated with {len(findings)} finding(s) marked."
                    except Exception as ann_err:
                        result_msg = f"Annotation could not be rendered: {ann_err}"
                else:
                    result_msg = "No image attached or no findings provided — skipping annotation."
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     result_msg,
                })

            elif block.name == "query_drug_interactions":
                drugs = block.input.get("drugs", [])
                result = _tool_drug_interactions(drugs)
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     result,
                })

            elif block.name == "get_clinical_guideline":
                condition = block.input.get("condition", "")
                procedure = block.input.get("procedure", "")
                result = _tool_clinical_guideline(condition, procedure)
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     result,
                })

            elif block.name == "web_search":
                # Anthropic's native web search — results are returned by the API
                # as a tool_result content block automatically. We just need to pass
                # an acknowledgement back so the loop continues.
                query = block.input.get("query", "")
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     f"Web search executed for: {query}",
                })

            elif block.name == "ask_clarification":
                clarification_q = block.input.get("question", "")
                return None, [], [], clarification_q, None

            elif block.name == "provide_answer":
                answer      = block.input.get("answer", "")
                needs_images = block.input.get("needs_images", True)
                sources     = block.input.get("sources", all_sources)
                all_sources = list(dict.fromkeys(sources + all_sources))
                final_nodes = retrieved_nodes if needs_images else []
                terminal    = True
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     "Answer delivered.",
                })
                messages.append({"role": "user", "content": tool_results})
                return answer, all_sources, final_nodes, None, annotated_image

        if not terminal:
            messages.append({"role": "user", "content": tool_results})

    return "I wasn't able to complete that request. Please try again.", all_sources, retrieved_nodes, None, annotated_image


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
    "<div class='stat-chip stat-chip-lg'><span class='stat-num stat-num-lg'>🦷</span><span class='stat-label stat-label-lg'>DentAI</span></div>"
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
def render_response(assistant_text, sources, images, model="", annotated_image=None):
    """Render one assistant turn: response card + optional image panel + sources."""
    t = _UI[st.session_state.lang]

    # ── Model badge (subtle, for testing / transparency) ─────────────────────
    if model:
        _is_haiku  = "haiku"  in model.lower()
        _badge_icon  = "⚡" if _is_haiku else "🧠"
        _badge_label = "Haiku" if _is_haiku else "Sonnet"
        _badge_color = "rgba(0,200,100,0.15)" if _is_haiku else "rgba(123,94,167,0.2)"
        _badge_text_color = "#00c864" if _is_haiku else "#b89fd8"
        st.markdown(
            f"<div style='text-align:right;margin-bottom:4px;'>"
            f"<span style='font-size:0.65rem;padding:2px 8px;border-radius:20px;"
            f"background:{_badge_color};color:{_badge_text_color};"
            f"font-weight:600;letter-spacing:0.3px;'>{_badge_icon} {_badge_label}</span>"
            f"</div>",
            unsafe_allow_html=True
        )

    # ── Annotated radiograph (shown prominently above the text response) ──
    if annotated_image:
        st.markdown(
            "<div style='margin-bottom:16px;'>"
            "<div style='font-size:0.78rem;font-weight:700;color:#003087;"
            "letter-spacing:0.4px;text-transform:uppercase;margin-bottom:8px;'>"
            "🔬 Annotated Radiograph</div>",
            unsafe_allow_html=True,
        )
        st.image(annotated_image, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

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
        # Show attached image thumbnail if this exchange had one
        _exch_img = (st.session_state.get("exchange_images") or {}).get(str(i))
        if _exch_img:
            _img_col, _ = st.columns([1, 4])
            with _img_col:
                st.image(_exch_img, caption="📎 Attached radiograph", use_container_width=True)
        imgs     = st.session_state.latest_images if i == len(st.session_state.chat_history) - 1 else []
        ann_img  = st.session_state.get("latest_annotated_image") if i == len(st.session_state.chat_history) - 1 else None
        render_response(exchange["assistant"], exchange["sources"], imgs, exchange.get("model", ""), annotated_image=ann_img)


# ─── Pending Clarification Display ───────────────────────────────────────────────
# When the agent needs more info, show its question as an assistant bubble
# so the student knows to answer it in the next chat input.
if "pending_clarification" in st.session_state:
    _clarification_q = st.session_state.pop("pending_clarification")
    st.markdown(
        "<div class='response-card' style='border-left-color:#FDB913;'>"
        "<div class='response-header'>"
        "<span class='response-badge' style='background:linear-gradient(135deg,#7b5ea7,#9b7ec7);'>🤔 Clarification Needed</span>"
        "</div>"
        f"<p style='color:#1e293b;font-size:0.97rem;line-height:1.7;margin:0;'>{_clarification_q}</p>"
        "</div>",
        unsafe_allow_html=True,
    )

# ─── Image upload (compact, above chat input) ────────────────────────────────────
st.markdown("""
<style>
/* Compact image uploader strip */
.upload-strip [data-testid="stFileUploader"] {
    border: 1px dashed rgba(0,48,135,0.25) !important;
    border-radius: 10px !important;
    background: rgba(0,48,135,0.03) !important;
    padding: 4px 8px !important;
}
.upload-strip [data-testid="stFileUploader"] label { display: none !important; }
.upload-strip [data-testid="stFileUploaderDropzone"] {
    padding: 6px 12px !important;
    min-height: unset !important;
}
.upload-strip [data-testid="stFileUploaderDropzoneInstructions"] span {
    font-size: 0.78rem !important;
    color: #94a3b8 !important;
}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div style="background:#fffbeb;border:1px solid rgba(253,185,19,0.5);border-left:4px solid #FDB913;
            border-radius:10px;padding:10px 14px;margin-bottom:10px;display:flex;gap:10px;align-items:flex-start;">
  <span style="font-size:1rem;flex-shrink:0;">⚠️</span>
  <div>
    <div style="font-size:0.82rem;font-weight:700;color:#92400e;margin-bottom:2px;">
      Patient Privacy Notice
    </div>
    <div style="font-size:0.78rem;color:#78350f;line-height:1.5;">
      Do <strong>not</strong> upload radiographs containing patient identifiers
      (name, date of birth, patient ID, or date of service).
      Remove all identifying information before uploading.
      Uploading identifiable patient data may violate HIPAA and NSU's patient privacy policies.
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

st.markdown("<div class='upload-strip'>", unsafe_allow_html=True)
_uploaded_image = st.file_uploader(
    "Attach radiograph",
    type=["png", "jpg", "jpeg"],
    label_visibility="collapsed",
    key="image_uploader",
)
st.markdown("</div>", unsafe_allow_html=True)

# Preview thumbnail if image is attached
if _uploaded_image:
    _prev_col, _ = st.columns([1, 5])
    with _prev_col:
        st.image(_uploaded_image, caption="📎 Attached", use_container_width=True)

# ─── Fixed-bottom chat input (native Streamlit) ───────────────────────────────────
_placeholder = _t["placeholder_followup"] if (_has_history or "pending_original_question" in st.session_state) else _t["placeholder_new"]
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

    # ── If student is answering a clarification, prepend original question ──
    if "pending_original_question" in st.session_state:
        full_question = (
            f"{st.session_state.pending_original_question}\n\n"
            f"[Student clarified: {case_input}]"
        )
        del st.session_state["pending_original_question"]
    else:
        full_question = case_input

    # ── Read attached image if present ──
    _image_bytes      = None
    _image_media_type = "image/jpeg"
    if _uploaded_image is not None:
        _image_bytes = _uploaded_image.getvalue()
        _mime        = _uploaded_image.type or "image/jpeg"
        _image_media_type = _mime if _mime.startswith("image/") else "image/jpeg"

    # ── Build longitudinal memory profile (Haiku-analyzed) ──
    memory_ctx  = analyze_student_memory(st.session_state.current_session_id)
    faculty_key = st.session_state.get("faculty_key", "general")

    try:
        assistant_text, sources, relevant_nodes, clarification_q, annotated_img = run_agent(
            question         = full_question,
            history          = st.session_state.chat_history,
            memory_ctx       = memory_ctx,
            faculty_key      = faculty_key,
            lang             = st.session_state.lang,
            image_bytes      = _image_bytes,
            image_media_type = _image_media_type,
            agent_mode       = st.session_state.get("agent_mode", "direct"),
            clinical_dept    = st.session_state.get("clinical_dept", "general"),
        )
    except Exception as e:
        _loader.empty()
        if "overloaded" in str(e).lower() or getattr(e, "status_code", None) == 529:
            st.warning("⚠️ The AI is experiencing high demand right now. Please wait a moment and try again.")
        else:
            st.error(f"An error occurred: {e}")
        st.stop()

    _loader.empty()

    # ── Agent asked a clarifying question — display it and wait ──
    if clarification_q:
        st.session_state["pending_original_question"] = case_input
        st.session_state["pending_clarification"]     = clarification_q
        st.rerun()

    # ── Store annotated radiograph if produced ──
    st.session_state.latest_annotated_image = annotated_img

    # ── Extract curriculum images only when the agent flagged them as useful ──
    if relevant_nodes:
        page_images, _ = extract_page_images(relevant_nodes, max_images=5)
    else:
        page_images = []
    st.session_state.latest_images = page_images

    # ── Save exchange to history + persist ──
    st.session_state.chat_history.append({
        "user":       case_input,
        "user_ctx":   full_question,
        "assistant":  assistant_text,
        "sources":    sources,
        "model":      "agent/sonnet",
        "timestamp":  datetime.now().isoformat(),
        "has_image":  _image_bytes is not None,
    })
    # Store image bytes in memory only (not persisted — too large for Supabase/JSON)
    if _image_bytes:
        if "exchange_images" not in st.session_state:
            st.session_state.exchange_images = {}
        _exchange_idx = len(st.session_state.chat_history) - 1
        st.session_state.exchange_images[str(_exchange_idx)] = _image_bytes
    save_session(st.session_state.current_session_id, st.session_state.chat_history)

    # ── Log query for faculty insights report ──
    _topics = extract_topics(case_input)
    log_query(_current_user_email(), case_input, _topics)

    st.rerun()


# ─── Sidebar ─────────────────────────────────────────────────────────────────────
with st.sidebar:
    # ── Profile card (cloud only) ────────────────────────────────────────────────
    if IS_CLOUD:
        _profile   = _load_profile()
        _full_name = _profile.get("full_name", "")
        _role      = (_profile.get("role", "") or "").capitalize()
        _year      = _profile.get("program_year", "")
        _email_disp = st.session_state.get("user_email", "")
        _initials  = "".join(w[0].upper() for w in _full_name.split()[:2]) if _full_name else "?"

        _role_colors = {
            "Student": ("#003087", "#FDB913"),
            "Faculty": ("#1a5c2e", "#4ade80"),
            "Admin":   ("#5c1a1a", "#f87171"),
        }
        _role_bg, _role_fg = _role_colors.get(_role, ("#2d3748", "#a0aec0"))

        _year_str = f" · {_year}" if _year else ""
        _name_display = _full_name if _full_name else _email_disp

        st.markdown(
            f"""
            <div style="background:rgba(255,255,255,0.06);border:1px solid rgba(253,185,19,0.25);
                        border-radius:12px;padding:14px 14px 12px;margin-bottom:14px;">
              <div style="display:flex;align-items:center;gap:10px;">
                <div style="width:40px;height:40px;border-radius:50%;
                            background:linear-gradient(135deg,#003087,#0041b3);
                            display:flex;align-items:center;justify-content:center;
                            font-size:0.95rem;font-weight:800;color:#FDB913;
                            border:2px solid rgba(253,185,19,0.4);flex-shrink:0;">
                  {_initials}
                </div>
                <div style="overflow:hidden;">
                  <div style="color:#ffffff;font-size:0.88rem;font-weight:700;
                              white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                    {_name_display}
                  </div>
                  <div style="color:rgba(255,255,255,0.5);font-size:0.72rem;
                              white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                    {_email_disp}
                  </div>
                </div>
              </div>
              <div style="margin-top:10px;display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                <span style="background:{_role_bg};color:{_role_fg};
                             font-size:0.68rem;font-weight:700;letter-spacing:0.5px;
                             text-transform:uppercase;padding:3px 9px;border-radius:20px;">
                  {_role}
                </span>
                {"<span style='color:rgba(255,255,255,0.55);font-size:0.72rem;'>" + _year_str.lstrip(" · ") + "</span>" if _year else ""}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ── Language toggle (top of sidebar) ────────────────────────────────────────
    st.markdown(f"<span class='lang-label'>{_t['lang_label']}</span>", unsafe_allow_html=True)
    if st.button(_t["lang_toggle"], type="primary"):
        st.session_state.lang = "es" if st.session_state.lang == "en" else "en"
        st.rerun()

    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
    if st.button(_t["btn_new_chat"], type="primary"):
        _new_id = new_session_id()
        st.session_state.current_session_id = _new_id
        st.session_state.chat_history       = []
        st.session_state.latest_images      = []
        st.query_params["load_sess"]        = _new_id
        st.rerun()

    # ── Clinical Department Selector ─────────────────────────────────────────────
    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown(
        "<p style='color:#FDB913;font-size:0.85rem;font-weight:700;"
        "letter-spacing:0.3px;margin:0 0 6px 0;'>🏥 Today's Clinic</p>",
        unsafe_allow_html=True,
    )

    _current_dept    = st.session_state.get("clinical_dept", "general")
    _dept_keys       = list(CLINICAL_DEPARTMENTS.keys())
    _dept_labels     = [d["label"] for d in CLINICAL_DEPARTMENTS.values()]
    _dept_idx        = _dept_keys.index(_current_dept)

    # Show active department as a compact highlighted chip
    _active_dept     = CLINICAL_DEPARTMENTS[_current_dept]
    st.markdown(
        f"<div style='background:rgba(253,185,19,0.12);border:1.5px solid rgba(253,185,19,0.55);"
        f"border-radius:8px;padding:7px 11px;margin-bottom:8px;display:flex;align-items:center;gap:7px;'>"
        f"<span style='font-size:1rem;'>{_active_dept['icon']}</span>"
        f"<span style='color:#FDB913;font-size:0.82rem;font-weight:700;'>{_active_dept['short']}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # Dropdown to switch
    _selected_dept_label = st.selectbox(
        "Switch clinic",
        options=_dept_labels,
        index=_dept_idx,
        label_visibility="collapsed",
    )
    _selected_dept = _dept_keys[_dept_labels.index(_selected_dept_label)]
    if _selected_dept != _current_dept:
        st.session_state.clinical_dept = _selected_dept
        st.rerun()

    # ── Learning Mode Selector ───────────────────────────────────────────────────
    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown(
        "<p style='color:#FDB913;font-size:0.85rem;font-weight:700;"
        "letter-spacing:0.3px;margin:0 0 8px 0;'>📚 Learning Mode</p>",
        unsafe_allow_html=True,
    )

    _current_mode = st.session_state.get("agent_mode", "direct")

    for _mk, _mm in AGENT_MODES.items():
        _is_active = (_mk == _current_mode)

        if _is_active:
            # Active mode — gold card, no button
            st.markdown(
                f"<div style='"
                f"background:rgba(253,185,19,0.15);"
                f"border:1.5px solid rgba(253,185,19,0.70);"
                f"border-radius:10px;padding:10px 12px;margin-bottom:6px;'>"
                f"<div style='display:flex;align-items:center;gap:7px;'>"
                f"<span style='font-size:1.05rem;'>{_mm['icon']}</span>"
                f"<span style='color:#FDB913;font-size:0.84rem;font-weight:700;'>{_mm['short']}</span>"
                f"<span style='margin-left:auto;background:rgba(253,185,19,0.25);"
                f"color:#FDB913;font-size:0.62rem;font-weight:700;letter-spacing:0.5px;"
                f"text-transform:uppercase;padding:2px 7px;border-radius:20px;'>Active</span>"
                f"</div>"
                f"<div style='color:rgba(253,185,19,0.80);font-size:0.72rem;"
                f"line-height:1.4;margin-top:4px;padding-left:27px;'>"
                f"{_mm['description']}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
        else:
            # Inactive mode — dimmer card + switch button
            st.markdown(
                f"<div style='"
                f"background:rgba(255,255,255,0.04);"
                f"border:1px solid rgba(255,255,255,0.10);"
                f"border-radius:10px;padding:10px 12px;margin-bottom:4px;'>"
                f"<div style='display:flex;align-items:center;gap:7px;'>"
                f"<span style='font-size:1.05rem;opacity:0.7;'>{_mm['icon']}</span>"
                f"<span style='color:rgba(255,255,255,0.75);font-size:0.84rem;font-weight:600;'>"
                f"{_mm['short']}</span>"
                f"</div>"
                f"<div style='color:rgba(255,255,255,0.45);font-size:0.72rem;"
                f"line-height:1.4;margin-top:3px;padding-left:27px;'>"
                f"{_mm['description']}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
            if st.button(f"Switch to {_mm['short']}", key=f"mode_btn_{_mk}",
                         use_container_width=True):
                st.session_state.agent_mode = _mk
                st.session_state.chat_history        = []
                st.session_state.current_session_id  = new_session_id()
                st.session_state.latest_images       = []
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
        # Scrollable session list
        with st.container(height=280, border=True):
            for sess in reversed(_all_sessions):
                is_active   = sess["id"] == st.session_state.current_session_id
                is_renaming = st.session_state.get("_renaming_id") == sess["id"]
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
                        if st.button(
                            _title_short,
                            key=f"load_{sess['id']}",
                            use_container_width=True,
                        ):
                            st.session_state.current_session_id = sess["id"]
                            st.session_state.chat_history       = load_session(sess["id"])
                            st.session_state.latest_images      = []
                            st.query_params["load_sess"]        = sess["id"]
                            st.rerun()

                with col_menu:
                    with st.popover("⋮", use_container_width=True):
                        if st.button("✏️  Rename", key=f"rename_btn_{sess['id']}", use_container_width=True):
                            st.session_state["_renaming_id"] = sess["id"]
                            st.rerun()
                        if st.button("🗑  Delete", key=f"del_btn_{sess['id']}", use_container_width=True):
                            if is_active:
                                _del_new_id = new_session_id()
                                st.session_state.current_session_id = _del_new_id
                                st.session_state.chat_history       = []
                                st.session_state.latest_images      = []
                                st.query_params["load_sess"]        = _del_new_id
                            delete_session(sess["id"])
                            st.rerun()

    st.markdown("<hr>", unsafe_allow_html=True)

    # ── Faculty selector ─────────────────────────────────────────────────────────
    st.markdown(
        "<p style='color:#FDB913;font-size:0.85rem;font-weight:700;"
        "letter-spacing:0.3px;margin:0 0 8px 0;'>👨‍⚕️ Learn From</p>",
        unsafe_allow_html=True
    )
    _faculty_options = {v["label"]: k for k, v in FACULTY.items()}
    _current_faculty_key = st.session_state.get("faculty_key", "general")
    _current_faculty_label = next(
        (v["label"] for k, v in FACULTY.items() if k == _current_faculty_key),
        FACULTY["general"]["label"]
    )
    _faculty_choice = st.radio(
        "faculty_selector",
        options=list(_faculty_options.keys()),
        index=list(_faculty_options.keys()).index(_current_faculty_label),
        label_visibility="collapsed",
    )
    st.session_state.faculty_key = _faculty_options[_faculty_choice]

    st.markdown("<hr>", unsafe_allow_html=True)

    # ── Model selector ───────────────────────────────────────────────────────────
    st.markdown(
        "<p style='color:#FDB913;font-size:0.85rem;font-weight:700;"
        "letter-spacing:0.3px;margin:0 0 8px 0;'>🤖 AI Model</p>",
        unsafe_allow_html=True
    )
    _model_choice = st.radio(
        "model_selector",
        options=["🔀 Auto", "⚡ Haiku (Fast)", "🧠 Sonnet (Advanced)"],
        index=["🔀 Auto", "⚡ Haiku (Fast)", "🧠 Sonnet (Advanced)"].index(
            st.session_state.get("model_override", "🔀 Auto")
        ),
        label_visibility="collapsed",
    )
    st.session_state.model_override = _model_choice

    st.markdown("<hr>", unsafe_allow_html=True)

    # ── How to use ───────────────────────────────────────────────────────────────
    st.markdown(f"## {_t['sidebar_how']}")
    for num, text in _t["how_steps"]:
        st.markdown(
            f"<div class='sb-step'><div class='sb-num'>{num}</div><div class='sb-text'>{text}</div></div>",
            unsafe_allow_html=True
        )


    # ── Logout (cloud only) ──────────────────────────────────────────────────────
    if IS_CLOUD:
        if st.button("🚪 Sign Out", use_container_width=True, type="primary",
                     key="logout_btn"):
            # Clear auth token from URL and wipe session
            st.query_params.pop("s", None)
            for _k in list(st.session_state.keys()):
                del st.session_state[_k]
            st.session_state.pop("user_profile", None)
            st.rerun()

    st.markdown(
        f"<div class='sb-footer'><p>{_t['sb_footer']}</p></div>",
        unsafe_allow_html=True
    )

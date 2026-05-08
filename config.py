"""
DentAI Configuration
────────────────────
Set MODE in your .env file to switch between environments:

  MODE=local   → runs on your machine with ChromaDB (default)
  MODE=cloud   → runs on Streamlit Cloud with Pinecone + Supabase + Google OAuth

All other modules import from here so the switch is in one place.
"""

import os
from dotenv import load_dotenv

load_dotenv()

MODE = os.getenv("MODE", "local").lower()

IS_LOCAL = MODE == "local"
IS_CLOUD = MODE == "cloud"

# ── Model selection ───────────────────────────────────────────────────────────
# Hybrid routing: simple questions → Haiku, complex clinical → Sonnet.
# Override both with env vars if you want to pin to a specific model.
CLAUDE_MODEL_SIMPLE  = os.getenv("CLAUDE_MODEL_SIMPLE",  "claude-haiku-4-5-20251001")
CLAUDE_MODEL_COMPLEX = os.getenv("CLAUDE_MODEL_COMPLEX", "claude-sonnet-4-5")

# Manual override: set CLAUDE_MODEL in .env to force a single model for all queries.
# Leave unset (or blank) to use hybrid routing (recommended).
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "").strip() or None

# ── Local (ChromaDB) settings ─────────────────────────────────────────────────
CHROMA_PATH       = os.getenv("CHROMA_PATH", "./chroma_db")
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "dental_materials")

# ── Cloud (Pinecone + Supabase) settings ──────────────────────────────────────
# These are read from .env only when MODE=cloud — leave them blank locally.
PINECONE_API_KEY   = os.getenv("PINECONE_API_KEY", "")
PINECONE_INDEX     = os.getenv("PINECONE_INDEX", "dentai")
SUPABASE_URL       = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY       = os.getenv("SUPABASE_KEY", "")
GOOGLE_CLIENT_ID   = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

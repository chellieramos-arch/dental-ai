"""
DentAI — Per-School Configuration
─────────────────────────────────
Every school-specific value lives here, driven by environment variables with
NSU defaults so the existing NSU deployment behaves identically with no env
changes.

To deploy a NEW school: set these env vars on its App Service (or .env) —
no code changes. See provision_school.sh.

  SCHOOL_ID              short slug, used for subdomain + resource names (e.g. "nsu")
  SCHOOL_NAME            full program name shown in UI (e.g. "NSU College of Dental Medicine")
  SCHOOL_NAME_ES         Spanish version of SCHOOL_NAME (optional)
  SCHOOL_SHORT           short label/badge text (e.g. "NSU")
  UNIVERSITY_NAME        parent institution (e.g. "Nova Southeastern University")
  PROGRAM_LABEL          subtitle on login (default "Student Study Portal")
  ALLOWED_EMAIL_DOMAINS  comma-separated, each starting with @ (e.g. "@mynsu.nova.edu,@nova.edu")
  EMAIL_PLACEHOLDER      login placeholder (default yourname@<first allowed domain>)
  SEAL_FILE              filename of the school seal image in the repo root (optional)
  APP_URL                canonical app URL for this school (e.g. https://app.dentaiassist.com)

Per-school backend isolation (separate projects per school) stays in the
existing env vars: SUPABASE_URL, SUPABASE_KEY, PINECONE_INDEX, ADMIN_PASSWORD.
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _csv(value: str) -> tuple:
    return tuple(x.strip().lower() for x in value.split(",") if x.strip())


SCHOOL_ID   = os.getenv("SCHOOL_ID", "nsu").strip().lower()
SCHOOL_NAME = os.getenv("SCHOOL_NAME", "NSU College of Dental Medicine").strip()
SCHOOL_SHORT = os.getenv("SCHOOL_SHORT", "NSU").strip()
UNIVERSITY_NAME = os.getenv("UNIVERSITY_NAME", "Nova Southeastern University").strip()
PROGRAM_LABEL = os.getenv("PROGRAM_LABEL", "Student Study Portal").strip()

SCHOOL_NAME_ES = os.getenv("SCHOOL_NAME_ES", "").strip() or (
    "NSU Colegio de Medicina Dental" if SCHOOL_ID == "nsu" else SCHOOL_NAME
)

ALLOWED_EMAIL_DOMAINS = _csv(
    os.getenv("ALLOWED_EMAIL_DOMAINS", "@mynsu.nova.edu,@nova.edu,@health.snova.edu")
)

EMAIL_PLACEHOLDER = os.getenv("EMAIL_PLACEHOLDER", "").strip() or (
    f"yourname{ALLOWED_EMAIL_DOMAINS[0]}" if ALLOWED_EMAIL_DOMAINS else "yourname@school.edu"
)

SEAL_FILE = os.getenv("SEAL_FILE", "nsu_seal.png").strip()

APP_URL = os.getenv("APP_URL", "https://app.dentaiassist.com").strip()

# Human-readable list for error messages: "@a.edu, @b.edu, or @c.edu"
if len(ALLOWED_EMAIL_DOMAINS) > 1:
    DOMAINS_READABLE = ", ".join(ALLOWED_EMAIL_DOMAINS[:-1]) + ", or " + ALLOWED_EMAIL_DOMAINS[-1]
elif ALLOWED_EMAIL_DOMAINS:
    DOMAINS_READABLE = ALLOWED_EMAIL_DOMAINS[0]
else:
    DOMAINS_READABLE = "your school email"


def is_school_email(email: str) -> bool:
    """True if the email ends with one of the school's allowed domains."""
    e = email.strip().lower()
    return any(e.endswith(d) for d in ALLOWED_EMAIL_DOMAINS)

"""
Upload all files from documents/ to Supabase Storage (course-files bucket).
Run once from the dental-ai folder:
    python3 upload_to_storage.py
"""
import os, sys
from pathlib import Path

SUPABASE_URL = "https://xgluymsiqtvbiivznxap.supabase.co"
SERVICE_KEY  = os.getenv("SUPABASE_SERVICE_KEY", "")
BUCKET       = "course-files"
DOCS_DIR     = Path(__file__).parent / "documents"

MIME = {
    ".pdf":  "application/pdf",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt":  "application/vnd.ms-powerpoint",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
}

if not SERVICE_KEY:
    # Fall back to reading from .env
    env_path = Path(__file__).parent / ".env"
    for line in env_path.read_text().splitlines():
        if line.startswith("SUPABASE_SERVICE_KEY="):
            SERVICE_KEY = line.split("=", 1)[1].strip()
            break

if not SERVICE_KEY:
    sys.exit("ERROR: SUPABASE_SERVICE_KEY not found in .env")

try:
    from supabase import create_client
except ImportError:
    sys.exit("ERROR: Run `pip install supabase` first")

sb = create_client(SUPABASE_URL, SERVICE_KEY)

files = sorted(f for f in DOCS_DIR.iterdir() if f.is_file() and f.name != ".DS_Store")
print(f"Found {len(files)} files in documents/\n")

ok = fail = 0
for i, f in enumerate(files, 1):
    ext = f.suffix.lower()
    content_type = MIME.get(ext, "application/octet-stream")
    try:
        raw = f.read_bytes()
        try:
            sb.storage.from_(BUCKET).remove([f.name])
        except Exception:
            pass
        sb.storage.from_(BUCKET).upload(f.name, raw, {"content-type": content_type})
        ok += 1
        print(f"[{i}/{len(files)}] ✓ {f.name}")
    except Exception as e:
        fail += 1
        print(f"[{i}/{len(files)}] ✗ {f.name}: {e}")

print(f"\n{'='*50}")
print(f"Done: {ok} uploaded, {fail} failed")
if fail == 0:
    print("All files are now in Supabase Storage — image extraction will work on Azure.")

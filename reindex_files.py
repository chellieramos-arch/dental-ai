"""
Targeted re-index of specific documents into the image-caption index.

Use when check_image_index.py shows a document's images never made it into
the index (the original build classified everything as skippable, or calls
failed silently). Unlike build_image_index.py this:
  - processes ONLY the files you name (or the crown-prep defaults),
  - prints every keep/skip decision with the caption/reason (verbose by design),
  - uses a classifier prompt that explicitly includes typodont/model photos
    and tooth-preparation photos as keepable,
  - uses Sonnet for classification (per-file image counts are small; accuracy
    over cost here).

Run from the dental-ai folder:
    MODE=cloud python3 reindex_files.py                       # crown-prep defaults
    MODE=cloud python3 reindex_files.py "Some Lecture.pdf"    # specific file(s)
    MODE=cloud python3 reindex_files.py --from-audit          # every file that
        # image_index_audit.csv (from audit_image_index.py) flags as having
        # >=5 candidates but zero indexed images
    MODE=cloud python3 reindex_files.py --from-audit --thin   # also include
        # files with <10% of candidates indexed
"""

import io
import os
import sys
import time
import hashlib
import base64 as b64

from dotenv import load_dotenv
load_dotenv()

from config import IS_CLOUD, PINECONE_API_KEY, PINECONE_INDEX, SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_KEY
from build_image_index import extract_candidates_from_pdf, extract_candidates_from_pptx

PDF_FOLDER   = "./documents"
IMAGE_BUCKET = "course-images"
EMBED_MODEL  = "text-embedding-ada-002"
VISION_MODEL = os.getenv("REINDEX_VISION_MODEL", "claude-sonnet-4-6")

DEFAULT_FILES = [
    "Posterior Tooth Prep Guide.pdf",
    "Prep and Prov.pdf",
    "Prep and Prov 9-x-11.pdf",
    "Prep and Prov3x5.pdf",
    "Prepcheck Tips.pdf",
    "Principles of Tooth Prep (5).pdf",
    "CEREC preparations.pdf",
]

if not IS_CLOUD:
    sys.exit("Run with: MODE=cloud python3 reindex_files.py")


def classify(client, image_bytes, file_name, page_num, max_retries=4):
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(image_bytes))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.thumbnail((700, 700))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=80)
        data = b64.standard_b64encode(buf.getvalue()).decode("utf-8")
    except Exception as e:
        return None, f"thumbnail failed: {e}"

    for attempt in range(max_retries):
        try:
            resp = client.messages.create(
                model=VISION_MODEL,
                max_tokens=300,
                tools=[{
                    "name": "describe_image",
                    "description": "Describe a candidate image from dental course materials and classify it.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "is_teaching_visual": {
                                "type": "boolean",
                                "description": (
                                    "True if a student could learn something clinical by LOOKING at "
                                    "this image: clinical/intraoral photos, radiographs, photos of "
                                    "typodont/model/extracted teeth (including tooth preparations on "
                                    "plastic teeth), anatomical illustrations, labeled diagrams, "
                                    "step-by-step procedure figures, instrument/bur photos. False for "
                                    "decorative art, logos, cover/title graphics, campus photos, "
                                    "clip-art, portraits of people, or screenshots of typed text/tables."
                                ),
                            },
                            "caption": {
                                "type": "string",
                                "description": (
                                    "One or two sentences describing exactly what is depicted — name "
                                    "the image type (clinical photo / typodont photo / radiograph / "
                                    "diagram / chart), the procedure or anatomy shown, tooth numbers "
                                    "if visible, and the view. If is_teaching_visual is False, briefly "
                                    "say why (e.g. 'logo', 'text screenshot')."
                                ),
                            },
                        },
                        "required": ["is_teaching_visual", "caption"],
                    },
                }],
                tool_choice={"type": "tool", "name": "describe_image"},
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": (
                            f"This image is from '{file_name}', page/slide {page_num}, in a dental "
                            f"school's course materials. Describe and classify it."
                        )},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
                    ],
                }],
            )
            for block in resp.content:
                if block.type == "tool_use" and block.name == "describe_image":
                    return block.input, None
            return None, "no tool_use block in response"
        except Exception as e:
            is_overloaded = getattr(e, "status_code", None) == 529 or "overloaded" in str(e).lower()
            if not is_overloaded or attempt == max_retries - 1:
                return None, f"{type(e).__name__}: {e}"
            time.sleep(2 ** attempt)
    return None, "exhausted retries"


def main():
    import anthropic
    import openai
    from pinecone import Pinecone
    from supabase import create_client

    args = sys.argv[1:]
    if "--from-audit" in args:
        import csv
        include_thin = "--thin" in args
        try:
            with open("image_index_audit.csv") as f:
                rows = list(csv.DictReader(f))
        except FileNotFoundError:
            sys.exit("image_index_audit.csv not found — run audit_image_index.py first.")
        files = []
        for r in rows:
            cand, idx_n = int(r["candidates"]), int(r["indexed"])
            if cand >= 5 and (idx_n == 0 or (include_thin and idx_n < cand * 0.1)):
                files.append(r["file"])
        est = sum(int(r["candidates"]) for r in rows if r["file"] in set(files))
        print(f"--from-audit: {len(files)} file(s), ~{est} vision calls (Sonnet). Ctrl+C now to abort.")
        time.sleep(5)
    else:
        files = [a for a in args if not a.startswith("--")] or DEFAULT_FILES
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), max_retries=3)
    oai = openai.OpenAI()
    pc = Pinecone(api_key=PINECONE_API_KEY)
    index = pc.Index(PINECONE_INDEX)
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY or SUPABASE_KEY)

    # Checkpoint — safe to Ctrl+C between files and re-run without re-billing.
    import json
    PROGRESS = "reindex_progress.json"
    done = set(json.load(open(PROGRESS))) if os.path.exists(PROGRESS) else set()
    already = [f for f in files if f in done]
    if already:
        print(f"skipping {len(already)} file(s) already re-indexed (delete {PROGRESS} to redo)")
    files = [f for f in files if f not in done]

    grand_kept = grand_skipped = 0
    for fname in files:
        path = os.path.join(PDF_FOLDER, fname)
        if not os.path.exists(path):
            print(f"\n=== {fname}: FILE NOT FOUND — skipping")
            continue
        if fname.lower().endswith(".pdf"):
            candidates = extract_candidates_from_pdf(path)
        else:
            candidates = extract_candidates_from_pptx(path)
        print(f"\n=== {fname}: {len(candidates)} candidate(s)")
        if not candidates:
            print("    (no extractable raster images — figures are likely vector-drawn; "
                  "would need full-page rendering to capture, not handled here)")
            continue

        vectors = []
        for c in candidates:
            result, err = classify(client, c["bytes"], fname, c["page"])
            if err or not result:
                print(f"    p.{c['page']} ❌ ERROR: {err}")
                grand_skipped += 1
                continue
            caption = (result.get("caption") or "")[:800]
            if not result.get("is_teaching_visual"):
                print(f"    p.{c['page']} ⏭️  skip: {caption[:90]}")
                grand_skipped += 1
                continue

            storage_key = (
                f"{hashlib.md5(fname.encode()).hexdigest()[:10]}_p{c['page']}_"
                f"{hashlib.md5(c['bytes']).hexdigest()[:8]}.{c['ext']}"
            )
            try:
                try:
                    sb.storage.from_(IMAGE_BUCKET).remove([storage_key])
                except Exception:
                    pass
                sb.storage.from_(IMAGE_BUCKET).upload(
                    storage_key, c["bytes"], {"content-type": f"image/{c['ext']}"})
            except Exception as e:
                print(f"    p.{c['page']} ❌ upload failed: {e}")
                grand_skipped += 1
                continue
            try:
                emb = oai.embeddings.create(input=caption, model=EMBED_MODEL).data[0].embedding
            except Exception as e:
                print(f"    p.{c['page']} ❌ embedding failed: {e}")
                grand_skipped += 1
                continue

            vectors.append({
                "id": f"img::{fname}::{c['page']}::{storage_key}",
                "values": emb,
                "metadata": {
                    "content_type": "image", "file_name": fname,
                    "page_label": str(c["page"]), "caption": caption,
                    "storage_key": storage_key, "width": c["w"], "height": c["h"],
                },
            })
            grand_kept += 1
            print(f"    p.{c['page']} ✅ KEEP: {caption[:90]}")

        if vectors:
            index.upsert(vectors=vectors)
            print(f"    → upserted {len(vectors)} vector(s)")
        done.add(fname)
        json.dump(sorted(done), open(PROGRESS, "w"))

    print(f"\nDONE: {grand_kept} indexed, {grand_skipped} skipped across {len(files)} file(s).")
    print("Verify with: MODE=cloud python3 check_image_index.py")


if __name__ == "__main__":
    main()

"""
Local test for the image vision filter (_select_relevant_images in app.py).

Extracts two known-problematic images from the treatment-planning PDF:
  - p.8  tooth models photo   (borderline — fine either way)
  - p.22 odontogram/charting  (MUST be rejected for a crown prep question)
and runs the real filter against a crown prep question, 3 trials.

Run from the dental-ai folder (uses your .env):
    python3 test_image_filter.py
"""

import io
import os
import sys

from dotenv import load_dotenv
load_dotenv()

PDF = "documents/10-Phase III to V-Treatment Planning-2026-03-11.pdf"
QUESTION = "How do I prepare tooth #19 (mandibular left first molar) for a full crown?"
ANSWER = (
    "Tooth #19 is prepped like #30: occlusal reduction 1.5-2.0mm functional cusps, "
    "1.0-1.5mm non-functional, axial reduction 1.0-1.2mm, chamfer finish line "
    "0.5-1.0mm supragingival..."
)
TRIALS = 3


def get_image(page_1based, min_w, max_w):
    """Extract the first embedded image on a page within a width range."""
    import fitz
    doc = fitz.open(PDF)
    page = doc[page_1based - 1]
    for info in page.get_images(full=True):
        base = doc.extract_image(info[0])
        if min_w <= base["width"] <= max_w:
            return base["image"], base["ext"]
    sys.exit(f"couldn't find expected image on p.{page_1based}")


def load_filter_fn():
    """Extract _select_relevant_images from app.py without importing it
    (importing a Streamlit script executes the whole UI)."""
    import ast
    import anthropic
    src = open("app.py").read()
    tree = ast.parse(src)
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "_select_relevant_images")
    fn_src = ast.get_source_segment(src, node)
    ns = {
        "os": os,
        "anthropic_client": anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY")),
    }
    exec(compile(fn_src, "app.py::_select_relevant_images", "exec"), ns)
    return ns["_select_relevant_images"]


def main():
    os.environ.setdefault("MODE", "cloud")
    _select_relevant_images = load_filter_fn()

    models_bytes, _ = get_image(8, 1000, 1500)      # tooth models photo (~1277px)
    chart_bytes, _  = get_image(22, 300, 400)        # odontogram (~335px)

    candidates = [
        {"bytes": models_bytes, "ext": "jpeg", "caption": "📄 10-Phase III...pdf · p.8"},
        {"bytes": chart_bytes,  "ext": "jpeg", "caption": "📄 10-Phase III...pdf · p.22"},
    ]

    failures = 0
    for t in range(TRIALS):
        kept, meta = _select_relevant_images(candidates, QUESTION, ANSWER, max_keep=5)
        kept_pages = ["p.8" if c is candidates[0] else "p.22" for c in kept]
        chart_kept = candidates[1] in kept
        verdict = "❌ FAIL — odontogram kept" if chart_kept else "✅ odontogram rejected"
        failures += chart_kept
        print(f"trial {t + 1}: kept={kept_pages}  model={meta.get('model')}  {verdict}")
        for j in meta.get("judgments", []):
            print(f"    [{j.get('index')}] keep={j.get('keep')} — {j.get('depicts', '')[:100]}")

    print(f"\n{TRIALS - failures}/{TRIALS} trials passed.")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

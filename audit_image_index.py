"""
Corpus-wide audit of the image-caption index.

For every PDF/PPTX in ./documents, compares:
  candidates — extractable raster images (same filters as the indexer)
  indexed    — images actually present in Pinecone for that file

Flags likely victims: files with several candidates but zero indexed images.
Writes image_index_audit.csv and prints a summary.

Run from the dental-ai folder (takes a few minutes — it opens every PDF):
    MODE=cloud python3 audit_image_index.py
"""

import csv
import os
import sys

from dotenv import load_dotenv
load_dotenv()

from config import PINECONE_API_KEY, PINECONE_INDEX
from build_image_index import extract_candidates_from_pdf, extract_candidates_from_pptx

PDF_FOLDER = "./documents"
OUT_CSV = "image_index_audit.csv"


def main():
    from pinecone import Pinecone
    pc = Pinecone(api_key=PINECONE_API_KEY)
    idx = pc.Index(PINECONE_INDEX)
    dim = idx.describe_index_stats().dimension
    zero = [0.0] * dim

    files = sorted(f for f in os.listdir(PDF_FOLDER)
                   if f.lower().endswith((".pdf", ".pptx", ".ppt")))
    rows = []
    for i, fname in enumerate(files):
        path = os.path.join(PDF_FOLDER, fname)
        try:
            if fname.lower().endswith(".pdf"):
                candidates = len(extract_candidates_from_pdf(path))
            else:
                candidates = len(extract_candidates_from_pptx(path))
        except Exception as e:
            print(f"[{i + 1}/{len(files)}] {fname}: extraction error {e}")
            candidates = -1
        try:
            res = idx.query(vector=zero, top_k=1000, include_metadata=False,
                            filter={"content_type": {"$eq": "image"},
                                    "file_name": {"$eq": fname}})
            indexed = len(res.matches)
        except Exception as e:
            print(f"[{i + 1}/{len(files)}] {fname}: pinecone error {e}")
            indexed = -1
        rows.append({"file": fname, "candidates": candidates, "indexed": indexed})
        print(f"[{i + 1}/{len(files)}] {fname}: {candidates} candidates → {indexed} indexed")

    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["file", "candidates", "indexed"])
        w.writeheader()
        w.writerows(rows)

    ok = [r for r in rows if r["candidates"] >= 0 and r["indexed"] >= 0]
    zero_indexed = [r for r in ok if r["candidates"] >= 5 and r["indexed"] == 0]
    thin = [r for r in ok if r["candidates"] >= 5 and 0 < r["indexed"] < r["candidates"] * 0.1]
    no_raster = [r for r in ok if r["candidates"] == 0]

    print("\n───────── SUMMARY ─────────")
    print(f"files audited:                 {len(ok)}")
    print(f"total candidates:              {sum(r['candidates'] for r in ok)}")
    print(f"total indexed:                 {sum(r['indexed'] for r in ok)}")
    print(f"⚠️  ≥5 candidates, ZERO indexed: {len(zero_indexed)} file(s)")
    print(f"⚠️  ≥5 candidates, <10% indexed: {len(thin)} file(s)")
    print(f"ℹ️  no extractable rasters:      {len(no_raster)} file(s) (vector-drawn or text-only)")
    print(f"\nFull detail: {OUT_CSV}")
    if zero_indexed:
        print("\nWorst offenders (zero indexed):")
        for r in sorted(zero_indexed, key=lambda r: -r["candidates"])[:25]:
            print(f"   {r['candidates']:4d} candidates — {r['file']}")


if __name__ == "__main__":
    main()

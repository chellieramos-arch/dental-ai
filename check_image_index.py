"""
Check which images the caption index actually contains for given documents.

Usage (from the dental-ai folder):
    MODE=cloud python3 check_image_index.py "Posterior Tooth Prep Guide.pdf" "Prepcheck Tips.pdf"
    MODE=cloud python3 check_image_index.py            # defaults to the crown-prep docs
"""

import sys

from dotenv import load_dotenv
load_dotenv()

from config import PINECONE_API_KEY, PINECONE_INDEX

DEFAULT_FILES = [
    "Posterior Tooth Prep Guide.pdf",
    "Prep and Prov.pdf",
    "Prep and Prov 9-x-11.pdf",
    "Prep and Prov3x5.pdf",
    "Prepcheck Tips.pdf",
    "Principles of Tooth Prep (5).pdf",
    "CEREC preparations.pdf",
]


def main():
    from pinecone import Pinecone
    files = sys.argv[1:] or DEFAULT_FILES
    pc = Pinecone(api_key=PINECONE_API_KEY)
    idx = pc.Index(PINECONE_INDEX)

    # dummy query vector — we only care about the metadata filter
    dim = idx.describe_index_stats().dimension
    zero = [0.0] * dim

    total = 0
    for f in files:
        res = idx.query(
            vector=zero, top_k=100, include_metadata=True,
            filter={"content_type": {"$eq": "image"}, "file_name": {"$eq": f}},
        )
        n = len(res.matches)
        total += n
        print(f"\n{f}: {n} image(s) indexed")
        for m in res.matches:
            meta = m.metadata or {}
            print(f"   p.{meta.get('page_label')}: {meta.get('caption', '')[:110]}")

    print(f"\nTOTAL: {total} indexed images across {len(files)} file(s).")
    if total == 0:
        print("→ The index has NOTHING from these documents. The build-time classifier")
        print("  skipped them all — re-index these files with a corrected classifier.")


if __name__ == "__main__":
    main()

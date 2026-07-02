"""
DentAI — Document Ingestion Pipeline
──────────────────────────────────────
Reads all PDFs in ./documents and indexes them into the vector store.

  MODE=local  → ChromaDB on disk via llama-index (default)
  MODE=cloud  → Pinecone via direct OpenAI embeddings (no llama-index needed)

Run:
  python3 ingest.py            # local mode
  MODE=cloud python3 ingest.py # cloud mode
"""

import os
from dotenv import load_dotenv

load_dotenv()
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")

from config import (
    IS_LOCAL, IS_CLOUD,
    CHROMA_PATH, CHROMA_COLLECTION,
    PINECONE_API_KEY, PINECONE_INDEX,
)

PDF_FOLDER = "./documents"
EMBED_MODEL = "text-embedding-ada-002"
CHUNK_SIZE  = 1500   # characters per chunk
CHUNK_OVERLAP = 200  # overlap between chunks
BATCH_SIZE  = 96     # Pinecone upsert batch size (max 100)


# ── Text chunking ─────────────────────────────────────────────────────────────
def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list:
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start : start + size])
        start += size - overlap
    return chunks


# ── Local mode (llama-index + ChromaDB) ───────────────────────────────────────
def ingest_local():
    from llama_index.core import VectorStoreIndex, SimpleDirectoryReader, StorageContext
    import chromadb
    from llama_index.vector_stores.chroma import ChromaVectorStore

    print(f"[ingest] Mode: LOCAL  →  ChromaDB at '{CHROMA_PATH}'")
    pdf_files = [f for f in os.listdir(PDF_FOLDER) if f.lower().endswith(".pdf")]
    print(f"[ingest] Reading {len(pdf_files)} PDF(s)...")
    documents = SimpleDirectoryReader(PDF_FOLDER).load_data()
    print(f"[ingest] Loaded {len(documents)} section(s). Indexing...")

    chroma_client     = chromadb.PersistentClient(path=CHROMA_PATH)
    chroma_collection = chroma_client.get_or_create_collection(CHROMA_COLLECTION)
    vector_store      = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context   = StorageContext.from_defaults(vector_store=vector_store)
    VectorStoreIndex.from_documents(documents, storage_context=storage_context)
    print("[ingest] Done! ChromaDB updated.")


# ── Cloud mode (PyMuPDF + OpenAI embeddings + Pinecone directly) ──────────────
def ingest_cloud(wipe_first: bool = True):
    import fitz          # PyMuPDF — already installed
    import openai
    from pinecone import Pinecone

    print(f"[ingest] Mode: CLOUD  →  Pinecone index '{PINECONE_INDEX}'")
    pc    = Pinecone(api_key=PINECONE_API_KEY)
    index = pc.Index(PINECONE_INDEX)
    oai   = openai.OpenAI()

    if wipe_first:
        # This script does a full (re)build of the corpus. Old vectors were
        # indexed without page numbers (whole-doc text flattened before
        # chunking), so a partial upsert would leave stale, page-less chunks
        # mixed in with the new page-aware ones. Wipe and rebuild clean.
        print("[ingest] Clearing existing vectors before rebuild...")
        try:
            index.delete(delete_all=True)
        except Exception as e:
            print(f"[ingest] Warning: could not clear index first ({e}). Continuing anyway.")

    pdf_files = sorted(f for f in os.listdir(PDF_FOLDER) if f.lower().endswith(".pdf"))
    print(f"[ingest] Found {len(pdf_files)} PDF(s).\n")

    total_vectors = 0
    for pdf_file in pdf_files:
        path = os.path.join(PDF_FOLDER, pdf_file)
        print(f"  → {pdf_file}", end="", flush=True)
        try:
            doc = fitz.open(path)
            # Chunk PER PAGE (not the whole doc flattened) so each vector's
            # metadata records which page it came from — this is what lets
            # the student app later pull the correct page's image for a
            # retrieved chunk, instead of always defaulting to page 1.
            tagged_chunks = []   # (page_num, chunk_text)
            for page_idx, page in enumerate(doc):
                page_text = page.get_text()
                if not page_text.strip():
                    continue
                for c in chunk_text(page_text):
                    if c.strip():
                        tagged_chunks.append((page_idx + 1, c))
            doc.close()

            print(f"  ({len(tagged_chunks)} chunks)", end="", flush=True)

            # Embed + upsert in batches
            for i in range(0, len(tagged_chunks), BATCH_SIZE):
                batch = tagged_chunks[i : i + BATCH_SIZE]
                texts = [c for _, c in batch]
                resp  = oai.embeddings.create(input=texts, model=EMBED_MODEL)
                vectors = [
                    {
                        "id":     f"{pdf_file}::{i + j}",
                        "values": item.embedding,
                        "metadata": {
                            "text":        batch[j][1],
                            "file_name":   pdf_file,
                            "chunk_index": i + j,
                            "page_label":  str(batch[j][0]),
                        },
                    }
                    for j, item in enumerate(resp.data)
                ]
                index.upsert(vectors=vectors)
                total_vectors += len(vectors)

            print("  ✅")
        except Exception as e:
            print(f"  ⚠️  skipped ({e})")

    print(f"\n[ingest] Done! {total_vectors} vectors uploaded to Pinecone.")


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    if not os.path.exists(PDF_FOLDER):
        os.makedirs(PDF_FOLDER)
        print(f"[ingest] Created '{PDF_FOLDER}' — add your dental PDFs and run again.")
        return

    pdf_files = [f for f in os.listdir(PDF_FOLDER) if f.lower().endswith(".pdf")]
    if not pdf_files:
        print(f"[ingest] No PDFs found in '{PDF_FOLDER}'. Add files and run again.")
        return

    if IS_CLOUD:
        ingest_cloud()
    elif IS_LOCAL:
        ingest_local()
    else:
        raise ValueError(f"Unknown MODE='{os.getenv('MODE')}'. Expected 'local' or 'cloud'.")


if __name__ == "__main__":
    main()

"""
DentAI — Document Ingestion Pipeline
──────────────────────────────────────
Reads all PDFs in ./documents and indexes them into the vector store.

  MODE=local  → ChromaDB on disk (default, works right now)
  MODE=cloud  → Pinecone (requires PINECONE_API_KEY in .env)

Run:
  python ingest.py
"""

import os
from dotenv import load_dotenv
from llama_index.core import VectorStoreIndex, SimpleDirectoryReader
from llama_index.core import StorageContext

load_dotenv()
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")

from config import (
    IS_LOCAL, IS_CLOUD,
    CHROMA_PATH, CHROMA_COLLECTION,
    PINECONE_API_KEY, PINECONE_INDEX,
)

PDF_FOLDER = "./documents"


def build_storage_context():
    """Return a StorageContext wired to the right vector store for the current MODE."""

    if IS_LOCAL:
        # ── Local: ChromaDB on disk ──────────────────────────────────────────
        import chromadb
        from llama_index.vector_stores.chroma import ChromaVectorStore

        print(f"[ingest] Mode: LOCAL  →  ChromaDB at '{CHROMA_PATH}'")
        chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
        chroma_collection = chroma_client.get_or_create_collection(CHROMA_COLLECTION)
        vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
        return StorageContext.from_defaults(vector_store=vector_store)

    elif IS_CLOUD:
        # ── Cloud: Pinecone ──────────────────────────────────────────────────
        # Uncomment and complete this block when setting up the cloud deployment.
        #
        # from pinecone import Pinecone
        # from llama_index.vector_stores.pinecone import PineconeVectorStore
        #
        # print(f"[ingest] Mode: CLOUD  →  Pinecone index '{PINECONE_INDEX}'")
        # pc = Pinecone(api_key=PINECONE_API_KEY)
        # pinecone_index = pc.Index(PINECONE_INDEX)
        # vector_store = PineconeVectorStore(pinecone_index=pinecone_index)
        # return StorageContext.from_defaults(vector_store=vector_store)

        raise NotImplementedError(
            "Cloud ingestion (Pinecone) is not yet configured.\n"
            "Set MODE=local in your .env to ingest into ChromaDB,\n"
            "or complete the Pinecone block in ingest.py first."
        )

    else:
        raise ValueError(f"Unknown MODE='{os.getenv('MODE')}'. Expected 'local' or 'cloud'.")


def main():
    if not os.path.exists(PDF_FOLDER):
        os.makedirs(PDF_FOLDER)
        print(f"[ingest] Created '{PDF_FOLDER}' — add your dental school PDFs and run again.")
        return

    pdf_files = [f for f in os.listdir(PDF_FOLDER) if f.lower().endswith(".pdf")]
    if not pdf_files:
        print(f"[ingest] No PDFs found in '{PDF_FOLDER}'. Add files and run again.")
        return

    print(f"[ingest] Reading {len(pdf_files)} PDF(s) from '{PDF_FOLDER}'...")
    documents = SimpleDirectoryReader(PDF_FOLDER).load_data()
    print(f"[ingest] Loaded {len(documents)} document section(s).")

    storage_context = build_storage_context()

    print("[ingest] Indexing into vector store (this may take a few minutes)...")
    VectorStoreIndex.from_documents(documents, storage_context=storage_context)

    print("[ingest] Done! Your materials are indexed and ready to search.")


if __name__ == "__main__":
    main()

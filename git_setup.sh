#!/bin/bash
# Run this once from your terminal inside the dental-ai folder:
#   bash git_setup.sh

set -e
cd "$(dirname "$0")"

# Clear any stale lock from the automated setup
rm -f .git/index.lock

# Stage source files only — never PDFs or the chroma DB
git add app.py ingest.py config.py .gitignore git_setup.sh

git commit -m "feat: baseline local app with dual-mode config + hybrid routing

- config.py: MODE flag (local/cloud), CLAUDE_MODEL_SIMPLE/COMPLEX, paths
- app.py:
    * imports branch on IS_LOCAL (chromadb only loaded locally)
    * load_index() mode-aware (Pinecone slot scaffolded)
    * route_model() — Haiku for simple, Sonnet for clinical complexity
    * API call uses route_model() instead of hardcoded model string
- ingest.py: mode-aware (ChromaDB local, Pinecone cloud scaffold)
- .gitignore: excludes .env, chroma_db/, documents/, chat_cache.json

Local mode fully functional. Cloud scaffold in place (flip MODE=cloud
and add Pinecone/Supabase keys when ready to deploy)."

# Create the cloud branch off this baseline
git checkout -b cloud
git checkout main

echo ""
echo "✅ Done! You now have:"
echo "   main  — your stable local version (safe to run anytime)"
echo "   cloud — where we'll build Pinecone + Supabase + Google OAuth"
echo ""
echo "Useful commands:"
echo "   git checkout cloud      switch to cloud branch"
echo "   git checkout main       switch back to local app"
echo "   git log --oneline       see commit history"
echo "   git diff main..cloud    see what differs between branches"
echo ""
echo "⚠️  Your documents/ folder is NOT in git by design."
echo "   Keep a backup on Google Drive, Dropbox, or similar."

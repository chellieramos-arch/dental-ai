# DentAI Cloud Setup Guide

Follow these steps **once** before deploying to Streamlit Community Cloud.
Each section tells you exactly what to click and what value to copy into your `.env` or Streamlit secrets.

---

## 1. Pinecone (Vector Database)

Pinecone replaces your local ChromaDB so all 500 students share the same indexed documents.

1. Go to [https://app.pinecone.io](https://app.pinecone.io) and create a free account.
2. After logging in, click **"Create Index"** and fill in:
   - **Index name:** `dentai`
   - **Dimensions:** `1536`  ← must match OpenAI's text-embedding-ada-002
   - **Metric:** `cosine`
   - **Cloud / Region:** AWS us-east-1 (free tier)
3. Click **API Keys** in the left sidebar. Copy your **API Key**.
4. Add to your `.env`:
   ```
   PINECONE_API_KEY=your-key-here
   PINECONE_INDEX=dentai
   ```
5. Run the ingestion once (cloud mode) to populate Pinecone:
   ```bash
   MODE=cloud python ingest.py
   ```

---

## 2. Supabase (Student Auth + Chat History)

Supabase handles two things: email OTP login (no Azure needed) and per-student chat history.

### 2a. Create the project (if not done already)
1. Go to [https://supabase.com](https://supabase.com) — your project should already exist from setup.
2. In the left sidebar, click **SQL Editor** and run this to create the sessions table (if not done):

```sql
CREATE TABLE chat_sessions (
  id          TEXT PRIMARY KEY,
  user_email  TEXT NOT NULL,
  title       TEXT,
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  exchanges   JSONB DEFAULT '[]'::jsonb
);

CREATE INDEX idx_chat_sessions_user_email ON chat_sessions (user_email);
```

### 2b. Enable Email OTP (student login)
1. In the left sidebar, go to **Authentication → Providers**.
2. Make sure **Email** is enabled (it is by default).
3. Go to **Authentication → URL Configuration**.
4. Set **Site URL** to your Streamlit app URL once you have it:
   `https://your-app-name.streamlit.app`
5. Under **Redirect URLs**, add the same URL.

### 2c. Copy your API keys
1. Go to **Project Settings → API**. Copy:
   - **Project URL** → `SUPABASE_URL`
   - **anon / public key** → `SUPABASE_KEY`
2. Add to your `.env`:
   ```
   SUPABASE_URL=https://xxxx.supabase.co
   SUPABASE_KEY=your-anon-key-here
   ```

> **How login works for students:** They enter their NSU email (`@mynsu.nova.edu`),
> receive a 6-digit code in their Outlook inbox, enter it in the app — done.
> No passwords, no Azure, no IT department required.

---

## 3. Streamlit Community Cloud (Hosting)

1. Go to [https://share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
2. Push your `cloud` branch to GitHub first:
   ```bash
   git remote add origin https://github.com/YOUR_USERNAME/dental-ai.git
   git push -u origin cloud
   ```
3. Click **"New app"**, connect your repo, select the `cloud` branch, and set main file to `app.py`.
4. Under **"Advanced settings → Secrets"**, paste the contents of `.streamlit/secrets.toml`
   (all your API keys go here — never commit secrets.toml to git).
5. Click **Deploy**.
6. Copy the deployed URL (e.g. `https://dentai.streamlit.app`) and paste it into Supabase
   under **Authentication → URL Configuration → Site URL**.

---

## Summary Checklist

- [ ] Pinecone account created + API key copied to `.env`
- [ ] Pinecone index `dentai` created (dimensions: 1536, metric: cosine)
- [ ] `MODE=cloud python ingest.py` run successfully
- [ ] Supabase project created + `chat_sessions` table created
- [ ] Supabase Email provider enabled (Authentication → Providers)
- [ ] Supabase URL + anon key copied to `.env`
- [ ] Code pushed to GitHub `cloud` branch
- [ ] Streamlit Cloud app deployed with secrets pasted in
- [ ] Supabase Site URL updated to match Streamlit app URL

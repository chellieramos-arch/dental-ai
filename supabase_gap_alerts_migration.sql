-- ─────────────────────────────────────────────────────────────────────────────
-- DentAI — gap_alerts table migration
-- Patent Claim 5: Persistent Knowledge Gap Faculty Alert System
--
-- Run in Supabase SQL Editor (Dashboard → SQL Editor → New query)
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. Create the gap_alerts table
create table if not exists public.gap_alerts (
  id             bigint generated always as identity primary key,
  topic          text        not null,
  student_count  int         not null default 0,
  student_emails jsonb       not null default '[]'::jsonb,
  created_at     timestamptz not null default now(),
  resolved       boolean     not null default false
);

-- Ensure topic uniqueness so upserts can delete+insert cleanly
create unique index if not exists gap_alerts_topic_idx on public.gap_alerts (topic);

-- 2. Enable Row Level Security
alter table public.gap_alerts enable row level security;

-- 3. RLS Policies

-- Faculty and admin can read all alerts
create policy "Faculty can read gap alerts"
  on public.gap_alerts for select
  using (
    exists (
      select 1 from public.profiles p
      where p.id = auth.uid()
        and p.role in ('faculty', 'admin')
    )
  );

-- Service role (used by the Python backend via SUPABASE_KEY) can insert/update/delete.
-- The anon / authenticated key from faculty login cannot write — only the server can.
-- (No explicit policy needed for service_role — it bypasses RLS by default in Supabase.)

-- 4. Index for fast resolved-filter queries
create index if not exists gap_alerts_resolved_idx on public.gap_alerts (resolved);

-- ─────────────────────────────────────────────────────────────────────────────
-- Notes:
--   • student_emails stores up to 50 emails per alert (capped in gap_alerts.py).
--   • The Python module delete+inserts on upsert so the created_at timestamp
--     always reflects the last analysis run.
--   • To reset all alerts: DELETE FROM gap_alerts;
-- ─────────────────────────────────────────────────────────────────────────────

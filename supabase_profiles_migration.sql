-- ─────────────────────────────────────────────────────────────────────────────
-- DentAI — profiles table migration
-- Run this in your Supabase SQL editor (Dashboard → SQL Editor → New query)
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. Create the profiles table
create table if not exists public.profiles (
  id           uuid primary key references auth.users(id) on delete cascade,
  email        text not null,
  full_name    text not null,
  school_id    text not null,
  role         text not null check (role in ('student', 'faculty', 'admin')),
  program_year text,          -- e.g. 'D1', 'D2', 'D3', 'D4', 'Faculty', 'Staff'
  created_at   timestamptz not null default now()
);

-- 2. Enable Row Level Security
alter table public.profiles enable row level security;

-- 3. RLS Policies

-- Users can read their own profile
create policy "Users can read own profile"
  on public.profiles for select
  using (auth.uid() = id);

-- Users can insert their own profile (on signup)
create policy "Users can insert own profile"
  on public.profiles for insert
  with check (auth.uid() = id);

-- Users can update their own profile
create policy "Users can update own profile"
  on public.profiles for update
  using (auth.uid() = id);

-- Admins can read all profiles
create policy "Admins can read all profiles"
  on public.profiles for select
  using (
    exists (
      select 1 from public.profiles p
      where p.id = auth.uid() and p.role = 'admin'
    )
  );

-- 4. Index for fast email lookups
create index if not exists profiles_email_idx on public.profiles (email);

-- 5. Aggregate stats for the DentAI master dashboard (operator-only tool).
--    Security definer so the anon key can read COUNTS ONLY — never row data.
create or replace function public.master_stats()
returns json
language sql
stable
security definer
set search_path = public
as $$
  select json_build_object(
    'students', (select count(*) from profiles where role = 'student'),
    'users',    (select count(*) from profiles)
  );
$$;
grant execute on function public.master_stats() to anon;

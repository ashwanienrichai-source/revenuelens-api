-- ═══════════════════════════════════════════════════════════════
-- RevenueLens — Supabase Database Schema
-- Run this in your Supabase SQL editor
-- ═══════════════════════════════════════════════════════════════

-- ── Enable UUID extension ────────────────────────────────────────
create extension if not exists "uuid-ossp";

-- ── Profiles table ───────────────────────────────────────────────
-- Extends Supabase auth.users with subscription + role data
create table if not exists public.profiles (
  id                   uuid primary key references auth.users(id) on delete cascade,
  email                text not null,
  full_name            text,
  role                 text not null default 'user' check (role in ('admin', 'user')),
  subscription_status  text not null default 'free'
                         check (subscription_status in ('free', 'starter', 'pro', 'enterprise')),
  subscription_id      text,
  stripe_customer_id   text unique,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);

-- Auto-create profile on user signup
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer as $$
begin
  insert into public.profiles (id, email, full_name, role)
  values (
    new.id,
    new.email,
    new.raw_user_meta_data->>'full_name',
    case when new.email = 'ashwanivatsalarya@gmail.com' then 'admin' else 'user' end
  );
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

-- ── Datasets table ────────────────────────────────────────────────
create table if not exists public.datasets (
  id           uuid primary key default uuid_generate_v4(),
  user_id      uuid not null references public.profiles(id) on delete cascade,
  name         text not null,
  type         text not null default 'revenue'
                 check (type in ('revenue', 'billing', 'bookings')),
  file_path    text,
  file_size    bigint default 0,
  row_count    integer,
  mapping      jsonb,
  status       text not null default 'uploaded'
                 check (status in ('uploaded', 'mapped', 'analyzed')),
  created_at   timestamptz not null default now()
);

-- ── Analytics runs table ──────────────────────────────────────────
create table if not exists public.analytics_runs (
  id              uuid primary key default uuid_generate_v4(),
  user_id         uuid not null references public.profiles(id) on delete cascade,
  dataset_id      uuid references public.datasets(id) on delete set null,
  module          text not null,
  config          jsonb default '{}',
  status          text not null default 'pending'
                    check (status in ('pending', 'running', 'completed', 'failed')),
  result_url      text,
  error_message   text,
  created_at      timestamptz not null default now(),
  completed_at    timestamptz
);

-- ── Row Level Security ────────────────────────────────────────────
alter table public.profiles        enable row level security;
alter table public.datasets        enable row level security;
alter table public.analytics_runs  enable row level security;

-- Profiles: users can read and update their own
create policy "Users can view own profile"
  on public.profiles for select using (auth.uid() = id);

create policy "Users can update own profile"
  on public.profiles for update using (auth.uid() = id);

-- Admins can view all profiles
create policy "Admins can view all profiles"
  on public.profiles for select
  using (exists (
    select 1 from public.profiles where id = auth.uid() and role = 'admin'
  ));

-- Datasets: users can CRUD their own
create policy "Users can manage own datasets"
  on public.datasets for all using (auth.uid() = user_id);

-- Analytics runs: users can CRUD their own
create policy "Users can manage own analytics runs"
  on public.analytics_runs for all using (auth.uid() = user_id);

-- ── Indexes ───────────────────────────────────────────────────────
create index if not exists idx_profiles_email           on public.profiles(email);
create index if not exists idx_profiles_stripe_customer on public.profiles(stripe_customer_id);
create index if not exists idx_datasets_user_id         on public.datasets(user_id);
create index if not exists idx_analytics_runs_user_id   on public.analytics_runs(user_id);
create index if not exists idx_analytics_runs_dataset   on public.analytics_runs(dataset_id);

-- ── Updated_at trigger ────────────────────────────────────────────
create or replace function public.update_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger update_profiles_updated_at
  before update on public.profiles
  for each row execute procedure public.update_updated_at();

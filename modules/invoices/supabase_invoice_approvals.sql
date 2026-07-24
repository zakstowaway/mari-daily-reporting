-- Invoice approvals — the Supabase-native replacement for the Pipedream route.
--
-- The app (admin-only) upserts a decision here; the Mac poller
-- (xero_process_approvals.py) reads pending rows with the service key and creates
-- the Xero DRAFT bills. Row-level security means the browser holds no secret:
-- only a signed-in admin can write, and the service key (Mac only) bypasses RLS.
--
-- Run once in Supabase → SQL Editor.

create table if not exists public.invoice_approvals (
  ref               text primary key,          -- the invoice/bill number
  supplier          text,
  supplier_key      text,
  invoice_date      date,
  total             numeric(12,2),
  decision          text not null check (decision in ('approve','reject')),
  tracking_category text,
  tracking_option   text,
  lines             jsonb not null default '[]',   -- [{description, amount, tax, account_code}]
  approver          text,
  approver_email    text,
  status            text not null default 'pending',  -- pending | drafted | rejected | needs_review
  xero_invoice_id   text,
  note              text,
  decided_at        timestamptz not null default now(),
  processed_at      timestamptz
);

alter table public.invoice_approvals enable row level security;

-- Admins only (role lives in the JWT's app_metadata, which only the service key
-- can set — so it can't be spoofed from the browser).
create policy invoice_admin_insert on public.invoice_approvals
  for insert to authenticated
  with check ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

create policy invoice_admin_update on public.invoice_approvals
  for update to authenticated
  using ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin')
  with check ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

create policy invoice_admin_select on public.invoice_approvals
  for select to authenticated
  using ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

/**
 * Public front-end config. Supabase's publishable/anon key is meant to ship to
 * the browser; row-level security, not secrecy, protects data. Safe to commit.
 * From: Supabase dashboard -> Project Settings -> API Keys.
 */
export const SUPABASE_URL = "https://fyqhvyvwbedoowjkrxyj.supabase.co";
export const SUPABASE_ANON_KEY = "sb_publishable_Q8pTPc83qHXrRC_UzQzPZQ_wFcqASCV";

// SHG Auth endpoint — a Supabase Edge Function (replaced the dead Pipedream
// worker 2026-07-24). Public — does nothing without a valid Supabase token.
// Routes: /admin/users|invite|role (service key, auto-injected) + /recipes,/prep.
export const WORKER_URL = "https://fyqhvyvwbedoowjkrxyj.supabase.co/functions/v1/shg-auth";

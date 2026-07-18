/**
 * Public front-end config. These values are PUBLIC by design — Supabase's
 * publishable/anon key is meant to ship to the browser; row-level security,
 * not secrecy, is what protects data. Safe to commit.
 *
 * From: Supabase dashboard -> Project Settings -> API Keys.
 */
export const SUPABASE_URL = "https://fyqhvyvwbedoowjkrxyj.supabase.co";
export const SUPABASE_ANON_KEY = "sb_publishable_Q8pTPc83qHXrRC_UzQzPZQ_wFcqASCV";

// The Pipedream SHG Auth endpoint that commits recipes. Public — it does
// nothing without a valid Supabase token. Already deployed:
export const WORKER_URL = "https://eotwefx7cim9jou.m.pipedream.net";

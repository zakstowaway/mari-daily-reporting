/**
 * Public front-end config. Supabase's publishable/anon key is meant to ship to
 * the browser; row-level security, not secrecy, protects data. Safe to commit.
 * From: Supabase dashboard -> Project Settings -> API Keys.
 */
export const SUPABASE_URL = "https://fyqhvyvwbedoowjkrxyj.supabase.co";
export const SUPABASE_ANON_KEY = "sb_publishable_Q8pTPc83qHXrRC_UzQzPZQ_wFcqASCV";

// Pipedream SHG Auth endpoint that commits recipes. Public — does nothing
// without a valid Supabase token.
export const WORKER_URL = "https://eotwefx7cim9jou.m.pipedream.net";

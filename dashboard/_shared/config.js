/**
 * Public front-end config. These two values are PUBLIC by design — Supabase's
 * anon key is meant to ship to the browser; row-level security, not secrecy,
 * is what protects data. Safe to commit.
 *
 * Fill after creating the Supabase project (see modules/auth/README.md):
 *   Supabase dashboard -> Project Settings -> API
 *     Project URL   -> SUPABASE_URL
 *     anon public   -> SUPABASE_ANON_KEY
 */
export const SUPABASE_URL = "";       // e.g. "https://abcdefgh.supabase.co"
export const SUPABASE_ANON_KEY = "";  // the long "anon public" JWT

// The Pipedream SHG Auth endpoint that commits recipes. Public — it does
// nothing without a valid Supabase token. Already deployed:
export const WORKER_URL = "https://eotwefx7cim9jou.m.pipedream.net";

# Auth — self-service, on Supabase

Zak, 2026-07-19: *"passwords and logins ... completely managed by the user ...
forgot password and everything."*

So auth is a managed provider now, not something we hand-roll. **Supabase Auth**
handles signup, sign-in, forgot-password emails, and password changes. Free
tier, works with a static site.

## What lives where

    Supabase     identities + passwords + reset emails.  NOT business data.
    this repo    recipes, COGS, everything that matters.  Unchanged.
    Pipedream    verifies a Supabase token, commits a recipe AS the user.

Passwords are never seen by us. The old admin-provisioned build (a CLI that
hashed passwords into an env var) is in `_archive/auth-admin-provisioned-*`.

## The split that keeps it safe

- **Password / account = the user's.** Signup, forgot, reset — all self-service
  through Supabase.
- **Role = the admin's.** You don't let a chef self-assign admin. Role and venue
  live in the Supabase user's `app_metadata` (only the service key can write it),
  and ride in the token so the recipe endpoint can trust it. Set with
  `modules/auth/set_role.py`.

## Setup — ~10 minutes

**1. Create the project** (only you can — I can't make accounts)
- supabase.com → sign in with GitHub → **New project**, name `stowaway`,
  region near Sydney, set + save a DB password.
- **Authentication → Providers → Email**: on.
- **Authentication → URL Configuration → Site URL**: `https://app.stowawaybar.com`
  and add `https://app.stowawaybar.com/recipes/` to redirect URLs (so the
  reset-link return lands on the recipe page).

**2. Wire the public keys** — Project Settings → API:
- Put **Project URL** and **anon public** into `dashboard/_shared/config.js`
  (`SUPABASE_URL`, `SUPABASE_ANON_KEY`). Both are public; commit them.

**3. Tell the Pipedream worker the same two values** — SHG Auth workflow →
Project → Variables (NOT secret, both public):

    SUPABASE_URL       https://<ref>.supabase.co
    SUPABASE_ANON_KEY  the anon public key

Delete the old `PASSWORDS`, `PEOPLE`, `JWT_SECRET` variables if you added them —
unused now. Then **Deploy**.

**4. Give people roles** — after each person signs up once:

    SUPABASE_URL=... SUPABASE_SERVICE_KEY=... \
      python3 -m modules.auth.set_role sam@stowawaybar.com --role stowfood --venue stowaway

(`service_role` key, from Project Settings → API — it's a secret, passed by env
var, never stored.) Roles: admin, bigchef, stowfood, hgfood, bar, pizza.

## How a chef uses it

1. Open `/recipes/`, **Create account** (name + email + password).
2. Confirm via the Supabase email, sign in.
3. Until you set their role they can sign in but not save — the page says so.
4. **Forgot password?** on the sign-in card → Supabase emails a reset link →
   it returns to `/recipes/#reset` and they set a new one. No admin involved.

## Why this shape

- **No password ever touches our code.** The whole class of hand-rolled
  reset/enumeration/expiry bugs is Supabase's problem, and they've solved it.
- **The Pipedream worker shrank to ~90 lines** — it verifies the token by asking
  Supabase who it is (`/auth/v1/user`), then commits. No hashing, no JWT signing,
  no shared secret to manage.
- **Attribution survives:** the commit author is the signed-in user's email/name,
  so `git log data/recipes/` still says who entered what.

## Still true, still worth knowing

- This gates the app, not the files. `data/*.json` is served publicly by Pages,
  as always. Login stops people *using* the app; it doesn't hide a file from
  someone who knows the URL. Fixing that means leaving Pages.
- The recipe endpoint takes role from the **Supabase token**, never the request
  body — a chef can't claim admin by editing the POST.

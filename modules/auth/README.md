# Auth — one login per person

Zak, 2026-07-17: *"one username per person, so that we can see who's inputting data"*.

Today `stowfood` could be any of six people. This makes logins personal, and
that hands you the audit trail for free: every save is committed **as the
person**, so `git log data/recipes/` says who entered what, forever.

## You do not need a Cloudflare account

I built this as a Cloudflare Worker first. Then I checked what you already have.

**Pipedream is the better answer here, and it's already set up** — workspace
`stowawaybar`, project `Mari Reporting`, already running Node steps, already
writing to this repo. Crucially, `PIPEDREAM_BRIDGE.md` says why it was chosen:

> *"no long-lived PAT stored anywhere (Pipedream handles GitHub OAuth token refresh)"*

The Cloudflare version needs a new account **and** a long-lived GitHub PAT
pasted in as a secret — reintroducing the exact thing this repo deliberately
avoided. So: Pipedream.

Both backends are kept and both are tested against the same hashes, so the
choice isn't a lock-in. `worker/index.js` is there if you ever want it.

## The split — this is the actual fix

    data/people.json          PUBLIC.  username -> name, role, venue, active.
                              Served to the browser. Nothing secret in it.

    .secrets/passwords.json   PRIVATE. username -> pbkdf2 hash. Gitignored.
                              Pasted into Pipedream once. Never committed.

The old `dashboard/users.json` had both in one file and served it to everyone,
salt included.

## Why a server has to exist

The old login ran `sha256(salt + password)` **in the browser**:

- the salt and every hash shipped to the client
- one salt for everyone, so identical passwords gave identical hashes
- a single fast SHA-256, which a GPU tries billions of times a second
- and the check ran in JS, so devtools skipped it entirely

Fine for hiding a read-only dashboard on an obscure URL. Not fine for writing
data that sets your food cost. Now: a random salt per person, 600k PBKDF2
iterations, checked server-side, hashes never leaving Pipedream. The browser
gets a signed token that expires in 12 hours — one shift.

## Setting it up — ~10 minutes, no new accounts

**1. Add your people**

    python3 -m modules.auth.cli add sam  --name "Sam Taylor"  --role stowfood --venue stowaway
    python3 -m modules.auth.cli add jess --name "Jess Nguyen" --role bigchef
    python3 -m modules.auth.cli list

Roles: `admin`, `bigchef`, `stowfood`, `hgfood`, `bar`, `pizza`.
It asks for the password and stores only the hash.

**2. Commit the public half**

    git add data/people.json && git commit -m "People"

`.secrets/` is gitignored. `git status` must never show it.

**3. Build the workflow** — pipedream.com → project **Mari Reporting**

1. New workflow, name it `SHG Auth`
2. Trigger: **HTTP / Webhook Requests** → choose **"Return a custom response"**
3. Add a **Node.js** step, name it `auth`, paste in
   `modules/auth/pipedream/auth_component.js`
4. Connect your **GitHub** account to the step (OAuth — no PAT)
5. **Settings → Environment Variables:**

       PASSWORDS    paste .secrets/passwords.json
       PEOPLE       paste data/people.json
       JWT_SECRET   openssl rand -base64 32

6. **Deploy**, copy the trigger URL

**4. Point the app at it**

`dashboard/_shared/auth.js` → set `WORKER_URL` to the trigger URL, commit.

The Save button turns on by itself. Until then it is disabled **with the reason
on screen** — a shared station password can't say who typed something, so a save
would have no name on it.

**Re-paste `PASSWORDS` whenever you add or remove someone.** The CLI reminds you.

## Worth knowing

- **Rotating `JWT_SECRET` signs everyone out.** That's the panic button.
- **`disable` keeps the person, kills the credential.** Their name stays on
  everything they entered — the point of personal logins.
- **Role comes from the signed token, never the request.** Otherwise anyone
  claims admin by editing the POST. Tested.
- **`stowfood` can't write Harry Gatos recipes.** `bigchef` and `admin` can
  write any venue.
- **Wrong password and unknown user return the same message, and take the same
  time.** Don't leak who has an account.
- **Old passwords can't be migrated** — they're `sha256(shared_salt + password)`,
  so there's nothing to convert. Everyone gets a new one, which was going to
  happen anyway when moving from 6 station logins to personal accounts.
- **This gates the app, not the files.** `data/*.json` — sales included — is
  still served publicly by Pages, exactly as it is today. The login stops people
  *using* the app; it doesn't stop someone who knows a URL reading a file.
  Fixing that means leaving GitHub Pages, which is a much bigger call.

## Tests

    python3 -m pytest modules/auth

The ones that matter are `test_worker_js_verifies_python_hashes` and
`test_pipedream_component_verifies_python_hashes`: Python writes the hashes,
each backend reads them, in two different crypto libraries. If either drifts,
every login fails at once and it looks like a password problem rather than a
code problem. Both pass, which also means the hashes are portable between
backends — choosing a host later isn't a migration.

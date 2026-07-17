# Auth — one login per person

Zak, 2026-07-17: *"one username per person, so that we can see who's inputting data"*.

Today `stowfood` could be any of six people. This fixes that, and the fix gives
you the audit trail for free: every write is committed **as the person**, so
`git log data/recipes/` tells you who entered what, forever.

## The split

    data/people.json          PUBLIC.  username -> name, role, venue, active.
                              Served to the browser. Nothing secret in it.

    .secrets/passwords.json   PRIVATE. username -> pbkdf2 hash.
                              Gitignored. Uploaded to the Worker once.

The old `dashboard/users.json` had both in one file and served it to everyone,
salt included. That is the thing being replaced.

## Why a server exists now

The old login ran `sha256(salt + password)` **in the browser**. That is fine for
hiding a read-only dashboard behind a speed bump. It is not fine for writing
data that decides food cost, because:

- the salt and every hash shipped to the client
- one salt for everyone, so identical passwords gave identical hashes
- one fast SHA-256, which a GPU tries billions of times a second
- and the check ran in JS, so devtools skipped it entirely

Now: a random salt per user, 600k PBKDF2 iterations, verified **server-side**,
hashes never leaving the Worker. The browser gets a signed token that says who
you are and expires in 12 hours (one shift).

## Deploying it — about 15 minutes, free

You need a Cloudflare account. Your DNS is Wix and hosting is GitHub Pages; none
of that changes. The Worker lives on `*.workers.dev` and the app calls it.

**1. Add your people**

    python3 -m modules.auth.cli add sam --name "Sam Taylor" --role stowfood --venue stowaway
    python3 -m modules.auth.cli add jess --name "Jess Nguyen" --role bigchef
    python3 -m modules.auth.cli list

Roles: `admin`, `bigchef`, `stowfood`, `hgfood`, `bar`, `pizza`.
It asks for the password and never stores it — only the hash.

**2. Commit the public half only**

    git add data/people.json && git commit -m "People"

`.secrets/` is gitignored. Check it stays that way: `git status` must never show it.

**3. Deploy the Worker**

    npm install -g wrangler
    cd modules/auth/worker
    wrangler login
    wrangler deploy

**4. Give it its secrets**

    # the password hashes
    wrangler secret put PASSWORDS < ../../../.secrets/passwords.json

    # the token signing key — any long random string, keep it somewhere safe
    openssl rand -base64 32 | wrangler secret put JWT_SECRET

    # a GitHub fine-grained PAT, scoped to this repo, Contents: read/write
    wrangler secret put GITHUB_TOKEN

    # the public identity list, so the Worker knows names and roles
    wrangler secret put PEOPLE < ../../../data/people.json

**5. Point the app at it**

Put the deployed URL in `dashboard/_shared/auth.js` (`WORKER_URL`).

**Re-run steps 4 whenever you add or remove someone** — the Worker holds a copy.
`modules/auth/cli.py` reminds you.

## Things that are true and worth knowing

- **Rotating `JWT_SECRET` signs everyone out.** That is your panic button.
- **`disable` keeps the person, kills the credential.** Their name stays on
  everything they entered — that is the whole point of per-person logins.
- **The role comes from the token, never the request.** Otherwise anyone could
  claim to be an admin by editing the POST body. Verified in the tests.
- **`stowfood` cannot write Harry Gatos recipes.** `bigchef` and `admin` can
  write any venue.
- **Login and unknown-user return the same message and take the same time.**
  Don't leak who has an account.
- **This does not protect the data feeds.** `data/*.json` is served publicly by
  Pages — sales numbers included. It always has been. The login gates the UI,
  not the files. If the numbers themselves need protecting, that is a different
  and much bigger job (it means moving off Pages).

## Tests

    python3 -m pytest modules/auth

The important one is `test_worker_js_verifies_python_hashes`: Python writes the
hashes, the Worker reads them, in two languages with two crypto libraries. If
they ever drift, every login fails at once and it looks like a password problem
rather than a code problem. So it's pinned.

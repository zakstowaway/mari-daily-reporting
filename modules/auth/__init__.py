"""
Auth: one login per person, so every write has a name on it.

    passwords.py    hashing. Shared definition with worker/index.js.
    cli.py          manage people (add/passwd/disable/list)
    worker/         the Cloudflare Worker: the only thing that sees a password,
                    holds the hashes, or can write to the repo.

See README.md for why a server had to exist, and how to deploy it.
"""

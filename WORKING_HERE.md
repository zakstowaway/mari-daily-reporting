# Where this project lives

**Canonical working copy:** `/Users/Shared/ClaudeShared/STOW/Sales Reports/Daily Reporting`
(also reachable as `~/Documents/STOW/Sales Reports/Daily Reporting` — symlink, same folder.
ACLs grant both `zak` and `stowaway` full access.)

As of 2026-07-15 this folder is a **real git clone** of `zakstowaway/mari-daily-reporting`.
It had previously drifted ~3 days stale because it was a plain folder with no
git, so nobody could see it had fallen behind. Don't let that happen again:

    git status      # drift is now visible
    git pull        # before you start
    git push        # when you're done

## Deploying the dashboard

Edit `dashboard/index.html`, then `git commit` + `git push`. GitHub Pages
redeploys automatically (custom domain: app.stowawaybar.com, served from the
`dashboard/` folder as site root — so `dashboard/index.html` is `/`).

There is **no need for the old patch_index_v*.py / push_*.py scripts**. They
existed only because earlier sessions had no persistent clone to push from, so
they mutated a scratch copy and PUT it via the GitHub contents API. They are
archived (untracked) in `_archive/patch-scripts-2026-07/` for reference only.

## Auth

`git push` authenticates via a credential helper configured in `.git/config`
that reads the PAT from `.secrets/github_pat_v2.txt` (gitignored). The token is
not stored in git config itself. If pushes start failing, check that file first.

Note: git needs a safe.directory exception because the folder is owned by `zak`:

    git config --global --add safe.directory "/Users/Shared/ClaudeShared/STOW/Sales Reports/Daily Reporting"

## Known stale copies (do not use)

- `.../local_5ea388ea-.../outputs/push/`  — session scratch, was the only copy
  of v16.4/v16.5 work until 2026-07-15. Now redundant.
- `.../local_5ea388ea-.../outputs/repo/`  — Jul 12 snapshot, no git.
- `../\_daily-reporting-backup-2026-07-15.tgz` — pre-adoption safety snapshot.

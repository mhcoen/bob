# Bob repository consolidation — checklist

Goal: merge mcloop, duplo, orchestra, bob-tools into the bob repo as
`packages/<name>/`, preserving history, re-signed under your key.

## Prereqs (one-time, 5 minutes)

1. `pip install git-filter-repo`.
2. All five repos clean (`git status` empty) and pushed to origin.
3. Make source repo histories read-only at the filesystem level —
   forecloses the entire class of "checklist bug accidentally touches
   the original":
   ```bash
   for repo in mcloop duplo orchestra bob-tools; do
     chmod -R a-w /Users/mhcoen/proj/${repo}/.git
   done
   ```
   Locks `.git/` only; working trees stay writable. `git clone` reads
   `.git/objects/` without needing write access, so step 4 still works.
   Intent is permanent; restore later with `chmod -R u+w` only if you
   ever need to unfreeze.
4. Disk: clone each source repo to a scratch directory under
   `/Users/mhcoen/proj/bob-tools/.scratch/consolidation/` so filter-repo
   operates on a copy, not your working repos.

Commits are not signed in this ecosystem and won't be re-signed by the
consolidation. Rewritten commits preserve `user.name` and `user.email`
from the original commits, which is all the authorship metadata that
matters for a solo project.

## Per-source-repo rewrite (mcloop, duplo, orchestra, bob-tools)

```bash
cd /Users/mhcoen/proj/bob-tools/.scratch/consolidation
git clone /Users/mhcoen/proj/mcloop mcloop-import
cd mcloop-import
git remote remove origin
git filter-repo --to-subdirectory-filter packages/mcloop
cd ..
```

Repeat for `duplo`, `orchestra`, `bob-tools`.

filter-repo writes `.git/filter-repo/commit-map` in each rewritten
clone. Save these — they map old SHA → new SHA, needed for citation
updates.

## Merge into bob

```bash
cd /Users/mhcoen/proj/bob
for repo in mcloop duplo orchestra bob-tools; do
  git remote add ${repo}-import /Users/mhcoen/proj/bob-tools/.scratch/consolidation/${repo}-import
  git fetch ${repo}-import
  git merge --allow-unrelated-histories ${repo}-import/main -m "Import ${repo} as packages/${repo}"
  git remote remove ${repo}-import
done
```

## Workspace setup

5. Create root `pyproject.toml`:
   ```toml
   [tool.uv.workspace]
   members = ["packages/*"]
   ```
6. Each `packages/<name>/pyproject.toml` should already exist from
   the import; verify intra-workspace deps (e.g. mcloop depending on
   bob-tools) resolve. `uv sync` from repo root.
7. Re-tag per-package: for each source repo's existing tags, look up
   the new SHA via the commit-map and `git tag <name>/<old-tag> <new-sha>`.

## Citation update

8. Four SHAs in `bob/design/mcloop-desplit-integration-plan.md`
   (`f2acceb`, `117f3ac`, `7bf086e`, `3cd8165`) need rewriting from
   the commit-maps. One commit, neutral message.
9. Save the four commit-maps under
   `bob/design/consolidation/<name>-commit-map.txt`. Commit and push.

## Verify

10. `git log --oneline --graph --all | head` — five histories
    converging into bob's seed.
11. `uv sync && uv run pytest` — full workspace test suite green.
12. Spot-check: `git log -- packages/mcloop/mcloop/main.py` shows
    historical commits with `--follow`; commit timestamps and
    `user.name`/`user.email` on rewritten commits match the originals.

## Archive

13. On the hosting platform (GitHub/wherever), archive mcloop,
    duplo, orchestra, bob-tools as read-only. Add an `ARCHIVED.md`
    to each pointing at bob and at the relevant commit-map.
14. Keep local clones in `/Users/mhcoen/proj/<name>/` indefinitely.

## Push

15. `git push origin main` and push all new package-prefixed tags.

---

Irreversible step is the filter-repo invocation. Everything before it
is rehearsable on the scratch clones; everything after it is
mechanical. If the re-signing callback doesn't work on the first
attempt, delete the scratch clone and re-clone — the source repos
are untouched.

# Consolidation commit-maps

These files map old-repo SHAs to new-repo SHAs from the
`git filter-repo --to-subdirectory-filter` rewrite that imported
mcloop, duplo, orchestra, and bob-tools into this workspace.

Each line: `<old-sha> <new-sha>`. Lines where new-sha is `0...0`
indicate the original commit was dropped (typically merge commits
that became no-ops after the subdirectory rewrite).

Use these maps to update historical SHA citations in design docs,
commit messages, and external references.

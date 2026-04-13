# Migration design

Migration is intentionally minimal. Only a handful of projects
currently use duplo, and migration code will be exercised a few
times then never again. We don't auto-migrate; we detect the
old layout and tell the user what to do manually.

## Detection

A project needs migration if `.duplo/duplo.json` exists AND
`SPEC.md` either doesn't exist or lacks the new top-matter
marker (the string `"How the pieces fit together:"`).

```python
def needs_migration(target_dir: Path) -> bool:
    duplo_json = target_dir / ".duplo" / "duplo.json"
    spec = target_dir / "SPEC.md"

    if not duplo_json.exists():
        return False
    if spec.exists() and "How the pieces fit together:" in spec.read_text():
        return False
    return True
```

## Behavior

When `duplo` runs in a project that needs migration, print a
message and exit:

```
$ duplo

This project predates the SPEC.md / ref/ redesign. Migrate manually:

  1. Create a ref/ directory:  mkdir ref
  2. Move reference files into ref/. Reference files are images
     (.png .jpg .gif .webp), videos (.mp4 .mov .webm .avi), and
     PDFs that aren't part of your source code.
  3. Run `duplo init` (with no URL or with your product URL)
     to generate a SPEC.md template.
  4. Edit SPEC.md to reflect what was previously inferred:
     - ## Sources: add the URL from .duplo/product.json if any
     - ## References: add an entry for each file in ref/
     - ## Architecture: describe your platform/language stack
  5. Run `duplo` again.

Your existing PLAN.md, .duplo/duplo.json, and source code are
unchanged. Nothing has been moved or modified by duplo.

Exit 1.
```

That's the entire migration story. duplo doesn't move files,
doesn't write SPEC.md, doesn't read product.json to pre-fill
anything. The user does the migration in five minutes the one
time they need to.

## Implementation

A single function in `duplo/main.py` (or a tiny `duplo/migration.py`
if separation feels worthwhile):

```python
def _check_migration(target_dir: Path) -> None:
    if needs_migration(target_dir):
        print(_MIGRATION_MESSAGE)
        sys.exit(1)
```

Called at the start of `main()` for the `duplo` (no subcommand)
path only. The `duplo init` subcommand MUST bypass this check
— the migration message tells users to run `duplo init`, so
init cannot itself trigger the check or the user is stuck in
a loop.

Dispatch order in `main()`:

1. Parse argv.
2. If subcommand is `init`, dispatch to init handler. No
   migration check.
3. Otherwise (no subcommand, `fix`, `investigate`), call
   `_check_migration(Path.cwd())` first. If it exits, the
   subcommand never runs.
4. Proceed with the subcommand.

Tests: one for `needs_migration` (returns true for old layout,
false for new and for non-duplo directories), one for the
exit behavior on the `duplo` path, one confirming `duplo init`
on an old project bypasses the check and proceeds normally.

## What we explicitly are not doing

- Auto-moving files into `ref/`.
- Auto-generating SPEC.md from `duplo.json` state.
- Detecting partial migration states.
- Providing a `--dry-run` flag.
- Handling projects without git differently.
- Migrating the `screenshots/` directory.
- Scanning text files for URLs to migrate.
- Anything resembling a rollback mechanism.

If a user with an existing project finds the manual migration
annoying, they can always delete `.duplo/` and run `duplo init`
fresh. That's a reasonable fallback for the few projects that
exist.

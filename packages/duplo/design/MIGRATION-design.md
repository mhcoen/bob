# Migration design

Migration is intentionally minimal. Only a handful of projects
currently use duplo, and migration code will be exercised a few
times then never again. We don't auto-migrate; we detect the
old layout and tell the user what to do manually.

## Detection

A project needs migration if `.duplo/duplo.json` exists AND
no valid new-format `SPEC.md` is present. "Valid new-format"
is detected by two independent signals, either of which is
sufficient to classify the spec as new-format (and therefore
skip migration):

1. **Marker-string match** (fast path): `SPEC.md` contains the
   string `"How the pieces fit together:"`. This string
   appears in the top-matter comment of `SPEC-template.md` and
   will be present in any SPEC.md created by `duplo init`
   (Phase 4) or by the user copying the template.

2. **Schema-structural match** (fallback): `SPEC.md` contains
   an `## Sources` heading. The old SPEC.md format had no such
   section; its appearance is a strong signal that the file
   was authored against the new schema even if the top-matter
   marker wasn't copied.

The structural fallback matters because Phase 2's migration
message instructs the user to author SPEC.md by hand using
the template as a starting point. A careful user who writes
a minimal new-format SPEC.md from scratch (without copying
the comment marker) would otherwise stay stuck in migration
forever because the first signal fails. The `## Sources`
heading is the lowest-ceremony structural signal the new
format introduces — any genuinely new-format SPEC.md will
have one (even if empty) because it's one of the three input
channels.

```python
def needs_migration(target_dir: Path) -> bool:
    duplo_json = target_dir / ".duplo" / "duplo.json"
    spec = target_dir / "SPEC.md"

    if not duplo_json.exists():
        return False
    if not spec.exists():
        return True
    spec_text = spec.read_text()
    # Either signal is sufficient to declare the spec new-format.
    if "How the pieces fit together:" in spec_text:
        return False
    if re.search(r"^## Sources\s*$", spec_text, re.MULTILINE):
        return False
    return True
```

## Behavior

Migration ships in Phase 2 — *before* `duplo init` exists
(`duplo init` ships in Phase 4). The message text therefore has
two versions:

**Phase 2 message** (when migration ships, init does not yet
exist):

```
$ duplo

This project predates the SPEC.md / ref/ redesign. Migrate manually:

  1. Create a ref/ directory:  mkdir ref
  2. Move reference files into ref/. Reference files are images
     (.png .jpg .gif .webp), videos (.mp4 .mov .webm .avi), and
     PDFs that aren't part of your source code.
  3. Author a SPEC.md by hand. Use SPEC-template.md (in the duplo
     repository) as a starting point. At minimum, fill in:
     - ## Purpose: one or two sentences
     - ## Architecture: your platform/language stack
     - ## Sources: add the URL from .duplo/product.json if any
     - ## References: add an entry for each file you moved to ref/
  4. Run `duplo` again.

Your existing PLAN.md, .duplo/duplo.json, and source code are
unchanged. Nothing has been moved or modified by duplo.

Exit 1.
```

**Phase 4 message upgrade** (after `duplo init` ships,
replace step 3 with):

```
  3. Run `duplo init` (with no URL or with your product URL)
     to generate a SPEC.md template.
  4. Edit SPEC.md to reflect what was previously inferred:
     - ## Sources: add the URL from .duplo/product.json if any
     - ## References: add an entry for each file in ref/
     - ## Architecture: describe your platform/language stack
  5. Run `duplo` again.
```

The Phase 4 update is a one-line change to the message constant
and a renumbering of the trailing steps. Treat it as a Phase 4
task: "update migration message to reference `duplo init`."

That's the entire migration story. duplo doesn't move files,
doesn't write SPEC.md, doesn't read product.json to pre-fill
anything. The user does the migration in five or ten minutes
the one time they need to.

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
path only.

**Phase 2 dispatch order** (no `duplo init` exists yet):

1. Parse argv.
2. If subcommand is `fix` or `investigate`, dispatch to that
   handler without the migration check (those subcommands work
   on already-initialized projects regardless of layout).
3. Otherwise (no subcommand), call `_check_migration(Path.cwd())`
   first. If it exits, the no-subcommand path never runs.
4. Proceed with the no-subcommand path.

**Phase 4 dispatch order update** (after `duplo init` ships):
Add the `init` subcommand at the top of the dispatch and ensure
it bypasses `_check_migration`. The migration message tells
users to run `duplo init`, so init cannot itself trigger the
check or the user is stuck in a loop:

1. Parse argv.
2. If subcommand is `init`, dispatch to init handler. No
   migration check.
3. If subcommand is `fix` or `investigate`, dispatch without
   migration check.
4. Otherwise (no subcommand), call `_check_migration(Path.cwd())`
   first. If it exits, the no-subcommand path never runs.
5. Proceed with the no-subcommand path.

Tests: one for `needs_migration` (returns true for old layout,
false for new and for non-duplo directories), one for the
exit behavior on the `duplo` path. After Phase 4 lands, add
a third test confirming `duplo init` on an old project
bypasses the check and proceeds normally.

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
annoying, they can always delete `.duplo/` and start fresh
(by hand-authoring SPEC.md in Phase 2, or via `duplo init`
once Phase 4 ships). That's a reasonable fallback for the
few projects that exist.

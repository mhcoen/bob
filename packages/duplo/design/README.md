# Duplo design notes archive

This directory holds design documents for work that has been delivered. They
are preserved as historical reference, so the document text may not reflect
current implementation names or every detail of the current code.

For active in-progress design, see the top-level `REDESIGN-overview.md` and
`PIPELINE-design.md`.

## Documents

- **INIT-design.md** — Design for `duplo init`. Delivered. Implementation lives
  in `duplo/init.py` and the init dispatch in `duplo/main.py`.
- **PARSER-design.md** — Design for SPEC.md parsing and role-filtered
  formatters. Delivered. Implementation lives in `duplo/spec_reader.py`. Some
  function names in the design, such as `format_scrapeable_sources`, shipped
  under different names, such as `scrapeable_sources`.
- **MIGRATION-design.md** — Design for old-format to new-format project
  migration. Delivered. Implementation lives in `duplo/migration.py`.
- **DRAFTER-design.md** — Design for a spec-drafter module. Delivered, but the
  implementation lives in `duplo/spec_writer.py`, not in a module named
  `spec_drafter.py` as the design text says. The design text is preserved
  unchanged for historical accuracy; this index supersedes that naming claim.

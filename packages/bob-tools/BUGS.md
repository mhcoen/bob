<!-- bob-plan-format: 1 -->

# bob-tools Bug Backlog

ACCEPTED TRADEOFF (2026-07-06, on record after three verification-audit rounds; deliberate decision, not a work item): a real task line sitting between two BALANCED code-fence markers in a plan file is silently absorbed as fenced example content -- never scheduled, never flagged, though the bytes are preserved verbatim on disk. This is undecidable by any line-based fence rule because the pattern is textually identical to a legitimate fenced example (the indent-bound heuristic tried in commit 07057e31 broke real nested-task files and was reverted in 4281a9a7); the unclosed-fence EOF diagnostic in parser.py catches the unbalanced half loudly, and the shared is_fence_line predicate keeps every counter and gate symmetric so the residual cannot widen. Revisit only if a real plan is bitten in practice.

## Bugs

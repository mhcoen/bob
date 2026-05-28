# Small Design Fixture Spec

## Purpose

A tiny SPEC.md used by ``test_design_integration`` as the seed input
for ``duplo.design.run_iterative_design``. The integration test is a
black-box check of the duplo/orchestra boundary: the orchestra runtime
is mocked so the test does not perform any real LLM call, but the
fixture is real text that exercises the seed-input plumbing end to end.

## Behavior

A counter button that increments on click. A reset button that returns
the count to zero. Persist the count across reloads.

## Architecture

Single HTML file. Vanilla JavaScript. CSS in a style tag.

"""Phase 6 end-to-end integration tests for ``duplo init``.

Each test constructs a fixture project in a tmpdir, runs ``run_init``
programmatically, and asserts on the output state. LLM calls must be
mocked so tests do not depend on claude -p availability or network.
"""

from __future__ import annotations

import argparse


def _make_args(**overrides) -> argparse.Namespace:
    """Build an argparse Namespace matching what ``duplo init`` produces."""
    defaults = {
        "url": None,
        "from_description": None,
        "deep": False,
        "force": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestInitNoArgsProducesTemplate:
    """Per PLAN.md § 'Automated integration tests':
    ``test_init_no_args_produces_template``.
    """

    def test_run_init_no_url_no_description_in_tmpdir(self, tmp_path, capsys, monkeypatch):
        """Run ``run_init`` with no URL, no description in a tmpdir.

        This covers the happy-path scaffolding: ``run_init`` is called
        with both optional inputs absent and an empty working
        directory.  It must return without raising.  Later subtasks
        add the content-level assertions (SPEC.md contents, ref/
        layout, migration status).
        """
        from duplo.init import _REF_README_CONTENT, run_init
        from duplo.migration import needs_migration

        monkeypatch.chdir(tmp_path)

        run_init(_make_args())

        # Drain captured output so it does not bleed into other tests.
        capsys.readouterr()

        spec_path = tmp_path / "SPEC.md"
        assert spec_path.is_file()
        spec_text = spec_path.read_text()
        assert "How the pieces fit together:" in spec_text
        assert "<FILL IN: one or two sentences describing what you're building>" in spec_text
        assert "<FILL IN: language, framework, platform, constraints>" in spec_text

        ref_dir = tmp_path / "ref"
        assert ref_dir.is_dir()
        readme_path = ref_dir / "README.md"
        assert readme_path.is_file()
        assert readme_path.read_text() == _REF_README_CONTENT

        assert needs_migration(tmp_path) is False

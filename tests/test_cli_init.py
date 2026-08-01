"""Tests for `cg init` — bootstrap another project with the bundled skills."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from circuit_graph.cli import _BUNDLED_SKILLS_DIR, _discover_bundled_skills, main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_bundled_skills_dir_exists() -> None:
    """Sanity: the package must actually ship a skills/ tree."""
    assert _BUNDLED_SKILLS_DIR.is_dir(), f"missing skills dir: {_BUNDLED_SKILLS_DIR}"


def test_discover_bundled_skills_finds_multiple() -> None:
    """At least two skills must ship: `cg` plus any embedded-tools skill."""
    skills = _discover_bundled_skills()
    assert len(skills) >= 2, f"expected 2+ bundled skills, found {[s.name for s in skills]}"
    names = [s.name for s in skills]
    assert "cg" in names, "the cg skill must always be bundled"
    for s in skills:
        assert (s / "SKILL.md").is_file(), f"{s}/SKILL.md missing"


def test_init_copies_all_bundled_skills(runner: CliRunner, tmp_path: Path) -> None:
    """Default behaviour: copy every bundled skill to
    <target>/.claude/skills/<name>/."""
    result = runner.invoke(main, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.stderr or result.stdout

    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["installed"] >= 2
    assert payload["skipped"] == 0
    assert payload["failed"] == 0

    # Every discovered skill must be present with SKILL.md on disk.
    for entry in payload["skills"]:
        if entry["status"] == "installed":
            installed_dir = Path(str(entry["target"]))
            assert installed_dir.is_dir(), f"{installed_dir} not created"
            assert (installed_dir / "SKILL.md").is_file(), (
                f"{installed_dir}/SKILL.md missing"
            )


def test_init_skips_existing_skills_without_force(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Re-running on a populated target should skip already-installed skills
    (not error)."""
    runner.invoke(main, ["init", str(tmp_path)])

    # Tamper with one installed skill so we can detect the skip vs overwrite.
    sample = tmp_path / ".claude" / "skills" / "cg" / "SKILL.md"
    assert sample.is_file()
    sample.write_text("tampered", encoding="utf-8")

    result = runner.invoke(main, ["init", str(tmp_path)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["installed"] == 0
    assert payload["skipped"] >= 1
    assert payload["failed"] == 0

    # Tampered file must NOT have been overwritten (since no --force).
    assert sample.read_text(encoding="utf-8") == "tampered"


def test_init_force_overwrites_existing_skills(
    runner: CliRunner, tmp_path: Path
) -> None:
    """--force must replace all existing skill files."""
    runner.invoke(main, ["init", str(tmp_path)])

    sample = tmp_path / ".claude" / "skills" / "cg" / "SKILL.md"
    sample.write_text("tampered", encoding="utf-8")

    result = runner.invoke(main, ["init", str(tmp_path), "--force"])
    assert result.exit_code == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["installed"] >= 2
    assert payload["skipped"] == 0
    assert payload["failed"] == 0

    assert sample.read_text(encoding="utf-8") != "tampered"


def test_init_dry_run_does_not_write(runner: CliRunner, tmp_path: Path) -> None:
    """--dry-run prints the per-skill plan and creates no files."""
    result = runner.invoke(main, ["init", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0, result.stderr or result.stdout

    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["installed"] == 0
    statuses = {e["status"] for e in payload["skills"]}
    assert statuses == {"would_install"}, f"unexpected statuses: {statuses}"

    assert not (tmp_path / ".claude").exists()


def test_init_dry_run_after_install_reports_would_overwrite(
    runner: CliRunner, tmp_path: Path
) -> None:
    """--dry-run on a populated target should mark each skill as
    would_overwrite (no short-circuit, no actual writes)."""
    runner.invoke(main, ["init", str(tmp_path)])

    sample = tmp_path / ".claude" / "skills" / "cg" / "SKILL.md"
    sample.write_text("tampered", encoding="utf-8")

    result = runner.invoke(main, ["init", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    statuses = {e["status"] for e in payload["skills"]}
    assert statuses == {"would_overwrite"}, f"unexpected statuses: {statuses}"
    # Tampered file should still be on disk untouched.
    assert sample.read_text(encoding="utf-8") == "tampered"


def test_init_defaults_to_cwd_when_target_omitted(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting TARGET defaults to the current working directory."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(main, ["init"])
    assert result.exit_code == 0, result.stderr or result.stdout
    assert (tmp_path / ".claude" / "skills" / "cg" / "SKILL.md").is_file()


def test_init_target_must_be_directory(runner: CliRunner, tmp_path: Path) -> None:
    """TARGET must not be an existing file (click.Path file_okay=False)."""
    bad = tmp_path / "not_a_dir"
    bad.write_text("hello", encoding="utf-8")
    result = runner.invoke(main, ["init", str(bad)])
    assert result.exit_code != 0
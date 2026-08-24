"""Command-line boundary tests."""

from __future__ import annotations

from pathlib import Path


def test_cli_requires_public_token(monkeypatch, capsys):
    """Catches an unauthenticated run attempting to contact GitHub."""
    from skill_radar.cli import main

    monkeypatch.delenv("PUBLIC_GITHUB_TOKEN", raising=False)

    assert main(["--root", "."]) == 2
    assert "PUBLIC_GITHUB_TOKEN" in capsys.readouterr().err


def test_cli_does_not_echo_token_on_runtime_failure(monkeypatch, capsys, tmp_path: Path):
    """Catches error output exposing the configured bearer token."""
    import skill_radar.cli as cli

    monkeypatch.setenv("PUBLIC_GITHUB_TOKEN", "never-print-this")
    monkeypatch.setattr(cli, "run_pipeline", lambda root, token: (_ for _ in ()).throw(RuntimeError(f"Authorization: Bearer {token}")))

    assert cli.main(["--root", str(tmp_path)]) == 1
    assert "never-print-this" not in capsys.readouterr().err


def test_cli_reports_safe_github_failure_reason(monkeypatch, capsys, tmp_path: Path):
    """Catches actionable GitHub API failures being hidden behind a generic error."""
    import skill_radar.cli as cli
    from skill_radar.github import GitHubAuthError

    monkeypatch.setenv("PUBLIC_GITHUB_TOKEN", "never-print-this")
    monkeypatch.setattr(
        cli,
        "run_pipeline",
        lambda root, token: (_ for _ in ()).throw(
            GitHubAuthError("GitHub rejected the public-read token")
        ),
    )

    assert cli.main(["--root", str(tmp_path)]) == 1
    error = capsys.readouterr().err
    assert "GitHubAuthError: GitHub rejected the public-read token" in error
    assert "never-print-this" not in error


def test_cli_reports_run_summary(monkeypatch, capsys, tmp_path: Path):
    """Catches a successful command giving no useful publication result."""
    import skill_radar.cli as cli
    from skill_radar.pipeline import RunResult

    monkeypatch.setenv("PUBLIC_GITHUB_TOKEN", "public-token")
    report = tmp_path / "reports" / "today.md"
    monkeypatch.setattr(cli, "run_pipeline", lambda root, token: RunResult(report, 3, 1, False))

    assert cli.main(["--root", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert str(report) in output
    assert "3" in output

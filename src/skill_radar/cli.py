"""Command-line interface for a weekly skill-radar run."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

from .github import GitHubError
from .pipeline import run_pipeline


def main(argv: Sequence[str] | None = None) -> int:
    """Run the radar once, returning a process exit status without leaking secrets."""
    parser = argparse.ArgumentParser(description="Generate the weekly GitHub Skill radar report.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="project root (defaults to the current directory)")
    arguments = parser.parse_args(argv)
    token = os.environ.get("PUBLIC_GITHUB_TOKEN", "")
    if not token.strip():
        print("PUBLIC_GITHUB_TOKEN is required.", file=__import__("sys").stderr)
        return 2
    try:
        result = run_pipeline(arguments.root, token)
    except GitHubError as error:
        print(f"Unable to generate the weekly skill radar report. {type(error).__name__}: {error}", file=__import__("sys").stderr)
        return 1
    except Exception:
        print("Unable to generate the weekly skill radar report.", file=__import__("sys").stderr)
        return 1
    print(
        f"Report: {result.report_path} | collected: {result.collected_count} | "
        f"warnings: {result.warning_count} | baseline: {'yes' if result.is_baseline else 'no'}"
    )
    return 0

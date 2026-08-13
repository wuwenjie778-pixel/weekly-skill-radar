# Weekly GitHub Skill Radar

This project discovers public GitHub repositories containing skills, classifies them, and publishes a weekly Markdown report. It produces four rankings:

- **Overall Top 10**: the highest-ranked discovered skill repositories across all categories.
- **Art and Design Top 10**: repositories matching the broader visual-art or design category.
- **Photoshop Top 5**: repositories whose evidence meets the Photoshop category rules.
- **Illustrator Top 5**: repositories whose evidence meets the Illustrator category rules.

The first successful run creates the baseline used for week-over-week changes. Until then, no ranking data exists; `LATEST.md` deliberately contains only a pre-run notice rather than invented results.

## Deploy on GitHub Actions

1. Create a GitHub personal access token that can read **public repositories only**. Do not grant private-repository access, write access, administration, or any additional scopes. A fine-grained token with public-repository read access is appropriate; an unauthenticated/public-only classic token is also sufficient where GitHub permits it.
2. In the repository, open **Settings → Secrets and variables → Actions**, create a new repository secret named `PUBLIC_GITHUB_TOKEN`, and paste that token as its value.
3. Push this project to GitHub. Open **Actions**, select **Weekly GitHub Skill Ranking**, choose **Run workflow**, and run it once to create the baseline.

The public-read `PUBLIC_GITHUB_TOKEN` is intentionally separate from GitHub Actions' built-in `GITHUB_TOKEN`. The first is supplied only to the ranking generator for GitHub API discovery; the built-in token has the workflow's narrowly scoped `contents: write` permission only so it can commit generated files to this repository. Keeping the tokens separate prevents a token used to read public GitHub data from acquiring repository write authority.

The workflow runs at Monday 00:00 UTC, which is **Monday 08:00 Beijing**. GitHub warns that scheduled workflows can be delayed during heavy load, and schedules run only from the repository's default branch. For public repositories, GitHub can disable scheduled workflows after **60 days** without repository activity; make a repository activity change or run the workflow manually to re-enable it.

## Run locally

Use Python 3.12. From the project root, install the project and test dependencies:

```powershell
python -m pip install -e ".[test]"
$env:PUBLIC_GITHUB_TOKEN = "your-public-read-token"
python -m pytest -v
python -m skill_radar --root .
```

The command requires `PUBLIC_GITHUB_TOKEN`; it does not fall back to an unauthenticated request. Keep the token in your shell environment or a local ignored `.env` workflow, never in a tracked file.

## Configure discovery and classification

- Edit `config/search_queries.yml` to adjust the GitHub code-search queries used to discover candidate skill repositories.
- Edit `config/categories.yml` to change category names, matching terms, weights, and thresholds for art/design, Photoshop, and Illustrator classification.

Run the tests and then generate a new report after configuration changes. Configuration changes affect classification, so previous cached classifications are intentionally not treated as current when rules change.

## Generated files and state

- `LATEST.md` is the current report shown at the repository root.
- `reports/YYYY-MM-DD.md` stores each dated weekly report.
- `data/candidates.json` is the candidate index used to reduce repeated discovery work.
- `data/snapshot.json` is the previous observation state used to calculate changes and preserve resilient collection results.

Do not edit generated report or state files manually. A successful run writes the report and both data files as one transaction; manual changes can make the next comparison misleading or be overwritten.

## Troubleshooting

- **Missing or invalid token:** confirm the secret/environment variable is exactly `PUBLIC_GITHUB_TOKEN`, then create a new public-read token and update the secret.
- **Rate limits:** wait for the GitHub API limit to reset, reduce or refine `config/search_queries.yml`, and use a valid public-read token rather than unauthenticated requests.
- **Fewer professional results than requested:** a Top 10 or Top 5 can contain fewer entries when the discovered public repositories do not meet the configured category evidence and quality threshold. Broaden queries or adjust rules deliberately.
- **Push conflict:** the workflow leaves the conflict visible rather than overwriting history. Rebase or merge the current default-branch changes, then rerun the workflow.
- **Schedule stopped in a public repository:** GitHub may disable it after 60 days without activity. Make an activity change or manually run the workflow on the default branch, then confirm the schedule remains enabled.

## Security

Never commit `.env`, a personal access token, or any other credential. `.env` is ignored locally, but always inspect staged changes before committing. If a token is exposed, revoke or **rotate** it in GitHub immediately, replace the `PUBLIC_GITHUB_TOKEN` Actions secret, and review repository history and workflow logs for exposure.

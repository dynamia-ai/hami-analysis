# GitHub member activity

This package produces replayable, public-only GitHub participation artifacts for configured members. It is an operational proxy for public participation, not a license audit or performance score. Private and internal repository metadata is rejected before artifact persistence; incomplete core sources produce a diagnostic run rather than zero metrics. Commit values are optional GitHub contribution-day context and may be unavailable.

Run locally with `uv run github-member-activity --help`. Collection requires `PUBLIC_GITHUB_TOKEN` (or the configured token environment variable); `--dry-run` and configuration validation never access GitHub.

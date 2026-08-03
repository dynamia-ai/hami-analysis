# GitHub member activity

This package produces replayable, public-only GitHub participation artifacts for configured members. It is an operational proxy for public participation, not a license audit or performance score. Private and internal repository metadata is rejected before artifact persistence; incomplete core sources produce a diagnostic run rather than zero metrics. Commit values are optional GitHub contribution-day context and may be unavailable.

Run locally with `uv run github-member-activity --help`. Collection requires `PUBLIC_GITHUB_TOKEN` (or the configured token environment variable); `--dry-run` and configuration validation never access GitHub.

The scheduled workflow requires an approved member login/node-ID list supplied as the module-root `config.yaml` or the `PUBLIC_GITHUB_MEMBER_ACTIVITY_CONFIG` secret. `config.yaml` is Git-ignored and must not be committed; `config.example.yaml` is schema documentation only and is never used by the workflow. Do not replace the member list with placeholder identities.

All generated evidence, reports, manifests, and diagnostics are written only below the Git-ignored `./output` and `./diagnostics` directories. `output.directory` is intentionally fixed to `./output`; a custom output path could cause generated activity data to be staged in this public repository.

Exit codes are stable: `0` means a publishable run passed verification, `2` means configuration or authentication setup failed before collection, `3` means the stability wait or member applicability produced a diagnostic run, and `4` means collection/core-source, artifact, receipt, or verification failure. A diagnostic run is never interpreted as zero activity.

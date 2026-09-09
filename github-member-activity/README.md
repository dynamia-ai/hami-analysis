# GitHub member activity

This package produces replayable, public-only GitHub participation artifacts for configured members. It is an operational proxy for public participation, not a license audit or performance score. Private and internal repository metadata is rejected before artifact persistence; incomplete core sources produce a diagnostic run rather than zero metrics. Commit values are optional GitHub contribution-day context and may be unavailable. `commit_days` counts distinct repository×contribution-day pairs, so the same contribution-day label in two repositories counts as two.

Run locally with `uv run github-member-activity --help`. Collection requires `PUBLIC_GITHUB_TOKEN` (or the configured token environment variable); `--dry-run` and configuration validation never access GitHub.

Collection requires an approved member login/node-ID list in the module-root `config.yaml`. This file is Git-ignored and must not be committed; `config.example.yaml` is schema documentation only. Do not replace the member list with placeholder identities.

Collection uses bounded `nodes(ids:)` requests (at most 100 IDs per request), including final publication checks. Each batch retains its requested order and cardinality checks; a failed batch makes the source incomplete.

REST Search supplies candidates, while two stable GraphQL discovery snapshots and hydration verify the actual author and canonical event time. A verified public Bot-authored candidate attributed by Search to a human is excluded. REST `created_at` may differ from stable GraphQL `createdAt` by at most one second; larger differences still fail. Created-time searches include a one-second boundary envelope, but emitted events must remain inside the original half-open reporting window.

Review contribution nodes are discovery candidates, not review timestamps. A candidate whose contribution time is outside the window is discarded only after the public PR's complete reviews connection confirms there is no submitted review by the member inside the window. In-window candidates without an eligible review, incomplete pagination, identity conflicts, and unstable snapshots still fail validation.

An account rename requires updating the approved login after checking its stable node ID. Collection does not silently substitute another account or relax the login/node-ID binding.

Published and diagnostic artifacts stay below the Git-ignored `./output` and `./diagnostics` directories. `output.directory` is intentionally fixed to `./output`; a custom output path could cause generated activity data to be staged in this public repository.

Exit codes are stable: `0` means a publishable run passed verification, `2` means configuration or authentication setup failed before collection, `3` means the stability wait or member applicability produced a diagnostic run, and `4` means collection/core-source, artifact, receipt, or verification failure. A diagnostic run is never interpreted as zero activity.

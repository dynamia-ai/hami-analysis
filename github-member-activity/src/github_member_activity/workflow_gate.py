from __future__ import annotations

import argparse

from .manifest import VALIDATOR_REASONS


def evaluate(
    collector_code: int,
    *,
    receipt_present: bool,
    manifest_status: str,
    manifest_publishable: str,
    manifest_reason: str,
    validator_status: str,
    validator_reason: str,
    verify_code: int,
) -> tuple[bool, int]:
    if collector_code == 2:
        return (False, 2) if not receipt_present else (False, 4)
    if not receipt_present:
        return False, 4
    if collector_code == 0:
        valid = (manifest_status, manifest_publishable, manifest_reason, validator_status, validator_reason, verify_code) == ("published", "True", "", "passed", "", 0)
        return valid, 0 if valid else 4
    if collector_code == 3:
        valid = manifest_status == "diagnostic" and manifest_publishable == "False" and validator_status == "not_run" and not validator_reason and manifest_reason in {"stability_gap_not_met", "no_applicable_members"} and verify_code == 3
        return valid, 3 if valid else 4
    if collector_code == 4:
        valid = manifest_status == "diagnostic" and manifest_publishable == "False" and manifest_reason not in {"", "stability_gap_not_met", "no_applicable_members"}
        if valid and manifest_reason == "validation_failed":
            valid = validator_status == "failed" and validator_reason in VALIDATOR_REASONS
        elif valid:
            valid = validator_status == "not_run" and not validator_reason
        valid = valid and verify_code == 3
        return valid, 4 if not valid else 4
    return False, 4


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collector-code", type=int, required=True)
    parser.add_argument("--receipt-present", action="store_true")
    parser.add_argument("--manifest-status", required=True)
    parser.add_argument("--manifest-publishable", required=True)
    parser.add_argument("--manifest-reason", default="")
    parser.add_argument("--validator-status", required=True)
    parser.add_argument("--validator-reason", default="")
    parser.add_argument("--verify-code", type=int, required=True)
    args = parser.parse_args()
    ready, exit_code = evaluate(
        args.collector_code,
        receipt_present=args.receipt_present,
        manifest_status=args.manifest_status,
        manifest_publishable=args.manifest_publishable,
        manifest_reason=args.manifest_reason,
        validator_status=args.validator_status,
        validator_reason=args.validator_reason,
        verify_code=args.verify_code,
    )
    print(f"artifact_ready={'true' if ready else 'false'}")
    print(f"exit_code={exit_code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

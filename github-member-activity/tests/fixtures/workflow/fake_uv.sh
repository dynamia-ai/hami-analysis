#!/usr/bin/env bash
set -u
python_bin="${PYTHON_EXECUTABLE:-python}"
if [ "$2" = "python" ]; then
  shift 2
  exec "$python_bin" "$@"
fi
if [ -n "${FIXTURE_ARGS_LOG:-}" ]; then
  printf '%s\n' "$*" >> "$FIXTURE_ARGS_LOG"
fi
if [ "$2" != "github-member-activity" ]; then
  exit 99
fi
case "$3" in
  validate-config)
    exec "$python_bin" -m github_member_activity validate-config "${@:4}"
    ;;
  collect)
    if [ "${FIXTURE_MODE:-success}" = "collector_crash" ]; then exit 99; fi
    "$python_bin" "$FIXTURE_BUILDER" "${FIXTURE_MODE:-success}"
    case "${FIXTURE_MODE:-diagnostic_success}" in
      collector_2) exit 2 ;;
      published) exit 0 ;;
      safe_diagnostic|validation_failed|collector_4) exit 4 ;;
      *) exit 3 ;;
    esac
    ;;
  verify)
    "$python_bin" -m github_member_activity verify "${@:4}"
    verify_code=$?
    if [ "${FIXTURE_MODE:-}" = "verify_fail" ]; then exit 4; fi
    exit "$verify_code"
    ;;
  *) exit 99 ;;
esac

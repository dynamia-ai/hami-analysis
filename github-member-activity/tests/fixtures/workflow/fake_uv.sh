#!/usr/bin/env bash
set -u
python_bin="${PYTHON_EXECUTABLE:-python}"
if [ "$2" = "python" ]; then
  shift 2
  exec "$python_bin" "$@"
fi
if [ "$2" != "github-member-activity" ]; then
  exit 99
fi
case "$3" in
  validate-config) exit 0 ;;
  collect)
    if [ "${FIXTURE_MODE:-success}" = "collector_crash" ]; then exit 99; fi
    "$python_bin" "$FIXTURE_BUILDER" "${FIXTURE_MODE:-success}"
    if [ "${FIXTURE_MODE:-success}" = "receipt_fault" ]; then exit 3; fi
    exit 3
    ;;
  verify) exit 3 ;;
  *) exit 99 ;;
esac

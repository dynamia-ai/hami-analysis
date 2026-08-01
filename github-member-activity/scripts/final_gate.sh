#!/usr/bin/env bash
set -u

code="${COLLECT_EXIT_CODE:-4}"
case "$code" in
  0|3)
    if [ "${ARTIFACT_READY:-false}" != "true" ] || [ "${UPLOAD_OUTCOME:-}" != "success" ]; then
      exit 4
    fi
    exit "$code"
    ;;
  2)
    if [ "${ARTIFACT_READY:-false}" = "true" ]; then exit 4; fi
    exit 2
    ;;
  4) exit 4;;
  *) exit 4;;
esac

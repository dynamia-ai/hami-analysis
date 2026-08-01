#!/usr/bin/env bash
set -u

code="${COLLECT_EXIT_CODE:-4}"
if [ "${ARTIFACT_READY:-false}" = "true" ] && [ "${UPLOAD_OUTCOME:-}" != "success" ]; then
  code=4
fi
case "$code" in
  0|2|3|4) exit "$code";;
  *) exit 4;;
esac

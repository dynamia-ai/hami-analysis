#!/usr/bin/env bash
set -u
set -o pipefail

write_output() {
  printf '%s\n' "$1" >> "$GITHUB_OUTPUT"
}

stop_with() {
  write_output "artifact_ready=false"
  write_output "exit_code=$1"
  exit 0
}

receipt="$RUNNER_TEMP/github-member-activity-receipt.json"
if [ -e "$receipt" ] || [ -L "$receipt" ]; then
  if [ -L "$receipt" ] || [ ! -f "$receipt" ] || [ "$(stat -c %h "$receipt")" != 1 ]; then
    echo "unsafe stale receipt" >&2
    stop_with 4
  fi
  if ! rm -- "$receipt" || [ -e "$receipt" ] || [ -L "$receipt" ]; then
    echo "stale receipt could not be removed" >&2
    stop_with 4
  fi
fi

if [ ! -f ./config.yaml ]; then
  if [ -z "${PUBLIC_GITHUB_MEMBER_ACTIVITY_CONFIG:-}" ]; then
    echo "production config.yaml or PUBLIC_GITHUB_MEMBER_ACTIVITY_CONFIG is required; config.example.yaml is not executable" >&2
    stop_with 2
  fi
  umask 077
  printf '%s\n' "$PUBLIC_GITHUB_MEMBER_ACTIVITY_CONFIG" > ./config.yaml
fi

uv run github-member-activity validate-config --config ./config.yaml --scheduled
config_code=$?
if [ "$config_code" != 0 ]; then
  stop_with "$config_code"
fi

if [ "${WORKFLOW_EVENT_NAME:-}" = "workflow_dispatch" ]; then
  selected_period="${WORKFLOW_DISPATCH_PERIOD:-}"
elif [ "${WORKFLOW_SCHEDULE:-}" = "15 1 * * 2" ]; then
  selected_period=weekly
elif [ "${WORKFLOW_SCHEDULE:-}" = "30 1 2 * *" ]; then
  selected_period=monthly
else
  echo "unsupported workflow event" >&2
  stop_with 4
fi

uv run github-member-activity collect --config ./config.yaml --period "$selected_period" --scheduled --receipt-path "$receipt"
collector_code=$?
write_output "collector_exit_code=$collector_code"
if [ ! -f "$receipt" ] || [ -L "$receipt" ] || [ "$(stat -c %h "$receipt")" != 1 ]; then
  if [ "$collector_code" = 2 ]; then stop_with 2; fi
  stop_with 4
fi

receipt_values="$(uv run python - <<'PY'
import json, os, pathlib, re, sys
from github_member_activity.canonical import canonical_bytes
path = pathlib.Path(os.environ['RUNNER_TEMP']) / 'github-member-activity-receipt.json'
try:
    raw = path.read_bytes()
    value = json.loads(raw[:-1].decode('utf-8'), object_pairs_hook=lambda pairs: {key: value for key, value in pairs} if len({key for key, _ in pairs}) == len(pairs) else (_ for _ in ()).throw(ValueError()), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if not raw.endswith(b'\n') or canonical_bytes(value) + b'\n' != raw:
        raise ValueError()
    keys = {'schema_version','period_id','period_utc_slug','run_id','run_dir','manifest_sha256'}
    if set(value) != keys or value['schema_version'] != '1.0' or any(not isinstance(value[key], str) or not value[key] for key in keys):
        raise ValueError
    if not re.fullmatch(r'[0-9a-f]{64}', value['manifest_sha256']):
        raise ValueError
    print(value['run_dir'] + '|' + value['manifest_sha256'] + '|' + value['period_id'] + '|' + value['period_utc_slug'] + '|' + value['run_id'])
except Exception:
    sys.exit(1)
PY
)"
if [ "$?" != 0 ]; then stop_with 4; fi
IFS='|' read -r artifact_path manifest_sha period_id period_utc_slug receipt_run_id <<< "$receipt_values"

manifest_state="$(uv run python - "$artifact_path" "$manifest_sha" "$period_id" "$period_utc_slug" "$receipt_run_id" <<'PY'
import hashlib, pathlib, sys
from github_member_activity.config import load_config
from github_member_activity.manifest import verify_directory
path = pathlib.Path(sys.argv[1])
if path.is_absolute() or not path.parts or '..' in path.parts or any(not part or part in {'.', '..'} for part in path.parts):
    raise SystemExit(1)
config = load_config(pathlib.Path('config.yaml'))
published_root = (pathlib.Path.cwd() / config.output.directory).resolve()
diagnostic_root = (pathlib.Path.cwd() / 'diagnostics').resolve()
resolved_path = path.resolve()
try:
    published_rel = resolved_path.relative_to(published_root)
except ValueError:
    published_rel = None
try:
    diagnostic_rel = resolved_path.relative_to(diagnostic_root)
except ValueError:
    diagnostic_rel = None
if not ((published_rel is not None and len(published_rel.parts) == 2) or (diagnostic_rel is not None and len(diagnostic_rel.parts) == 1)):
    raise SystemExit(1)
if any(component.is_symlink() for component in [pathlib.Path(*path.parts[:index]) for index in range(1, len(path.parts) + 1)]):
    raise SystemExit(1)
manifest, _ = verify_directory(path)
if manifest['run_id'] != sys.argv[5]:
    raise SystemExit(1)
if manifest['run_status'] == 'published':
    if published_rel is None or published_rel.parts != (manifest['period']['id'], manifest['run_id']):
        raise SystemExit(1)
elif manifest['run_status'] == 'diagnostic':
    if diagnostic_rel is None or diagnostic_rel.parts != (manifest['run_id'],):
        raise SystemExit(1)
else:
    raise SystemExit(1)
digest = hashlib.sha256((path / 'run-manifest.json').read_bytes()).hexdigest()
if digest != sys.argv[2] or manifest['run_id'] != path.name or manifest['period']['id'] != sys.argv[3] or f"{manifest['period']['start_utc'].replace('-', '').replace(':', '').replace('T', 't').replace('Z', 'z')}--{manifest['period']['end_utc'].replace('-', '').replace(':', '').replace('T', 't').replace('Z', 'z')}" != sys.argv[4]:
    raise SystemExit(1)
print(f"{manifest['run_status']}|{manifest['publishable']}|{manifest['run_reason'] or ''}|{manifest['validator_result']['status']}|{manifest['validator_result']['reason'] or ''}")
PY
)"
if [ "$?" != 0 ]; then stop_with 4; fi
IFS='|' read -r manifest_status manifest_publishable manifest_reason manifest_validator manifest_validator_reason <<< "$manifest_state"
expected_verify_code=3
if [ "$collector_code" = 0 ]; then expected_verify_code=0; fi
pre_gate="$(uv run python -m github_member_activity.workflow_gate --collector-code "$collector_code" --receipt-present --manifest-status "$manifest_status" --manifest-publishable "$manifest_publishable" --manifest-reason "$manifest_reason" --validator-status "$manifest_validator" --validator-reason "$manifest_validator_reason" --verify-code "$expected_verify_code")"
if [ "$?" != 0 ]; then stop_with 4; fi
eval "$pre_gate"
if [ "$artifact_ready" != true ]; then stop_with 4; fi

uv run github-member-activity verify --run-dir "$artifact_path" --expected-manifest-sha256 "$manifest_sha"
verify_code=$?
post_gate="$(uv run python -m github_member_activity.workflow_gate --collector-code "$collector_code" --receipt-present --manifest-status "$manifest_status" --manifest-publishable "$manifest_publishable" --manifest-reason "$manifest_reason" --validator-status "$manifest_validator" --validator-reason "$manifest_validator_reason" --verify-code "$verify_code")"
if [ "$?" != 0 ]; then stop_with 4; fi
eval "$post_gate"
if [ "$artifact_ready" != true ]; then stop_with 4; fi

write_output "artifact_ready=true"
write_output "artifact_path=$artifact_path"
write_output "period_id=$period_id"
write_output "period_utc_slug=$period_utc_slug"
write_output "manifest_sha256=$manifest_sha"
write_output "exit_code=$collector_code"

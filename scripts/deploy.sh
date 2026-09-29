#!/usr/bin/env bash
# Run from an immutable release bundle. Never resets simulator or deletes volumes.
set -Eeuo pipefail
umask 077
root=${1:?usage: deploy.sh /srv/jalani}
release=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
[[ $root =~ ^/[a-zA-Z0-9_./-]+$ ]] || { echo 'Invalid root path' >&2; exit 2; }
[[ -f "$root/.env" ]] || { echo 'Host .env has not been configured' >&2; exit 2; }
mkdir -p "$root/evidence" "$root/backups"
exec 9>"$root/deploy.lock"
flock -w 180 9 || { echo 'Another deployment is active' >&2; exit 2; }
sha=$(python3 "$release/scripts/manifest.py" "$release/manifest.json" --env-output "$release/release.env")
evidence="$root/evidence/$sha"
mkdir -p "$evidence"
previous=$(readlink -f "$root/current" 2>/dev/null || true)
compose() {
  local selected=$1
  shift
  docker compose --project-name jalani --env-file "$root/.env" --env-file "$selected/release.env" \
    -f "$selected/compose.yml" "$@"
}
# Dependencies must already be bootstrapped. An update never recreates the simulator.
compose "$release" exec -T db sh -c 'exec pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  > "$root/backups/$(date -u +%Y%m%dT%H%M%SZ)-$sha.sql"
apply_release() {
  local selected=$1
  local report=$2
  # Monitoring ships with the release, so a bad rules or Alertmanager file fails here rather
  # than at 3am. A rollback target from before monitoring existed must still be deployable,
  # so the service list follows what that release actually contains.
  local services=(backend frontend)
  [[ -f $selected/observability/alertmanager.yml ]] && services+=(alertmanager prometheus)
  compose "$selected" pull "${services[@]}" &&
  python3 "$selected/scripts/verify_images.py" "$selected/manifest.json" &&
  compose "$selected" up -d --no-deps --wait --wait-timeout 120 "${services[@]}" &&
  python3 "$selected/scripts/host_smoke.py" "$root" "$selected" "$report"
}
if ! apply_release "$release" "$evidence/deployed.json"; then
  compose "$release" ps > "$evidence/failed-containers.txt" || true
  # Do not capture resolved docker compose config: it contains secrets.
  if [[ -n $previous && -f "$previous/manifest.json" && $previous != "$release" ]]; then
    echo 'Candidate failed; restoring the previous release.' >&2
    if apply_release "$previous" "$evidence/rollback.json"; then
      echo 'Rollback verified; release remains FAILED.' >&2
    else
      echo 'ROLLBACK FAILED: inspect the host. No data was deleted.' >&2
    fi
  else
    echo 'No different known healthy release to restore; inspect the host.' >&2
  fi
  exit 1
fi
# Promote only after health and SHA verification. Preserve the last different good release.
if [[ -n $previous && -f "$previous/manifest.json" && $previous != "$release" ]]; then
  ln -sfn "$previous" "$root/previous.next"
  mv -Tf "$root/previous.next" "$root/previous"
fi
ln -sfn "$release" "$root/current.next"
mv -Tf "$root/current.next" "$root/current"
cp "$release/manifest.json" "$evidence/manifest.json"
echo "Healthy release recorded: $sha"

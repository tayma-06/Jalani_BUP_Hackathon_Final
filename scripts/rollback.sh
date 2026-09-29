#!/usr/bin/env bash
set -Eeuo pipefail
root=${1:?usage: rollback.sh /srv/jalani}
previous=$(readlink -f "$root/previous")
[[ -f "$previous/manifest.json" ]] || { echo 'No previous healthy release' >&2; exit 2; }
exec bash "$previous/scripts/deploy.sh" "$root"

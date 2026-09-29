#!/bin/sh
# Render the receiver token into the Alertmanager config, then exec Alertmanager.
#
# The config is committed with a placeholder so no credential is ever stored in git.
# Alertmanager cannot read environment variables from its own config file, so the
# substitution happens here. A missing token must stop startup: an Alertmanager that
# runs with the wrong credential accepts and silences every alert, which is worse than
# an alertmanager that refuses to start.
set -eu

config=/etc/alertmanager/alertmanager.yml
rendered=/alertmanager/rendered.yml

if [ -z "${ALERT_WEBHOOK_TOKEN:-}" ]; then
  echo 'ALERT_WEBHOOK_TOKEN is not set; refusing to start Alertmanager.' >&2
  echo 'The receiver would post to the backend with a credential it cannot authenticate.' >&2
  exit 2
fi

# Escape the characters that are special on the right-hand side of an s||| substitution.
token=$(printf '%s' "$ALERT_WEBHOOK_TOKEN" | sed 's/[\\&|]/\\&/g')

sed "s|@@ALERT_WEBHOOK_TOKEN@@|$token|" "$config" >"$rendered"

if grep -q '@@ALERT_WEBHOOK_TOKEN@@' "$rendered"; then
  echo 'Alertmanager config still contains the token placeholder after rendering.' >&2
  exit 2
fi

exec /bin/alertmanager --config.file="$rendered" --storage.path=/alertmanager "$@"

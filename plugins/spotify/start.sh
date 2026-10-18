#!/usr/bin/env bash

if [[ -n "$DISABLE_SPOTIFY" ]]; then
  echo "Spotify is disabled, exiting..."
  exit 0
fi

if [[ -z "$SOLOIST_API_KEY" ]]; then
  echo "SOLOIST_API_KEY is not set; Spotify Connect is unavailable."
  echo "Generate a Soloist API key at https://developer.spotify.com/documentation/soloist"
  echo "and add it as a fleet or device variable; balena restarts this service when it's set."
  # Sleep instead of exiting so restart: on-failure doesn't loop
  exec sleep infinity
fi

DEVICE_NAME=${DEVICE_NAME:-"nts-radio $(echo "$BALENA_DEVICE_UUID" | cut -c -4)"}
SOLOIST_DATA_DIR=/var/lib/soloist
SOLOIST_BIN="$SOLOIST_DATA_DIR/bin/soloist"

case "$SOLOIST_ARCH" in
  aarch64) SOLOIST_ARCHIVE=soloist_release_arm64.tar.gz ;;
  armv7hf|rpi) SOLOIST_ARCHIVE=soloist_release_arm32.tar.gz ;;
  amd64) SOLOIST_ARCHIVE=soloist_release_x86_64.tar.gz ;;
  *) echo "Unsupported architecture: $SOLOIST_ARCH"; exit 1 ;;
esac

# Soloist builds expire after 90 days, so the binary is fetched at runtime
# into the persistent volume and refreshed whenever it reports expiry.
# Download and extract into a temp dir and only move a binary that runs into
# place, so an interrupted download (power loss, reboot) can't leave a
# truncated executable behind.
install_soloist() {
  echo "Downloading $SOLOIST_ARCHIVE..."
  local tmp
  tmp=$(mktemp -d "$SOLOIST_DATA_DIR/.download.XXXXXX") || return 1
  if curl -fsSL --retry 3 "https://soloist-builds.spotifycdn.com/$SOLOIST_ARCHIVE" -o "$tmp/archive.tar.gz" \
    && tar -xzf "$tmp/archive.tar.gz" -C "$tmp" soloist \
    && "$tmp/soloist" --version >/dev/null; then
    mkdir -p "$SOLOIST_DATA_DIR/bin"
    mv -f "$tmp/soloist" "$SOLOIST_BIN"
    rm -rf "$tmp"
  else
    echo "ERROR: Soloist download failed or the binary doesn't run."
    rm -rf "$tmp"
    return 1
  fi
}

mkdir -p "$SOLOIST_DATA_DIR"
rm -rf "$SOLOIST_DATA_DIR"/.download.*
if ! "$SOLOIST_BIN" --version >/dev/null 2>&1; then
  [[ -e "$SOLOIST_BIN" ]] && echo "Installed Soloist binary is broken or expired, reinstalling."
  install_soloist || exit 1
fi

if [[ -n "$SPOTIFY_INITIAL_VOLUME" ]]; then
  set -- "$@" --initial-volume "$SPOTIFY_INITIAL_VOLUME"
fi

echo "Starting Spotify plugin (Soloist)..."
echo "Device name: $DEVICE_NAME"
"$SOLOIST_BIN" --version

run_soloist() {
  "$SOLOIST_BIN" \
    --device-name "$DEVICE_NAME" \
    --api-key "$SOLOIST_API_KEY" \
    --data-dir "$SOLOIST_DATA_DIR" \
    --cache-dir /tmp/soloist-cache \
    --cache-size 100 \
    "$@"
}

run_soloist "$@"
status=$?
if [[ $status -eq 10 ]]; then
  echo "Soloist build expired, fetching a newer one..."
  install_soloist || exit 1
  run_soloist "$@"
  status=$?
fi
exit $status

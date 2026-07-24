#!/usr/bin/env bash

if [[ -n "$SOUND_DISABLE_SPOTIFY" ]]; then
  echo "Spotify is disabled, exiting..."
  exit 0
fi

SOUND_DEVICE_NAME=${SOUND_DEVICE_NAME:-"pirate-radio $(echo "$BALENA_DEVICE_UUID" | cut -c -4)"}
SOUND_SPOTIFY_BITRATE=${SOUND_SPOTIFY_BITRATE:-160}

if [[ -z ${SOUND_SPOTIFY_DISABLE_NORMALISATION+x} ]]; then
  set -- "$@" \
    --enable-volume-normalisation
fi

if [[ -n "$SOUND_SPOTIFY_USERNAME" ]] && [[ -n "$SOUND_SPOTIFY_PASSWORD" ]]; then
  set -- "$@" \
    --username "$SOUND_SPOTIFY_USERNAME" \
    --password "$SOUND_SPOTIFY_PASSWORD"
fi

if [[ -z ${SOUND_SPOTIFY_ENABLE_CACHE+x} ]]; then
  set -- "$@" \
    --disable-audio-cache
fi

echo "Starting Spotify plugin..."
echo "Device name: $SOUND_DEVICE_NAME"
[[ -n "$SOUND_SPOTIFY_USERNAME" ]] && [[ -n "$SOUND_SPOTIFY_PASSWORD" ]] && echo "Using provided credentials for Spotify login."
[[ -n ${SOUND_SPOTIFY_DISABLE_NORMALISATION+x} ]] && echo "Volume normalization disabled."
[[ -n ${SOUND_SPOTIFY_ENABLE_CACHE+x} ]] && echo "Spotify audio cache enabled."

set -- /usr/bin/librespot \
  --backend pulseaudio \
  --name "$SOUND_DEVICE_NAME" \
  --bitrate "$SOUND_SPOTIFY_BITRATE" \
  --cache /var/cache/raspotify \
  --volume-ctrl linear \
  "$@"

exec "$@"

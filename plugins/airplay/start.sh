#!/usr/bin/env sh

if [ -n "$SOUND_DISABLE_AIRPLAY" ]; then
  echo "AirPlay is disabled, exiting..."
  exit 0
fi

SOUND_DEVICE_NAME=${SOUND_DEVICE_NAME:-"pirate-radio $(echo "$BALENA_DEVICE_UUID" | cut -c -4)"}

echo "Starting AirPlay plugin..."
echo "Device name: $SOUND_DEVICE_NAME"

exec shairport-sync \
  --name "$SOUND_DEVICE_NAME" \
  --output alsa \
  -- -d pulse

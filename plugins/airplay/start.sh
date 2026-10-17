#!/usr/bin/env sh

if [ -n "$DISABLE_AIRPLAY" ]; then
  echo "AirPlay is disabled, exiting..."
  exit 0
fi

DEVICE_NAME=${DEVICE_NAME:-"nts-radio $(echo "$BALENA_DEVICE_UUID" | cut -c -4)"}

echo "Starting AirPlay plugin..."
echo "Device name: $DEVICE_NAME"

exec shairport-sync \
  --name "$DEVICE_NAME" \
  --output alsa \
  -- -d pulse

#!/usr/bin/env bash

if [[ -n "$SOUND_DISABLE_UPNP" ]]; then
  echo "UPnP is disabled, exiting..."
  exit 0
fi

SOUND_DEVICE_NAME=${SOUND_DEVICE_NAME:-"pirate-radio $(echo "$BALENA_DEVICE_UUID" | cut -c -4)"}

echo "Starting UPnP plugin..."
echo "Device name: $SOUND_DEVICE_NAME"
echo "Using UPnP UUID: $BALENA_DEVICE_UUID"

exec /usr/bin/gmediarender \
  --friendly-name "$SOUND_DEVICE_NAME" \
  -u "$BALENA_DEVICE_UUID" \
  --port=49494

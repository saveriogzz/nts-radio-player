#!/bin/bash
set -e

CONFIG_TEMPLATE=/usr/src/balena-sound.pa
CONFIG_FILE=/etc/pulse/default.pa.d/01-balenasound.pa

function set_loopback_latency() {
  local LOOPBACK="$1"
  local LATENCY="$2"
  sed -i "s/%$LOOPBACK%/$LATENCY/" "$CONFIG_FILE"
}

function route_output_sink() {
  local OUTPUT=""

  local SINK_FILE=/run/pulse/pulseaudio.sink
  if [[ -f "$SINK_FILE" ]]; then
    OUTPUT=$(cat "$SINK_FILE")
  fi
  OUTPUT="${OUTPUT:-0}"
  sed -i "s/%OUTPUT_SINK%/sink=$OUTPUT/" "$CONFIG_FILE"
  echo "Routing 'balena-sound.output' to '$OUTPUT'."
}

function reset_sound_config() {
  if [[ -f "$CONFIG_FILE" ]]; then
    rm "$CONFIG_FILE"
  fi
  cp "$CONFIG_TEMPLATE" "$CONFIG_FILE"
}

SOUND_INPUT_LATENCY=${SOUND_INPUT_LATENCY:-200}
SOUND_OUTPUT_LATENCY=${SOUND_OUTPUT_LATENCY:-200}

echo "Setting audio routing rules (standalone mode)..."
reset_sound_config

# Standalone: route input directly to output (no snapcast)
sed -i "s/%INPUT_SINK%/sink=balena-sound.output/" "$CONFIG_FILE"
echo "Routing 'balena-sound.input' to 'balena-sound.output'."

route_output_sink
set_loopback_latency "INPUT_LATENCY" "$SOUND_INPUT_LATENCY"
set_loopback_latency "OUTPUT_LATENCY" "$SOUND_OUTPUT_LATENCY"

exec pulseaudio

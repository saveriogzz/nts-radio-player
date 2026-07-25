#!/bin/bash
set -e

CONFIG_TEMPLATE=/usr/src/balena-sound.pa
CONFIG_FILE=/etc/pulse/default.pa.d/01-balenasound.pa

# Wait for the HiFiBerry DAC to appear (I2S driver loads asynchronously)
MAX_WAIT=30
WAITED=0
while ! aplay -l 2>/dev/null | grep -q "snd_rpi_hifiberry"; do
  if [[ $WAITED -ge $MAX_WAIT ]]; then
    echo "WARNING: HiFiBerry DAC not detected after ${MAX_WAIT}s, proceeding anyway."
    break
  fi
  echo "Waiting for HiFiBerry DAC... (${WAITED}s)"
  sleep 2
  WAITED=$((WAITED + 2))
done

# Resolve the DAC's PulseAudio sink name directly from ALSA.
# The base image's entrypoint may have written a stale sink file before
# the DAC was ready, so we detect it ourselves.
DAC_OUTPUT=""
if aplay -l 2>/dev/null | grep -q "snd_rpi_hifiberry"; then
  DAC_CARD=$(aplay -l | grep "snd_rpi_hifiberry" | head -1 | sed 's/^card \([0-9]*\):.*/\1/')
  DAC_OUTPUT="alsa_output.dac.stereo-fallback"
  echo "HiFiBerry DAC detected on card ${DAC_CARD}, using sink: ${DAC_OUTPUT}"
fi

function set_loopback_latency() {
  local LOOPBACK="$1"
  local LATENCY="$2"
  sed -i "s/%$LOOPBACK%/$LATENCY/" "$CONFIG_FILE"
}

function route_output_sink() {
  local OUTPUT=""

  # Prefer the DAC we detected, fall back to the sink file from the base image
  if [[ -n "$DAC_OUTPUT" ]]; then
    OUTPUT="$DAC_OUTPUT"
  else
    local SINK_FILE=/run/pulse/pulseaudio.sink
    if [[ -f "$SINK_FILE" ]]; then
      OUTPUT=$(cat "$SINK_FILE")
    fi
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

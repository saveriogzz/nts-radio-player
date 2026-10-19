#!/bin/bash
set -e

AUDIO_OUTPUT=${AUDIO_OUTPUT:-DAC}
VOLUME=${VOLUME:-75}

# Set AUDIO_DEBUG to get verbose PipeWire/WirePlumber logs
if [[ -n "$AUDIO_DEBUG" ]]; then
  export PIPEWIRE_DEBUG=3 WIREPLUMBER_DEBUG=3
fi

# WirePlumber discovers sound cards through udev, whose database isn't
# shared from the host; run udevd here so the ALSA devices get registered
/lib/systemd/systemd-udevd --daemon
udevadm trigger --action=add --subsystem-match=sound
udevadm settle --timeout=10 || true

# Wait for the HiFiBerry DAC to appear (I2S driver loads asynchronously)
if [[ "$AUDIO_OUTPUT" == "DAC" ]]; then
  MAX_WAIT=30
  WAITED=0
  while ! aplay -l 2>/dev/null | grep -q "snd_rpi_hifiberry"; do
    if [[ $WAITED -ge $MAX_WAIT ]]; then
      if ! grep -qi hifiberry /proc/asound/cards; then
        echo "ERROR: HiFiBerry DAC not registered by the kernel. Check that the"
        echo "  'hifiberry-dac' dtoverlay is applied and onboard audio is off"
        echo "  (dtparam audio=off). A device-level RESIN_HOST_CONFIG_dtoverlay or"
        echo "  _dtparam override hides the fleet value: list them with"
        echo "  'balena env list --device <uuid> --config' and remove them."
      else
        echo "WARNING: HiFiBerry DAC registered but not visible to ALSA here."
      fi
      echo "Kernel sound cards:"; cat /proc/asound/cards
      echo "Sound devices:"; ls /dev/snd 2>&1
      break
    fi
    echo "Waiting for HiFiBerry DAC... (${WAITED}s)"
    sleep 2
    WAITED=$((WAITED + 2))
  done
fi

mkdir -p "$XDG_RUNTIME_DIR"
rm -f "$XDG_RUNTIME_DIR"/pipewire-0* "$XDG_RUNTIME_DIR"/pulse/native

# Private D-Bus session bus: several PipeWire/WirePlumber modules need one
# and WirePlumber exits if it can't reach it
dbus-uuidgen --ensure=/etc/machine-id
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/dbus-session"
rm -f "$XDG_RUNTIME_DIR/dbus-session"
dbus-daemon --session --fork --nopidfile --address="$DBUS_SESSION_BUS_ADDRESS"

declare -A PIDS
pipewire &
PIDS[pipewire]=$!
# WirePlumber needs the PipeWire socket to exist before it connects
until [[ -S "$XDG_RUNTIME_DIR/pipewire-0" ]]; do sleep 0.2; done
wireplumber &
PIDS[wireplumber]=$!
pipewire-pulse &
PIDS[pipewire-pulse]=$!

# Source router (who owns the speaker + now-playing API on 127.0.0.1:4319).
# Supervised on its own: a router crash restarts the router, never the audio
(
  cd /app
  while true; do
    PIPEWIRE_RUNTIME_DIR="$XDG_RUNTIME_DIR" PYTHONUNBUFFERED=1 /app/.venv/bin/python -m router
    echo "Router exited ($?), restarting in 5s"
    sleep 5
  done
) &

# ALSA card name pattern for each AUDIO_OUTPUT value
case "$AUDIO_OUTPUT" in
  DAC) CARD_PATTERN="hifiberry" ;;
  RPI_HEADPHONES) CARD_PATTERN="Headphones" ;;
  RPI_HDMI0) CARD_PATTERN="hdmi-?0" ;;
  *) CARD_PATTERN="" ;;
esac

# Match the pattern against every name a sink exposes; which property holds
# the ALSA card name differs between PipeWire/WirePlumber versions
function find_sink() {
  pw-dump 2>/dev/null | jq -r --arg p "$CARD_PATTERN" '
    .[] | select(.info.props["media.class"] == "Audio/Sink")
        | select([.info.props["alsa.card_name"], .info.props["api.alsa.card.name"],
                  .info.props["alsa.card"], .info.props["node.name"],
                  .info.props["node.description"]]
                 | map(select(. != null) | tostring) | any(test($p; "i")))
        | .id' | head -1
}

function list_sinks() {
  pw-dump 2>/dev/null | jq -r '
    .[] | select(.info.props["media.class"] == "Audio/Sink")
        | "  sink \(.id): \(.info.props["node.name"]) (\(.info.props["node.description"] // "?"))"'
}

# Pick the output sink once WirePlumber has created the ALSA nodes
# (can take a while on a Pi Zero)
SINK_ID=""
for _ in $(seq 1 30); do
  if [[ -z "$CARD_PATTERN" ]]; then
    wpctl inspect @DEFAULT_AUDIO_SINK@ >/dev/null 2>&1 && break
  else
    SINK_ID=$(find_sink)
    [[ -n "$SINK_ID" ]] && break
  fi
  sleep 1
done

echo "Audio sinks:"
list_sinks

if [[ -n "$SINK_ID" ]]; then
  wpctl set-default "$SINK_ID"
  echo "Routing audio to sink $SINK_ID ($AUDIO_OUTPUT)."
else
  if [[ -n "$CARD_PATTERN" ]]; then
    echo "WARNING: no sink matching '$CARD_PATTERN' for AUDIO_OUTPUT=$AUDIO_OUTPUT, using WirePlumber's default."
  else
    echo "Using WirePlumber's default sink (AUTO)."
  fi
fi
if ! wpctl set-volume @DEFAULT_AUDIO_SINK@ "$((VOLUME))%"; then
  echo "WARNING: no output sink available, volume not set."
fi

# Exit (and let the container restart) if an audio daemon dies; the router
# has its own restart loop above and is not part of this check
set +e
wait -n -p DEAD_PID "${PIDS[pipewire]}" "${PIDS[wireplumber]}" "${PIDS[pipewire-pulse]}"
STATUS=$?
sleep 1
for name in pipewire wireplumber pipewire-pulse; do
  if [[ "${PIDS[$name]}" == "$DEAD_PID" ]]; then
    echo "ERROR: $name exited with status $STATUS"
  elif ! kill -0 "${PIDS[$name]}" 2>/dev/null; then
    wait "${PIDS[$name]}"
    echo "ERROR: $name also exited (status $?)"
  fi
done
exit 1

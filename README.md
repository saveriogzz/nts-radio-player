# NTS Radio for Raspberry Pi

Stream NTS Radio (live channels + infinite mixtapes) on a Raspberry Pi with a Pimoroni Pirate Audio Line-out HAT. Also works as a Spotify Connect and AirPlay receiver. Deployed via Balena Cloud.

[![balena deploy button](https://www.balena.io/deploy.svg)](https://dashboard.balena-cloud.com/deploy?repoUrl=https://github.com/saveriogzz/nts-radio-player)

## What you need

- A Raspberry Pi with a 40-pin header — Zero 2W recommended; Pi 3/4/5 also supported
- [Pimoroni Pirate Audio Line-out](https://shop.pimoroni.com/products/pirate-audio-line-out) — ST7789 240x240 display, 4 buttons, I2S DAC (other Pirate Audio variants with the same DAC work too)
- A microSD card (4 GB or larger) and power supply
- A free [balenaCloud](https://dashboard.balena-cloud.com/signup) account (up to 10 devices)

**Assembly:** seat the Pirate Audio HAT on the Pi's 40-pin header before powering on. No soldering or wiring — the HAT is the only hardware configuration there is.

## Getting started (one-click)

1. Click the **Deploy with balena** button above and sign in to balenaCloud.
2. Review the fleet settings and click **Create and deploy**. Everything is preconfigured from `balena.yml` — SPI, the I2S DAC overlay, and default environment variables. No dashboard setup needed.
3. Set your Soloist API key (see [Spotify Connect setup](#spotify-connect-setup)) as a fleet variable **before adding the device**.
4. Click **Add device**, enter your Wi-Fi credentials, and download the OS image.
5. Flash the image to the SD card with [balenaEtcher](https://etcher.balena.io/), insert it, and power on.

On first boot the device downloads the application (a few minutes depending on your connection), the display shows **NTS RADIO — Starting up...**, and channel 1 starts playing automatically. The device also advertises itself on the network as a Spotify Connect and AirPlay target.

### Spotify Connect setup

Spotify Connect runs on [Spotify Soloist](https://developer.spotify.com/documentation/soloist), which needs an API key generated from a Spotify Premium account on the [Soloist API Key page](https://developer.spotify.com/documentation/soloist). Pass it to the device as the `SOLOIST_API_KEY` environment variable.

balenaOS images can't carry environment variables, so the key can't be written to the SD card itself. Set it on the **fleet** before flashing instead — every device downloads fleet variables the first time it connects, so it starts with Spotify Connect ready:

- Dashboard: **Fleet → Variables → Add variable** → `SOLOIST_API_KEY`
- CLI: `balena env set SOLOIST_API_KEY <key> --fleet <fleet-name>`

If the device is already online, set it as a device variable (**Device → Device Variables**, or `balena env set SOLOIST_API_KEY <key> --device <uuid>`) instead. balena restarts the services when the variable changes, so there's no need to reboot. Until the key is set, the Spotify service logs a warning and waits. NTS and AirPlay work without it.

### Name your speaker

The device shows up in the Spotify app as **`DEVICE_NAME`**. The deploy-button form asks for it (default `NTS Radio`, set for the whole fleet); leave it empty and each device is called `nts-radio XXXX` after its UUID.

To give one device its own name, set it as a device variable — it overrides the fleet value, and balena restarts the Spotify service so the new name appears within seconds:

- Dashboard: **Device → Device Variables → Add variable** → `DEVICE_NAME`
- CLI: `balena env set DEVICE_NAME "Living Room" --device <uuid>`

### Manual deploy (balena CLI)

Create a fleet on [Balena Cloud](https://dashboard.balena-cloud.com) (device type **Raspberry Pi Zero 2 W** or any supported model), then:

```bash
balena login

# Hardware configuration (SPI, I2S DAC overlay, onboard audio off).
# balena push does not apply balena.yml's config to an existing fleet, so set
# these BEFORE adding a device — otherwise the device records its stock
# config.txt as device-level overrides that hide these fleet values.
balena env set RESIN_HOST_CONFIG_dtparam '"i2c_arm=on","spi=on","audio=off"' --fleet <fleet-name>
balena env set RESIN_HOST_CONFIG_dtoverlay '"vc4-kms-v3d,cma-320","hifiberry-dac"' --fleet <fleet-name>
balena env set RESIN_HOST_CONFIG_gpio '"25=op,dh"' --fleet <fleet-name>
balena env set SOLOIST_API_KEY <key> --fleet <fleet-name>

balena push <fleet-name>
```

If a device was added before the fleet configuration existed, remove its device-level `dtparam`/`dtoverlay` overrides so the fleet values apply (the device reboots):

```bash
balena env list --device <uuid> --config      # look for rows with a DEVICE value
balena env rm <id> --config --device --yes
```

## Controls

| Button | Playing | Spotify | Menu |
|--------|-------------|---------|-----------|
| A (top-left) | Previous channel/mixtape | Previous track | Scroll up |
| B (bottom-left) | Next channel/mixtape | Next track | Scroll down |
| X (top-right) | Play/Pause | Play/Pause | Select |
| Y (bottom-right) | Open menu | Tap: menu · Hold: back to NTS | Back |

## Screens

- **Live** — NTS 1 / NTS 2 with show artwork, title, and progress bar
- **Mixtapes** — Browse and play all NTS infinite mixtapes
- **Spotify** — Shown automatically while Spotify Connect plays: track, artist, album, cover and progress. NTS pauses when Spotify starts; 30 s after Spotify is paused or stopped the screen returns to NTS (still paused — press X to resume). Hold Y on the Spotify screen to go straight back to NTS: Spotify pauses and this speaker is released in the Spotify app
- **Menu** — Switch between live radio, mixtapes and (while a Spotify track is loaded) Spotify

## Audio sources

NTS Radio plays on boot. The device also works as a receiver for:

| Source | Protocol | How to use |
|--------|----------|------------|
| **Spotify Connect** | [Spotify Soloist](https://developer.spotify.com/documentation/soloist) | Open Spotify on any device → select this device as a speaker |
| **AirPlay 2** | [shairport-sync](https://github.com/mikebrady/shairport-sync) | Use AirPlay from any Apple device or iTunes |

Disable any source by setting its environment variable in the dashboard:

| Variable | Effect |
|----------|--------|
| `DISABLE_SPOTIFY` | Disable Spotify Connect |
| `DISABLE_AIRPLAY` | Disable AirPlay |

## Configuration

All settings are optional. Fleet-level defaults are preconfigured in `balena.yml` and can be changed under **Fleet → Variables** in the dashboard. Per-device variables are set under **Device → Device Variables**.

**Fleet variables** (apply to all devices, set at deploy time):

| Variable | Default | Description |
|----------|---------|-------------|
| `NTS_DEFAULT_CHANNEL` | `1` | Start on channel 1 or 2 |
| `NTS_DISPLAY_BRIGHTNESS` | `80` | Backlight brightness 0-100 |
| `NTS_BUTTON_DEBOUNCE_MS` | `200` | Button debounce in ms |
| `VOLUME` | `75` | System volume (0-100) |
| `SOLOIST_API_KEY` | — | **Required for Spotify Connect.** See [Spotify Connect setup](#spotify-connect-setup) |
| `AUDIO_OUTPUT` | `DAC` | Audio output interface (`AUTO`, `DAC`, `RPI_HEADPHONES`, `RPI_HDMI0`) |
| `AUDIO_DEBUG` | — | Set to any value for verbose PipeWire/WirePlumber logs |

**Per-device variables** (set per device in the dashboard after adding it):

| Variable | Default | Description |
|----------|---------|-------------|
| `DEVICE_NAME` | `nts-radio XXXX` | Spotify Connect speaker name — see [Name your speaker](#name-your-speaker) |
| `SPOTIFY_INITIAL_VOLUME` | — | Spotify starting volume (0-100) |

The hardware configuration (SPI, `hifiberry-dac` device tree overlay, onboard audio off) is applied automatically via the fleet's configuration variables — you'll find it under **Fleet → Configuration** in the dashboard.

## Troubleshooting

- **No audio** — check the HAT is fully seated on the header. Then check **Fleet → Configuration** in the dashboard includes the `hifiberry-dac` overlay (set automatically when deploying with the button); the device reboots when configuration changes. If the audio log says `HiFiBerry DAC not registered by the kernel`, look under **Device → Device Configuration** for a `dtoverlay`/`dtparam` override without `hifiberry-dac` and delete it.
- **Blank display** — also a seating issue in most cases; the display needs SPI, which the same fleet configuration enables.
- **Stuck on "Loading..."** — the device has no route to `www.nts.live`. Check the network; the app needs outbound HTTPS. Audio can play before metadata appears — that's normal on a slow connection.
- **Buttons do nothing** — the app expects the Pirate Audio buttons on BCM pins 5, 6, 16, 24. Other button HATs won't map.
- Device logs are available in the balenaCloud dashboard for anything else.

## Project structure

```
nts-radio-player/
├── nts/                       # NTS Radio Python app
│   ├── api.py                 #   NTS API client (live, mixtapes)
│   ├── player.py              #   mpv wrapper (PipeWire output)
│   ├── display.py             #   ST7789 240x240 display rendering
│   ├── buttons.py             #   GPIO button handler with debounce
│   ├── spotify.py             #   Spotify Connect now-playing (Soloist WebSocket API)
│   └── app.py                 #   Main app: single-threaded event loop
├── core/audio/                # PipeWire + WirePlumber (shared DAC access)
├── plugins/
│   ├── spotify/               # Spotify Connect (Spotify Soloist)
│   └── airplay/               # AirPlay 2 (shairport-sync)
├── assets/mixtapes/           # Bundled mixtape icons
├── tests/                     # Unit tests (hardware mocked, run anywhere)
├── Dockerfile.template        # nts-radio container build
├── docker-compose.yml         # All services
├── balena.yml
├── pyproject.toml
└── uv.lock
```

## Local development

The project uses [uv](https://docs.astral.sh/uv/). Tests run on any machine — the GPIO, display, and mpv layers are mocked, and the hardware dependencies live behind an optional `hw` extra that only the Pi installs:

```bash
uv sync
uv run pytest
```

The app version is tracked in one place: the `version` field in `balena.yml`, which balena uses to version releases.

## Disclaimer

This is an unofficial project, not affiliated with or endorsed by NTS Radio. All audio content is streamed from [nts.live](https://www.nts.live) — go support them. The [NTS logo](https://commons.wikimedia.org/wiki/File:NTS_Radio_logo.svg) is a public-domain text logo; the NTS name and mark belong to NTS Radio and are used here for identification only.

Licensed under the terms in [LICENSE](LICENSE).

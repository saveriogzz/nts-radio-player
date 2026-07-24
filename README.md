# NTS Radio for Raspberry Pi

Stream NTS Radio (live channels + infinite mixtapes) on a Raspberry Pi with a Pimoroni Pirate Audio Line-out HAT. Also works as a Spotify Connect, AirPlay, Bluetooth, and UPnP receiver. Deployed via Balena Cloud.

[![balena deploy button](https://www.balena.io/deploy.svg)](https://dashboard.balena-cloud.com/deploy?repoUrl=https://github.com/saveriogzz/nts-pirate-radio)

## What you need

- A Raspberry Pi with a 40-pin header — Zero 2W recommended; Pi 3/4/5 also supported (Bluetooth requires Pi 3+ or Zero 2W)
- [Pimoroni Pirate Audio Line-out](https://shop.pimoroni.com/products/pirate-audio-line-out) — ST7789 240x240 display, 4 buttons, I2S DAC (other Pirate Audio variants with the same DAC work too)
- A microSD card (4 GB or larger) and power supply
- A free [balenaCloud](https://dashboard.balena-cloud.com/signup) account (up to 10 devices)

**Assembly:** seat the Pirate Audio HAT on the Pi's 40-pin header before powering on. No soldering or wiring — the HAT is the only hardware configuration there is.

## Getting started (one-click)

1. Click the **Deploy with balena** button above and sign in to balenaCloud.
2. Review the fleet settings and click **Create and deploy**. Everything is preconfigured from `balena.yml` — SPI, the I2S DAC overlay, and default environment variables. No dashboard setup needed.
3. Click **Add device**, enter your Wi-Fi credentials, and download the OS image.
4. Flash the image to the SD card with [balenaEtcher](https://etcher.balena.io/), insert it, and power on.

On first boot the device downloads the application (a few minutes depending on your connection), the display shows **NTS RADIO — Starting up...**, and channel 1 starts playing automatically. The device also advertises itself on the network as a Spotify Connect, AirPlay, and UPnP target, and is discoverable via Bluetooth.

### Manual deploy (balena CLI)

Create a fleet on [Balena Cloud](https://dashboard.balena-cloud.com) (device type **Raspberry Pi Zero 2 W** or any supported model), then:

```bash
balena login
balena push <fleet-name>
```

## Controls

| Button | Playing | Menu |
|--------|-------------|-----------|
| A (top-left) | Previous channel/mixtape | Scroll up |
| B (bottom-left) | Next channel/mixtape | Scroll down |
| X (top-right) | Play/Pause | Select |
| Y (bottom-right) | Open menu | Back |

## Screens

- **Live** — NTS 1 / NTS 2 with show artwork, title, and progress bar
- **Mixtapes** — Browse and play all NTS infinite mixtapes
- **Menu** — Switch between live radio and mixtapes

## Audio sources

NTS Radio plays on boot. The device also works as a receiver for:

| Source | Protocol | How to use |
|--------|----------|------------|
| **Spotify Connect** | [librespot](https://github.com/librespot-org/librespot) | Open Spotify on any device → select this device as a speaker |
| **AirPlay 2** | [shairport-sync](https://github.com/mikebrady/shairport-sync) | Use AirPlay from any Apple device or iTunes |
| **Bluetooth** | A2DP via [balena bluetooth block](https://github.com/balena-io-experimental/bluetooth) | Pair your phone/laptop via Bluetooth settings |
| **UPnP/DLNA** | [gmediarender](https://github.com/hzeller/gmrender-resurrect) | Cast from any UPnP/DLNA compatible app |

Disable any source by setting its environment variable in the dashboard:

| Variable | Effect |
|----------|--------|
| `SOUND_DISABLE_SPOTIFY` | Disable Spotify Connect |
| `SOUND_DISABLE_AIRPLAY` | Disable AirPlay |
| `SOUND_DISABLE_BLUETOOTH` | Disable Bluetooth |
| `SOUND_DISABLE_UPNP` | Disable UPnP/DLNA |

## Configuration

All settings are optional. These are set as fleet defaults in `balena.yml` and can be overridden per-fleet or per-device in the balenaCloud dashboard (Environment Variables):

**NTS settings:**

| Variable | Default | Description |
|----------|---------|-------------|
| `NTS_DEFAULT_CHANNEL` | `1` | Start on channel 1 or 2 |
| `NTS_DISPLAY_BRIGHTNESS` | `80` | Backlight brightness 0-100 |
| `NTS_BUTTON_DEBOUNCE_MS` | `200` | Button debounce in ms |

**Audio settings:**

| Variable | Default | Description |
|----------|---------|-------------|
| `SOUND_DEVICE_NAME` | auto | Broadcast name for Spotify/AirPlay/UPnP (defaults to `pirate-radio XXXX`) |
| `SOUND_SPOTIFY_BITRATE` | `160` | Spotify playback bitrate (96, 160, or 320) |
| `SOUND_VOLUME` | `75` | System volume (0-100) |
| `BLUETOOTH_DEVICE_NAME` | auto | Bluetooth broadcast name (separate from `SOUND_DEVICE_NAME`) |

The hardware configuration (SPI, `hifiberry-dac` device tree overlay, onboard audio off) is applied automatically via the fleet's configuration variables — you'll find it under **Fleet → Configuration** in the dashboard.

## Troubleshooting

- **No audio** — check the HAT is fully seated on the header. Then check **Fleet → Configuration** in the dashboard includes the `hifiberry-dac` overlay (set automatically when deploying with the button); the device reboots when configuration changes.
- **Blank display** — also a seating issue in most cases; the display needs SPI, which the same fleet configuration enables.
- **Stuck on "Loading..."** — the device has no route to `www.nts.live`. Check the network; the app needs outbound HTTPS. Audio can play before metadata appears — that's normal on a slow connection.
- **Buttons do nothing** — the app expects the Pirate Audio buttons on BCM pins 5, 6, 16, 24. Other button HATs won't map.
- Device logs are available in the balenaCloud dashboard for anything else.

## Project structure

```
nts-pirate-radio/
├── nts/                       # NTS Radio Python app
│   ├── api.py                 #   NTS API client (live, mixtapes)
│   ├── player.py              #   mpv wrapper (PulseAudio output)
│   ├── display.py             #   ST7789 240x240 display rendering
│   ├── buttons.py             #   GPIO button handler with debounce
│   └── app.py                 #   Main app: single-threaded event loop
├── core/audio/                # PulseAudio server (shared DAC access)
├── plugins/
│   ├── spotify/               # Spotify Connect (librespot)
│   ├── airplay/               # AirPlay 2 (shairport-sync)
│   ├── bluetooth/             # Bluetooth A2DP
│   └── upnp/                  # UPnP/DLNA (gmediarender)
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

For running on a Pi directly without Balena, `install.sh` and `nts-radio.service` set it up as a systemd service on Raspberry Pi OS.

## Disclaimer

This is an unofficial project, not affiliated with or endorsed by NTS Radio. All audio content is streamed from [nts.live](https://www.nts.live) — go support them. The [NTS logo](https://commons.wikimedia.org/wiki/File:NTS_Radio_logo.svg) is a public-domain text logo; the NTS name and mark belong to NTS Radio and are used here for identification only.

Licensed under the terms in [LICENSE](LICENSE).

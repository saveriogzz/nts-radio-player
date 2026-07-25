#!/bin/bash
# NTS Radio installer for Raspberry Pi Zero 2W with Pimoroni Pirate Audio
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="nts-radio"
INSTALL_DIR="/home/pi/nts-radio"

echo "=== NTS Radio Installer ==="
echo ""

# Check we're on a Pi
if [ ! -f /proc/device-tree/model ]; then
    echo "Warning: This doesn't look like a Raspberry Pi."
    echo "Continuing anyway..."
fi

# ── Enable SPI ───────────────────────────────────────────
echo "[1/6] Enabling SPI..."
if ! grep -q "^dtparam=spi=on" /boot/firmware/config.txt 2>/dev/null && \
   ! grep -q "^dtparam=spi=on" /boot/config.txt 2>/dev/null; then
    CONFIG_FILE="/boot/firmware/config.txt"
    [ ! -f "$CONFIG_FILE" ] && CONFIG_FILE="/boot/config.txt"
    sudo bash -c "echo 'dtparam=spi=on' >> $CONFIG_FILE"
    echo "  SPI enabled (reboot required)"
else
    echo "  SPI already enabled"
fi

# ── Enable I2S DAC overlay ───────────────────────────────
echo "[2/6] Enabling I2S DAC (hifiberry-dac)..."
CONFIG_FILE="/boot/firmware/config.txt"
[ ! -f "$CONFIG_FILE" ] && CONFIG_FILE="/boot/config.txt"

if ! grep -q "^dtoverlay=hifiberry-dac" "$CONFIG_FILE" 2>/dev/null; then
    sudo bash -c "echo 'dtoverlay=hifiberry-dac' >> $CONFIG_FILE"
    echo "  I2S DAC overlay enabled (reboot required)"
else
    echo "  I2S DAC overlay already enabled"
fi

# Disable onboard audio if active (conflicts with I2S)
if grep -q "^dtparam=audio=on" "$CONFIG_FILE" 2>/dev/null; then
    sudo sed -i 's/^dtparam=audio=on/# dtparam=audio=on/' "$CONFIG_FILE"
    echo "  Onboard audio disabled (I2S takes over)"
fi

# ── Install system dependencies ──────────────────────────
echo "[3/6] Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    mpv \
    fonts-dejavu-core \
    python3-dev \
    gcc \
    swig \
    curl \
    pulseaudio

# ── Install uv ────────────────────────────────────────────
echo "[4/6] Installing uv..."
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"

# ── Copy files and install Python dependencies ────────────
echo "[5/6] Installing application..."
if [ "$SCRIPT_DIR" != "$INSTALL_DIR" ]; then
    mkdir -p "$INSTALL_DIR"
    cp -r "$SCRIPT_DIR/nts" "$SCRIPT_DIR/assets" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/pyproject.toml" "$SCRIPT_DIR/uv.lock" "$INSTALL_DIR/"
    echo "  Installed to $INSTALL_DIR"
else
    echo "  Already in install directory"
fi

cd "$INSTALL_DIR"
"$UV" sync --locked --no-dev --extra hw

# ── Install and enable systemd service ───────────────────
echo "[6/6] Setting up systemd service..."
sudo cp "$SCRIPT_DIR/nts-radio.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

echo ""
echo "=== Installation complete ==="
echo ""
echo "To start now:  sudo systemctl start $SERVICE_NAME"
echo "View logs:     journalctl -u $SERVICE_NAME -f"
echo ""
echo "NOTE: If SPI or I2S was just enabled, please reboot first:"
echo "      sudo reboot"

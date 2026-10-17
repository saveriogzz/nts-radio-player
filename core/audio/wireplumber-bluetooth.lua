-- No Bluetooth on this device; the monitor also needs systemd-logind, which
-- doesn't exist in the container and makes WirePlumber exit (status 70).
-- Loaded as 60-*: after 50-bluez-config.lua, before 90-enable-all.lua.
bluez_monitor.enabled = false
bluez_monitor.properties["with-logind"] = false

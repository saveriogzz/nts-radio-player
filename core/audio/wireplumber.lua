-- Loaded as 60-*: after 50-alsa-config.lua, before 90-enable-all.lua.
-- No session D-Bus inside the container, so skip ALSA device reservation
alsa_monitor.properties["alsa.reserve"] = false
-- Keep the DAC open; suspending it causes pops and slow resume on I2S
alsa_monitor.rules[#alsa_monitor.rules + 1] = {
  matches = { { { "node.name", "matches", "alsa_output.*" } } },
  apply_properties = { ["session.suspend-timeout-seconds"] = 0 },
}
-- No cameras on this device: skip video monitors (libcamera SPA isn't installed)
libcamera_monitor.enabled = false
v4l2_monitor.enabled = false

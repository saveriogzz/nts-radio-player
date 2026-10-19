"""Audio sources other than NTS, as seen by the device UI.

NTS is the app's own source (mpv, live and mixtape screens). Every other
source (Spotify Connect today; AirPlay, Bluetooth later) is played by its own
service, owned through the router, and shown on a shared "now playing"
screen. Adding one means adding a ``SourceSpec`` here, plus its router
adapter; the app's state machine needs no new conditions.
"""

from dataclasses import dataclass, field

from nts.display import SPOTIFY_GREEN

NTS = "nts"


@dataclass(frozen=True)
class SourceSpec:
    name: str                   # source name used by the router ("spotify")
    label: str                  # menu entry and screen header
    accent: tuple               # RGB colour for the header status and progress bar
    # Button → router command on this source's screen; "toggle" = play/pause
    buttons: dict = field(default_factory=lambda: {
        "a": "skip_prev",
        "b": "skip_next",
        "x": "toggle",
    })


SOURCES: dict[str, SourceSpec] = {
    spec.name: spec
    for spec in (
        SourceSpec(name="spotify", label="Spotify", accent=SPOTIFY_GREEN),
    )
}


def source_by_label(label: str):
    for spec in SOURCES.values():
        if spec.label == label:
            return spec
    return None

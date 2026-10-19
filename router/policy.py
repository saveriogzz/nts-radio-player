"""Which source owns the speaker: newest wins.

Pure logic, no I/O. The router feeds it "source X is/isn't playing" facts
(from PipeWire stream states) and gets back what changed: the new active
source and which source, if any, must be asked to stop.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Decision:
    active: Optional[str]
    stop: Optional[str] = None  # previous owner to pause, if any


class Arbiter:
    """Newest wins: a source that starts playing takes over from the current one.

    Pausing doesn't give the speaker away; the active source stays active
    (so the screen can keep showing it) until another source starts.
    """

    def __init__(self):
        self.active: Optional[str] = None
        self._playing: set[str] = set()

    @property
    def playing(self) -> frozenset[str]:
        return frozenset(self._playing)

    def update(self, source: str, playing: bool) -> Optional[Decision]:
        """Record a source's playing state; return a Decision if ``active`` changed."""
        was_playing = source in self._playing
        if playing:
            self._playing.add(source)
        else:
            self._playing.discard(source)

        if playing and not was_playing and source != self.active:
            previous = self.active
            self.active = source
            # Only interrupt the previous owner if it is actually still playing
            return Decision(active=source, stop=previous if previous in self._playing else None)
        return None

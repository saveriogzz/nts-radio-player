"""Learn which sources are playing from the PipeWire graph.

``pw-dump --monitor`` prints the graph as JSON and then one JSON array per
batch of changes. Changed objects are re-sent; removed ones come back as
``{"id": N, "info": null}``. ``GraphTracker`` keeps the audio output streams
and maps them to sources, so the router knows who is playing without any
per-source code.
"""

import asyncio
import json
import logging
import re
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

# Stream → source, matched case-insensitively against the stream's
# application/node/binary names. Add a line here for each new source.
SOURCE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("nts", re.compile(r"\bmpv\b", re.I)),
    ("spotify", re.compile(r"spotify|soloist", re.I)),
    ("airplay", re.compile(r"shairport", re.I)),
    ("bluetooth", re.compile(r"bluez", re.I)),
]

_NAME_PROPS = ("application.name", "node.name", "application.process.binary", "media.name")


def source_for(props: dict) -> Optional[str]:
    names = " ".join(str(props.get(k, "")) for k in _NAME_PROPS)
    for source, pattern in SOURCE_PATTERNS:
        if pattern.search(names):
            return source
    return None


class GraphTracker:
    """Tracks output streams; ``apply`` returns per-source playing changes."""

    def __init__(self):
        self._streams: dict[int, dict] = {}  # id -> {"source", "state"}
        self._reported: dict[str, bool] = {}

    def apply(self, objects: list) -> dict[str, bool]:
        for obj in objects if isinstance(objects, list) else []:
            if not isinstance(obj, dict) or "id" not in obj:
                continue
            oid = obj["id"]
            info = obj.get("info")
            if info is None:  # object removed
                self._streams.pop(oid, None)
                continue
            if obj.get("type", "PipeWire:Interface:Node") != "PipeWire:Interface:Node":
                continue
            entry = self._streams.get(oid)
            props = info.get("props") or {}
            if entry is None:
                if props.get("media.class") != "Stream/Output/Audio":
                    continue
                source = source_for(props)
                if source is None:
                    continue
                entry = self._streams[oid] = {"source": source, "state": None}
            if info.get("state"):
                entry["state"] = info["state"]
        return self._changes()

    def playing(self) -> dict[str, bool]:
        result: dict[str, bool] = {}
        for entry in self._streams.values():
            result[entry["source"]] = result.get(entry["source"], False) or entry["state"] == "running"
        return result

    def _changes(self) -> dict[str, bool]:
        now = self.playing()
        changes = {}
        for source in set(now) | set(self._reported):
            value = now.get(source, False)
            if self._reported.get(source, False) != value:
                changes[source] = value
        self._reported = {s: v for s, v in now.items()}
        return changes


def iter_json_arrays(buffer: str) -> tuple[list, str]:
    """Split complete top-level JSON values off the front of ``buffer``."""
    decoder = json.JSONDecoder()
    values = []
    idx = 0
    while True:
        while idx < len(buffer) and buffer[idx].isspace():
            idx += 1
        if idx >= len(buffer):
            break
        try:
            value, end = decoder.raw_decode(buffer, idx)
        except ValueError:
            break  # incomplete; wait for more input
        values.append(value)
        idx = end
    return values, buffer[idx:]


async def monitor(retry_delay: float = 5.0) -> AsyncIterator[dict[str, bool]]:
    """Yield per-source playing changes forever, restarting pw-dump as needed."""
    while True:
        tracker = GraphTracker()
        try:
            proc = await asyncio.create_subprocess_exec(
                "pw-dump", "--monitor", "--no-colors",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            logger.info("Watching PipeWire graph")
            buffer = ""
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", "replace")
                values, buffer = iter_json_arrays(buffer)
                for value in values:
                    changes = tracker.apply(value)
                    if changes:
                        yield changes
            await proc.wait()
            logger.warning("pw-dump exited (%s); restarting", proc.returncode)
        except FileNotFoundError:
            logger.error("pw-dump not found")
        except Exception:
            logger.exception("PipeWire monitor failed")
        # Whatever was playing can't be trusted any more
        lost = {source: False for source, on in tracker.playing().items() if on}
        if lost:
            yield lost
        await asyncio.sleep(retry_delay)

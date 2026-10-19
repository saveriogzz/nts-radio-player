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
        # Every node seen, with props merged across updates: a new node may be
        # announced before its properties are filled in, so it's classified
        # whenever enough is known rather than only on first sight
        self._nodes: dict[int, dict] = {}  # id -> {"props": {...}, "state": str|None}
        self._non_nodes: set[int] = set()
        self._reported: dict[str, bool] = {}

    def apply(self, objects: list) -> dict[str, bool]:
        for obj in objects if isinstance(objects, list) else []:
            if not isinstance(obj, dict) or "id" not in obj:
                continue
            oid = obj["id"]
            info = obj.get("info")
            if info is None:  # object removed
                self._nodes.pop(oid, None)
                self._non_nodes.discard(oid)
                continue
            kind = obj.get("type")
            if kind is not None and kind != "PipeWire:Interface:Node":
                self._non_nodes.add(oid)
                continue
            if oid in self._non_nodes or not isinstance(info, dict):
                continue
            node = self._nodes.setdefault(oid, {"props": {}, "state": None})
            if isinstance(info.get("props"), dict):
                node["props"].update(info["props"])
            if info.get("state"):
                node["state"] = info["state"]
        return self._changes()

    def _streams(self):
        for node in self._nodes.values():
            props = node["props"]
            if props.get("media.class") != "Stream/Output/Audio":
                continue
            source = source_for(props)
            if source is not None:
                yield source, node["state"]

    def playing(self) -> dict[str, bool]:
        result: dict[str, bool] = {}
        for source, state in self._streams():
            result[source] = result.get(source, False) or state == "running"
        return result

    def describe(self) -> str:
        """Streams currently mapped to sources, for logs."""
        parts = []
        for oid, node in sorted(self._nodes.items()):
            props = node["props"]
            if props.get("media.class") != "Stream/Output/Audio":
                continue
            name = props.get("application.name") or props.get("node.name") or "?"
            parts.append(f"{oid}:{name}={node['state']}->{source_for(props) or 'unmapped'}")
        return ", ".join(parts) or "none"

    def _changes(self) -> dict[str, bool]:
        now = self.playing()
        changes = {}
        for source in set(now) | set(self._reported):
            value = now.get(source, False)
            if self._reported.get(source, False) != value:
                changes[source] = value
        self._reported = {s: v for s, v in now.items()}
        return changes


# pw-dump 0.3.65 (Debian bookworm) sometimes prints a key-less "[ ]" inside an
# object (seen on Soloist's ports: `"ProcessLatency": [ ], [ ] }`), which is
# invalid JSON. Drop such stray empty arrays before parsing.
_STRAY_EMPTY_ARRAY = re.compile(r",\s*\[\s*\](?=\s*[}\]])")
# pw-dump ends every top-level array with "]" alone on a line
_TOP_LEVEL_END = re.compile(r"^\][ \t]*$", re.M)


def _parse_dump(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(_STRAY_EMPTY_ARRAY.sub("", text))


def iter_json_arrays(buffer: str) -> tuple[list, str]:
    """Split complete top-level arrays off the front of ``buffer``.

    Chunks are cut at pw-dump's top-level closing bracket rather than by the
    JSON parser, so a malformed update is skipped (and logged) instead of
    stalling everything after it.
    """
    values = []
    while True:
        match = _TOP_LEVEL_END.search(buffer)
        if match is None:
            break  # incomplete; wait for more input
        chunk, buffer = buffer[: match.end()], buffer[match.end():]
        if not chunk.strip():
            continue
        try:
            values.append(_parse_dump(chunk))
        except ValueError as e:
            logger.warning("Skipping unparseable pw-dump update (%s)", e)
    return values, buffer


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
                        logger.info("Playing changed %s; streams: %s", changes, tracker.describe())
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

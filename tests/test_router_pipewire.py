"""Tests for router.pipewire — PipeWire graph → per-source playing state."""

import json
from pathlib import Path

from router.pipewire import GraphTracker, iter_json_arrays, source_for

FIXTURE = Path(__file__).parent / "fixtures" / "pw_dump_nts_playing.json"


def _stream(oid, name, state, app=None):
    props = {"media.class": "Stream/Output/Audio", "node.name": name}
    if app:
        props["application.name"] = app
    return {"id": oid, "type": "PipeWire:Interface:Node", "info": {"state": state, "props": props}}


def test_real_dump_shows_nts_playing():
    t = GraphTracker()
    assert t.apply(json.loads(FIXTURE.read_text())) == {"nts": True}


def test_sinks_and_drivers_are_ignored():
    dump = json.loads(FIXTURE.read_text())
    t = GraphTracker()
    t.apply([o for o in dump if o["info"]["props"].get("media.class") != "Stream/Output/Audio"])
    assert t.playing() == {}


def test_source_mapping():
    assert source_for({"application.name": "mpv"}) == "nts"
    assert source_for({"node.name": "Spotify"}) == "spotify"
    assert source_for({"application.process.binary": "shairport-sync"}) == "airplay"
    assert source_for({"node.name": "bluez_input.AA_BB"}) == "bluetooth"
    assert source_for({"node.name": "something-else"}) is None


def test_state_changes_and_removal():
    t = GraphTracker()
    assert t.apply([_stream(50, "Spotify", "running")]) == {"spotify": True}
    # partial update: only state is re-sent
    assert t.apply([{"id": 50, "type": "PipeWire:Interface:Node", "info": {"state": "paused"}}]) == {"spotify": False}
    assert t.apply([{"id": 50, "type": "PipeWire:Interface:Node", "info": {"state": "running"}}]) == {"spotify": True}
    assert t.apply([{"id": 50, "info": None}]) == {"spotify": False}


def test_no_change_reports_nothing():
    t = GraphTracker()
    t.apply([_stream(43, "mpv", "running", app="mpv")])
    assert t.apply([_stream(43, "mpv", "running", app="mpv")]) == {}


def test_two_streams_one_source():
    t = GraphTracker()
    t.apply([_stream(1, "mpv", "running", app="mpv"), _stream(2, "mpv", "idle", app="mpv")])
    assert t.playing() == {"nts": True}
    assert t.apply([{"id": 1, "info": None}]) == {"nts": False}


def test_iter_json_arrays_handles_partial_input():
    values, rest = iter_json_arrays('[{"id": 1}]\n[{"id": 2}')
    assert values == [[{"id": 1}]]
    values, rest = iter_json_arrays(rest + "]\n")
    assert values == [[{"id": 2}]]
    assert rest == ""


def test_monitor_against_fake_pw_dump(tmp_path, monkeypatch):
    """monitor() parses a real subprocess stream and reports changes."""
    import asyncio
    from router import pipewire

    script = tmp_path / "pw-dump"
    script.write_text(
        "#!/bin/sh\n"
        "cat <<'JSON'\n"
        '[{"id": 43, "type": "PipeWire:Interface:Node", "info": {"state": "running",'
        ' "props": {"media.class": "Stream/Output/Audio", "node.name": "mpv", "application.name": "mpv"}}}]\n'
        '[{"id": 43, "type": "PipeWire:Interface:Node", "info": {"state": "paused"}}]\n'
        "JSON\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{__import__('os').environ['PATH']}")

    async def collect():
        out = []
        gen = pipewire.monitor(retry_delay=0.01)
        async for changes in gen:
            out.append(changes)
            if len(out) == 2:
                break
        await gen.aclose()
        return out

    assert asyncio.run(asyncio.wait_for(collect(), 5)) == [{"nts": True}, {"nts": False}]

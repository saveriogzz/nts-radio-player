"""Entry point: ``python -m router``."""

import asyncio
import logging
import os

from router import pipewire
from router.service import DEFAULT_HOST, DEFAULT_PORT, Router
from router.soloist import SoloistAdapter

logger = logging.getLogger("router")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    from websockets.asyncio.server import serve

    router = Router()
    soloist = SoloistAdapter(router.broadcast)
    router.adapters["spotify"] = soloist

    async def watch_pipewire():
        async for changes in pipewire.monitor():
            await router.on_playing_changes(changes)

    host = os.environ.get("ROUTER_HOST", DEFAULT_HOST)
    port = int(os.environ.get("ROUTER_PORT", DEFAULT_PORT))
    async with serve(router.handle_client, host, port, ping_interval=20):
        logger.info("Router API listening on ws://%s:%d", host, port)
        await asyncio.gather(soloist.run(), watch_pipewire())


if __name__ == "__main__":
    asyncio.run(main())

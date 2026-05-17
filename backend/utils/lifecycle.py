"""Lifecycle helpers — shutdown signaling for long-lived SSE handlers.

uvicorn's graceful shutdown waits for in-flight HTTP requests to finish
before closing. SSE handlers loop indefinitely (they yield heartbeats
forever until the client disconnects), so without explicit shutdown
detection they block ``--reload`` and clean restarts forever.

Any handler that runs an indefinite loop should ``await`` or check
``get_shutdown_event()`` and exit cleanly when it's set.
"""

import asyncio

_event: asyncio.Event | None = None


def get_shutdown_event() -> asyncio.Event:
    """Return (creating on first use) a process-wide shutdown ``asyncio.Event``."""
    global _event
    if _event is None:
        _event = asyncio.Event()
    return _event


def signal_shutdown() -> None:
    """Wake every active SSE / long-poll handler so they exit their loops."""
    get_shutdown_event().set()

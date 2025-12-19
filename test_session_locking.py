import asyncio

import pytest

import main


class _DummySessionClient:
    def __init__(self) -> None:
        self._active_instances = 0
        self.start_calls = 0
        self.max_active = 0

    async def start(self) -> None:
        if self._active_instances:
            raise RuntimeError("session start called concurrently")

        self._active_instances += 1
        self.start_calls += 1
        self.max_active = max(self.max_active, self._active_instances)

        await asyncio.sleep(0.01)

    async def stop(self) -> None:
        if not self._active_instances:
            raise RuntimeError("session stop without active start")

        await asyncio.sleep(0.005)
        self._active_instances -= 1


@pytest.fixture(autouse=True)
def _reset_session_locks() -> None:
    main.session_locks.clear()
    main.active_client_locks.clear()
    yield
    main.session_locks.clear()
    main.active_client_locks.clear()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_client_session_serializes_concurrent_access() -> None:
    client = _DummySessionClient()
    session_key = "test-session"

    async def _use_session(_: int) -> None:
        async with main._client_session(client, session_file=session_key, lock_key=session_key):
            await asyncio.sleep(0.01)

    tasks = [asyncio.create_task(_use_session(i)) for i in range(3)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        assert result is None

    assert client.start_calls == 3
    assert client.max_active == 1

    main._release_session_lock(session_key)

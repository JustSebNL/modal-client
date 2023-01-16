# Copyright Modal Labs 2023
import pytest

from modal.aio import AioQueue, AioStub, aio_lookup
from modal.exception import NotFoundError


@pytest.mark.asyncio
async def test_persistent_object(servicer, aio_client):
    stub = AioStub()
    stub["q_1"] = AioQueue()
    await stub.deploy("my-queue", client=aio_client)

    q = await aio_lookup("my-queue", client=aio_client)
    # assert isinstance(q_3, AioQueue)  # TODO(erikbern): it's a AioQueueHandler
    assert q.object_id == "qu-1"

    with pytest.raises(NotFoundError):
        await aio_lookup("bazbazbaz", client=aio_client)


def square(x):
    # This function isn't deployed anyway
    pass


@pytest.mark.asyncio
async def test_lookup_function(servicer, aio_client):
    stub = AioStub()

    stub.function(square)
    await stub.deploy("my-function", client=aio_client)

    f = await aio_lookup("my-function", client=aio_client)
    assert f.object_id == "fu-1"

    # Make sure we can call this function
    assert await f.call(2, 4) == 20
    assert [r async for r in f.map([5, 2], [4, 3])] == [41, 13]
import pytest

from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.core.models import ProviderEvent, ProviderEventType, UserMessage
from aipic_to_model.agent.providers.base import ModelProfile, ModelRequest
from aipic_to_model.agent.providers.fake import FakeProvider, ScriptedResponse


@pytest.mark.agent
@pytest.mark.asyncio
async def test_fake_provider_replays_scripted_events() -> None:
    provider = FakeProvider(
        (ScriptedResponse((ProviderEvent(ProviderEventType.TEXT_DELTA, delta="ok"),)),)
    )
    request = ModelRequest(ModelProfile("fake", "fake", "http://fake"), (UserMessage("hi"),))
    assert [event.delta async for event in provider.stream(request, CancellationToken())] == ["ok"]
    assert provider.requests == [request]

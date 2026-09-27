"""The api gets the chat's workflow only when CHAT_ENABLED is on (P12): off, nothing can call the
LLM or spend money, and the chat route answers 409."""

from app.chat.graph import GraphChatEngine
from tests.helpers import build_settings


def test_the_chat_is_off_by_default() -> None:
    from app.main import create_app

    assert create_app(build_settings()).state.chat_engine is None


def test_switched_on_the_api_holds_the_workflow() -> None:
    """Building it makes no request: the Bedrock clients connect only when called."""
    from app.main import create_app

    app = create_app(build_settings(chat_enabled=True, embeddings_enabled=True))
    assert isinstance(app.state.chat_engine, GraphChatEngine)


def test_the_chat_works_without_search() -> None:
    """CHAT_ENABLED alone: the workflow answers from facts, with no embedder."""
    from app.main import create_app

    app = create_app(build_settings(chat_enabled=True, embeddings_enabled=False))
    assert isinstance(app.state.chat_engine, GraphChatEngine)
    assert app.state.embedder is None

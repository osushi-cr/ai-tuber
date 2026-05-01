import pytest
import re
import logging
from unittest.mock import AsyncMock, MagicMock, patch
from saint_graph.saint_graph import SaintGraph
from saint_graph.config import Config
from google.adk.runners import InMemoryRunner
from google.genai import types

class MockEvent:
    def __init__(self, text):
        self.content = MagicMock()
        part = MagicMock()
        part.text = text
        self.content.parts = [part]
    def __str__(self):
        return "MockEvent"

@pytest.fixture
def mock_adk():
    with patch("saint_graph.saint_graph.Agent") as mock_agent, \
         patch("saint_graph.saint_graph.InMemoryRunner", spec=InMemoryRunner) as mock_runner, \
         patch("saint_graph.saint_graph.McpToolset") as mock_toolset, \
         patch("saint_graph.saint_graph.BodyClient") as mock_body_client, \
         patch("saint_graph.saint_graph.Event", MockEvent):
        yield {
            "Agent": mock_agent,
            "InMemoryRunner": mock_runner,
            "McpToolset": mock_toolset,
            "BodyClient": mock_body_client
        }

@pytest.mark.asyncio
async def test_saint_graph_initialization(mock_adk):
    # Setup
    weather_mcp_url = "http://weather:8001/sse"
    system_instruction = "Test instruction"
    mock_body = mock_adk["BodyClient"]()
    
    # Execute
    sg = SaintGraph(mock_body, weather_mcp_url, system_instruction)
    
    # Verify
    assert sg.system_instruction == system_instruction
    assert sg.body == mock_body
    mock_adk["McpToolset"].assert_called_once()
    mock_adk["Agent"].assert_called_once()
    mock_adk["InMemoryRunner"].assert_called_once_with(agent=sg.agent)

@pytest.mark.asyncio
async def test_process_turn_parses_emotion_tag(mock_adk):
    # Setup
    mock_body = mock_adk["BodyClient"]()
    sg = SaintGraph(mock_body, "", "Instruction")
    sg.body.change_emotion = AsyncMock()
    sg.body.queue_speak = AsyncMock(return_value={"action_id": "speak-1"})
    sg.body.wait_for_queue = AsyncMock()
    sg.body.wait_for_queue_strict = AsyncMock(return_value=True)
    
    mock_run_async = MagicMock()
    async def mock_iter(*args, **kwargs):
        yield MockEvent("[emotion: joyful] Hello World")

    mock_run_async.side_effect = mock_iter
    sg.runner.run_async = mock_run_async
    
    sg.runner.app_name = "TestApp"
    sg.runner.session_service = AsyncMock()
    sg.runner.session_service.get_session = AsyncMock(return_value="ExistingSession")
    
    # Execute
    await sg.process_turn("Hello")
    
    # Verify
    from unittest.mock import call
    sg.body.change_emotion.assert_has_calls([
        call("silent"),
        call("joyful"),
        call("silent")
    ])
    sg.body.queue_speak.assert_called_once_with("Hello World", style="joyful", speaker_id=None)

@pytest.mark.asyncio
async def test_process_turn_defaults_to_neutral(mock_adk):
    # Setup
    mock_body = mock_adk["BodyClient"]()
    sg = SaintGraph(mock_body, "", "Instruction")
    sg.body.change_emotion = AsyncMock()
    sg.body.queue_speak = AsyncMock(return_value={"action_id": "speak-1"})
    sg.body.wait_for_queue = AsyncMock()
    sg.body.wait_for_queue_strict = AsyncMock(return_value=True)
    
    mock_run_async = MagicMock()
    async def mock_iter(*args, **kwargs):
        yield MockEvent("No tag here")

    mock_run_async.side_effect = mock_iter
    sg.runner.run_async = mock_run_async
    
    sg.runner.app_name = "TestApp"
    sg.runner.session_service = AsyncMock()
    sg.runner.session_service.get_session = AsyncMock(return_value="ExistingSession")
    
    # Execute
    await sg.process_turn("Hello")
    
    # Verify
    from unittest.mock import call
    sg.body.change_emotion.assert_has_calls([
        call("silent"),
        call("neutral"),
        call("silent")
    ])
    sg.body.queue_speak.assert_called_once_with("No tag here", style="neutral", speaker_id=None)

@pytest.mark.asyncio
async def test_prepare_news_reading_text_collects_without_speaking(mock_adk):
    # Setup
    mock_body = mock_adk["BodyClient"]()
    templates = {"news_reading": "Here is {title}: {content}"}
    sg = SaintGraph(mock_body, "", "Instruction", templates=templates)
    sg.body.change_emotion = AsyncMock()
    sg.body.queue_speak = AsyncMock()
    sg.body.wait_for_queue = AsyncMock()

    mock_run_async = MagicMock()
    async def mock_iter(*args, **kwargs):
        yield MockEvent("[emotion: joyful] Hello World")

    mock_run_async.side_effect = mock_iter
    sg.runner.run_async = mock_run_async

    sg.runner.app_name = "TestApp"
    sg.runner.session_service = AsyncMock()
    sg.runner.session_service.get_session = AsyncMock(return_value="ExistingSession")

    # Execute
    sentences = await sg.prepare_news_reading_text("MyTopic", "MyContent")

    # Verify
    assert sentences == [("joyful", "Hello World")]
    sg.body.change_emotion.assert_not_called()
    sg.body.queue_speak.assert_not_called()
    sg.body.wait_for_queue.assert_not_called()


@pytest.mark.asyncio
async def test_play_prepared_sentences_speaks_and_waits(mock_adk):
    # Setup
    mock_body = mock_adk["BodyClient"]()
    sg = SaintGraph(mock_body, "", "Instruction", mind_config={"speaker_id": 8})
    sg.body.change_emotion = AsyncMock()
    sg.body.queue_speak = AsyncMock(side_effect=[
        {"action_id": "speak-1"},
        {"action_id": "speak-2"},
    ])
    sg.body.wait_for_queue = AsyncMock()
    sg.body.wait_for_queue_strict = AsyncMock(return_value=True)

    # Execute
    action_id = await sg.play_prepared_sentences([("joyful", "Hello"), ("sad", "Bye")])

    # Verify
    from unittest.mock import call
    sg.body.change_emotion.assert_has_calls([
        call("joyful"),
        call("sad"),
        call("silent"),
    ])
    sg.body.queue_speak.assert_has_calls([
        call("Hello", style="joyful", speaker_id=8),
        call("Bye", style="sad", speaker_id=8),
    ])
    # auto_filler 並走中のハング回避のため、 全 queue ではなく自分が投入した
    # speak action_ids だけを strict 待ちする
    sg.body.wait_for_queue_strict.assert_called_once_with(
        action_ids=["speak-1", "speak-2"]
    )
    sg.body.wait_for_queue.assert_not_called()
    assert action_id == "speak-1"


@pytest.mark.asyncio
async def test_play_prepared_sentences_waits_only_for_own_speak_actions(mock_adk):
    """回帰テスト: process_turn / play_prepared_sentences の wait_after=True が
    全 action_queue の空を待つと、 auto_filler が並走している状況で永遠にハングする。
    自分が投入した speak action_ids だけを strict 待ちすることでハングを避ける。
    """
    mock_body = mock_adk["BodyClient"]()
    sg = SaintGraph(mock_body, "", "Instruction")
    sg.body.change_emotion = AsyncMock()
    sg.body.queue_speak = AsyncMock(side_effect=[
        {"action_id": "speak-A"},
        {"action_id": "speak-B"},
        {"action_id": "speak-C"},
    ])
    sg.body.wait_for_queue = AsyncMock()
    sg.body.wait_for_queue_strict = AsyncMock(return_value=True)

    await sg.play_prepared_sentences(
        [("neutral", "one"), ("joyful", "two"), ("sad", "three")]
    )

    # 全 queue を待つ wait_for_queue は呼ばれない（auto_filler ハング回避の本旨）
    sg.body.wait_for_queue.assert_not_called()
    # 投入した 3 件すべての action_id が strict 待ち対象に含まれる
    sg.body.wait_for_queue_strict.assert_called_once_with(
        action_ids=["speak-A", "speak-B", "speak-C"]
    )


@pytest.mark.asyncio
async def test_play_prepared_sentences_can_skip_wait(mock_adk):
    # Setup
    mock_body = mock_adk["BodyClient"]()
    sg = SaintGraph(mock_body, "", "Instruction")
    sg.body.change_emotion = AsyncMock()
    sg.body.queue_speak = AsyncMock(return_value={"action_id": "speak-1"})
    sg.body.wait_for_queue = AsyncMock()

    # Execute
    action_id = await sg.play_prepared_sentences([("neutral", "Hello")], wait_after=False)

    # Verify
    sg.body.change_emotion.assert_called_once_with("neutral")
    sg.body.queue_speak.assert_called_once_with("Hello", style="neutral", speaker_id=None)
    sg.body.wait_for_queue.assert_not_called()
    assert action_id == "speak-1"


@pytest.mark.asyncio
async def test_play_prepared_sentences_with_caption_attaches_caption_to_first_speak(mock_adk):
    mock_body = mock_adk["BodyClient"]()
    sg = SaintGraph(mock_body, "", "Instruction")
    sg.body.change_emotion = AsyncMock()
    sg.body.queue_speak = AsyncMock(side_effect=[
        {"action_id": "speak-1"},
        {"action_id": "speak-2"},
    ])
    sg.body.wait_for_queue = AsyncMock()

    action_ids = await sg.play_prepared_sentences_with_caption(
        [("neutral", "First"), ("joyful", "Second")],
        caption_title="Title",
        caption_summary="Summary",
        wait_after=False,
    )

    from unittest.mock import call
    sg.body.queue_speak.assert_has_calls([
        call(
            "First",
            style="neutral",
            speaker_id=None,
            caption_title="Title",
            caption_summary="Summary",
        ),
        call("Second", style="joyful", speaker_id=None),
    ])
    sg.body.wait_for_queue.assert_not_called()
    assert action_ids == ["speak-1", "speak-2"]

@pytest.mark.asyncio
async def test_high_level_process_methods(mock_adk):
    # Setup
    mock_body = mock_adk["BodyClient"]()
    templates = {
        "intro": "Welcome to my stream",
        "news_reading": "Here is {title}: {content}",
        "news_finished": "Done with news",
        "closing": "Bye bye"
    }
    sg = SaintGraph(mock_body, "", "Instruction", templates=templates)
    sg.process_turn = AsyncMock()

    # Execute & Verify process_intro
    await sg.process_intro()
    sg.process_turn.assert_called_with("Welcome to my stream", context="Intro")

    # Execute & Verify process_news_reading
    await sg.process_news_reading("MyTopic", "MyContent")
    sg.process_turn.assert_called_with(
        "Here is MyTopic: MyContent", context="News Reading: MyTopic", wait_after=True
    )

    # Execute & Verify process_news_finished
    await sg.process_news_finished()
    sg.process_turn.assert_called_with("Done with news", context="News Finished")

    # Execute & Verify process_closing
    await sg.process_closing()
    sg.process_turn.assert_called_with("Bye bye", context="Closing")

    await sg.process_closing(reason="technical_failure")
    closing_arg = sg.process_turn.call_args.args[0]
    assert "配信システムの技術的不具合" in closing_arg
    sg.process_turn.assert_called_with(closing_arg, context="Closing")

def test_config_defaults(monkeypatch):
    # Defaults
    monkeypatch.delenv("WEATHER_MCP_URL", raising=False)
    monkeypatch.delenv("BODY_URL", raising=False)
    cfg = Config()
    assert cfg.weather_mcp_url == "http://tools-weather:8001/sse"
    assert cfg.body_url == "http://localhost:8000"
    
def test_config_env_override(monkeypatch):
    monkeypatch.setenv("WEATHER_MCP_URL", "http://new-weather:8001/sse")
    monkeypatch.setenv("BODY_URL", "http://new-body:8000")
    cfg = Config()
    assert cfg.weather_mcp_url == "http://new-weather:8001/sse"
    assert cfg.body_url == "http://new-body:8000"

def test_config_cloud_run_warn_only(monkeypatch, caplog):
    # Ensure GOOGLE_API_KEY is set to avoid that failure
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy_key")

    # Simulate Cloud Run environment without required env
    monkeypatch.setenv("K_SERVICE", "test-service")
    monkeypatch.delenv("WEATHER_MCP_URL", raising=False)
    
    cfg = Config(google_api_key="dummy_key")

    # Should not raise SystemExit, but log a warning
    with caplog.at_level(logging.WARNING):
        cfg.validate()
    
    assert "WEATHER_MCP_URL is not set in Cloud Run environment" in caplog.text
    assert "MCP features will be disabled" in caplog.text

    # Now set it
    monkeypatch.setenv("WEATHER_MCP_URL", "http://ok/sse")
    cfg = Config(google_api_key="dummy_key")
    cfg.validate() # Should not raise

def test_config_missing_api_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    cfg = Config(google_api_key=None)
    with pytest.raises(SystemExit) as e:
        cfg.validate(force_exit=True)
    assert e.value.code == 1

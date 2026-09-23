import asyncio
import copy
from pathlib import Path

from sekka import client
from sekka.client import ChatResponse
from sekka.config import DEFAULT_CONFIG, Config
from sekka.tui import ConfigScreen, ConfirmScreen, SekkaApp


def make_config(**overrides):
    values = copy.deepcopy(DEFAULT_CONFIG)
    values.update(overrides)
    return Config(values)


async def wait_for(pilot, predicate, timeout=5.0):
    """Pause until predicate() is true (or fail loudly)."""
    for _ in range(int(timeout * 20)):
        await pilot.pause()
        if predicate():
            return
    raise AssertionError("condition never became true")


def history_text(app):
    return "\n".join(text for _, text in app.ui_lines)


def run_typing(pilot, text):
    async def go():
        for ch in text:
            await pilot.press(ch)
    return go()


# ---------------------------------------------------------------------------


def test_app_boots_with_history_and_input():
    async def go():
        app = SekkaApp(make_config(model="test-model"))
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            assert app.query_one("#history") is not None
            editor = app.query_one("#input")
            assert editor.has_focus
            assert "test-model" in app.title
    asyncio.run(go())


def test_help_command_lists_commands():
    async def go():
        app = SekkaApp(make_config(model="test-model"))
        async with app.run_test(size=(90, 30)) as pilot:
            await run_typing(pilot, "/help")
            await pilot.press("enter")
            await pilot.pause()
            text = history_text(app)
            assert "/save" in text and "/config" in text and "tok/s" not in text
            # input was cleared after submit
            assert app.query_one("#input").text == ""
    asyncio.run(go())


def test_chat_roundtrip_appends_stats():
    async def go():
        def fake_chat(endpoint, model, messages, **kwargs):
            # system prompt + user message
            assert messages[0]["role"] == "system"
            assert messages[-1] == {"role": "user", "content": "hello world"}
            return ChatResponse(content="general reply", completion_tokens=20, elapsed=2.0)

        original = client.chat_completion
        client.chat_completion = fake_chat
        try:
            app = SekkaApp(make_config(model="test-model"))
            async with app.run_test(size=(90, 30)) as pilot:
                await run_typing(pilot, "hello world")
                await pilot.press("enter")
                await wait_for(pilot, lambda: not app.busy and len(app.chat) == 2)
                assert app.chat == [
                    {"role": "user", "content": "hello world"},
                    {"role": "assistant", "content": "general reply"},
                ]
                text = history_text(app)
                assert "[2.0s, 10.0 tok/s]" in text
        finally:
            client.chat_completion = original
    asyncio.run(go())


def test_error_response_rolls_back_user_turn():
    async def go():
        def boom(endpoint, model, messages, **kwargs):
            raise client.ClientError("kaboom")

        original = client.chat_completion
        client.chat_completion = boom
        try:
            app = SekkaApp(make_config(model="test-model"))
            async with app.run_test(size=(90, 30)) as pilot:
                await run_typing(pilot, "are you ok?")
                await pilot.press("enter")
                await wait_for(pilot, lambda: not app.busy)
                assert app.chat == []  # user turn rolled back
                assert "kaboom" in history_text(app)
        finally:
            client.chat_completion = original
    asyncio.run(go())


def test_single_model_is_auto_selected():
    async def go():
        original = client.list_models
        client.list_models = lambda *a, **k: ["only-model"]
        try:
            app = SekkaApp(make_config(model=""))  # no model -> query endpoint
            async with app.run_test(size=(90, 30)) as pilot:
                await wait_for(pilot, lambda: app.config["model"] == "only-model")
                assert app.title.endswith("only-model")
        finally:
            client.list_models = original
    asyncio.run(go())


def test_save_command_writes_file_after_confirm(tmp_path):
    tmp_dir = str(tmp_path)

    async def go():
        app = SekkaApp(make_config(model="test-model", save_dir=tmp_dir))

        def fake_chat(*a, **k):
            return ChatResponse(content="reply", completion_tokens=5, elapsed=1.0)

        original = client.chat_completion
        client.chat_completion = fake_chat
        try:
            async with app.run_test(size=(90, 30)) as pilot:
                await run_typing(pilot, "hi")
                await pilot.press("enter")
                await wait_for(pilot, lambda: len(app.chat) == 2)

                await run_typing(pilot, "/save")
                await pilot.press("enter")
                await wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
                # confirm with "yes"
                app.screen.query_one("#yes").press()
                await wait_for(pilot, lambda: not isinstance(app.screen, ConfirmScreen))
        finally:
            client.chat_completion = original

        saved = list(Path(tmp_dir).glob("sekka_*.json"))
        assert len(saved) == 1
    asyncio.run(go())


def test_escape_closes_config_screen():
    async def go():
        app = SekkaApp(make_config(model="test-model"))
        async with app.run_test(size=(100, 35)) as pilot:
            await run_typing(pilot, "/config")
            await pilot.press("enter")
            await wait_for(pilot, lambda: isinstance(app.screen, ConfigScreen))
            await pilot.press("escape")
            await wait_for(pilot, lambda: not isinstance(app.screen, ConfigScreen))
            assert "Configuration saved" not in history_text(app)  # cancelled, not saved
    asyncio.run(go())


def test_escape_cancels_save_confirmation():
    async def go():
        app = SekkaApp(make_config(model="test-model"))
        async with app.run_test(size=(100, 35)) as pilot:
            app.chat.append({"role": "user", "content": "x"})
            await run_typing(pilot, "/save")
            await pilot.press("enter")
            await wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
            await pilot.press("escape")
            await wait_for(pilot, lambda: not isinstance(app.screen, ConfirmScreen))
            assert "(save cancelled)" in history_text(app)
    asyncio.run(go())


def test_custom_role_labels_are_used():
    async def go():
        def fake_chat(*a, **k):
            return ChatResponse(content="woof", completion_tokens=4, elapsed=0.5)

        original = client.chat_completion
        client.chat_completion = fake_chat
        try:
            cfg = make_config(model="test-model")
            cfg.values["labels"] = {"user": "Nick", "assistant": "Qwen"}
            app = SekkaApp(cfg)
            async with app.run_test(size=(90, 30)) as pilot:
                await run_typing(pilot, "hi")
                await pilot.press("enter")
                await wait_for(pilot, lambda: not app.busy and len(app.chat) == 2)
                text = history_text(app)
                assert "Nick:\nhi" in text
                assert "Qwen:\nwoof" in text
                assert "You:" not in text and "Assistant:" not in text
        finally:
            client.chat_completion = original
    asyncio.run(go())


def test_thinking_spinner_shown_then_removed():
    async def go():
        def slow_chat(*a, **k):
            import time as _t
            _t.sleep(0.4)
            return ChatResponse(content="done", completion_tokens=2, elapsed=0.4)

        original = client.chat_completion
        client.chat_completion = slow_chat
        try:
            app = SekkaApp(make_config(model="test-model"))
            async with app.run_test(size=(90, 30)) as pilot:
                await run_typing(pilot, "hello")
                await pilot.press("enter")
                await wait_for(pilot, lambda: app._thinking is not None)
                spinner = app._thinking
                assert spinner.is_mounted
                assert app._thinking_timer is not None  # animation running
                await wait_for(pilot, lambda: not app.busy and len(app.chat) == 2)
                assert app._thinking is None
                assert app._thinking_timer is None
                await wait_for(pilot, lambda: spinner.parent is None)  # detached from DOM
        finally:
            client.chat_completion = original
    asyncio.run(go())


def test_clear_command_resets_history():
    async def go():
        app = SekkaApp(make_config(model="test-model"))
        async with app.run_test(size=(90, 30)) as pilot:
            app.chat.append({"role": "user", "content": "x"})
            await run_typing(pilot, "/clear")
            await pilot.press("enter")
            await pilot.pause()
            assert app.chat == []
            assert "(history cleared)" in history_text(app)
    asyncio.run(go())


def test_unknown_command_reports_error():
    async def go():
        app = SekkaApp(make_config(model="test-model"))
        async with app.run_test(size=(90, 30)) as pilot:
            await run_typing(pilot, "/wat")
            await pilot.press("enter")
            await pilot.pause()
            assert "Unknown command" in history_text(app)
    asyncio.run(go())

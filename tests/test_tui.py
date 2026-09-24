import asyncio
import copy
import json
from pathlib import Path

from sekka import client
from sekka.client import ChatResponse, ModelInfo
from sekka.config import DEFAULT_CONFIG, Config
from textual.widgets import Button, Checkbox, Input, Select

from sekka.tui import ConfigScreen, ConfirmScreen, KnowledgeScreen, SekkaApp


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


def test_padded_reply_renders_tight():
    async def go():
        def fake(*a, **k):
            return ChatResponse(content="\n\nHello there.\n\n", completion_tokens=3, elapsed=0.5)

        old = client.chat_completion
        client.chat_completion = fake
        try:
            app = SekkaApp(make_config(model="test-model"))
            async with app.run_test(size=(90, 30)) as pilot:
                await run_typing(pilot, "hi")
                await pilot.press("enter")
                await wait_for(pilot, lambda: len(app.chat) == 2)
                text = history_text(app)
                assert "Assistant:\nHello there." in text
                assert "\n\nHello" not in text
                assert app.chat[-1]["content"] == "\n\nHello there.\n\n"  # context keeps raw
        finally:
            client.chat_completion = old
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
        client.list_models = lambda *a, **k: [ModelInfo("only-model", 262144)]
        try:
            app = SekkaApp(make_config(model=""))  # no model -> query endpoint
            async with app.run_test(size=(90, 30)) as pilot:
                await wait_for(pilot, lambda: app.config["model"] == "only-model")
                assert app.title.endswith("only-model")
                assert app.context_total == 262144  # picked up max_model_len
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
            app.full_chat.append({"role": "user", "content": "x"})
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


def test_config_screen_scrollable_on_small_terminal():
    async def go():
        app = SekkaApp(make_config(model="test-model"))
        async with app.run_test(size=(70, 16)) as pilot:
            await run_typing(pilot, "/config")
            await pilot.press("enter")
            await wait_for(pilot, lambda: isinstance(app.screen, ConfigScreen))
            screen = app.screen
            save = screen.query_one("#config_save")
            assert 0 <= save.region.y < 16  # buttons always visible
            scroller = screen.query_one("#cfg_scroll")
            await pilot.press("pagedown")
            await pilot.pause()
            assert scroller.scroll_y > 0  # keyboard scrolling works
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


# ---- context meter & full-context strategies ----


def fill_chat(app, rounds=6):
    """Simulate past exchanges: both the model context and the visible log."""
    for i in range(rounds):
        for m in ({"role": "user", "content": "x" * 4000},
                  {"role": "assistant", "content": "y" * 4000}):
            app.chat.append(m)
            app.full_chat.append(m)


def test_context_meter_shows_used_and_total():
    async def go():
        def fake_chat(*a, **k):
            return ChatResponse(content="fine", prompt_tokens=100, completion_tokens=20, elapsed=1.0)

        old_chat, old_models = client.chat_completion, client.list_models
        client.chat_completion = fake_chat
        client.list_models = lambda *a, **k: [ModelInfo("only-model", 262144)]
        try:
            app = SekkaApp(make_config(model=""))
            async with app.run_test(size=(90, 30)) as pilot:
                await wait_for(pilot, lambda: app.config["model"] == "only-model")
                await run_typing(pilot, "hi")
                await pilot.press("enter")
                await wait_for(pilot, lambda: not app.busy and len(app.chat) == 2)
                assert app.ctx_label == "120/262k"
        finally:
            client.chat_completion, client.list_models = old_chat, old_models
    asyncio.run(go())


def test_pause_mode_blocks_when_full():
    async def go():
        app = SekkaApp(make_config(model="test-model", context_window=4096, context_mode="pause"))
        async with app.run_test(size=(90, 30)) as pilot:
            fill_chat(app)
            await run_typing(pilot, "will this get through?")
            await pilot.press("enter")
            await pilot.pause()
            assert "Context window full" in history_text(app)
            assert len(app.chat) == 12  # message not appended
    asyncio.run(go())


def test_rolling_mode_drops_oldest():
    async def go():
        def fake_chat(*a, **k):
            return ChatResponse(content="ok", completion_tokens=5, elapsed=0.5)

        old = client.chat_completion
        client.chat_completion = fake_chat
        try:
            app = SekkaApp(make_config(model="test-model", context_window=4096, context_mode="rolling"))
            async with app.run_test(size=(90, 30)) as pilot:
                fill_chat(app)
                await run_typing(pilot, "fresh question")
                await pilot.press("enter")
                await wait_for(pilot, lambda: app.chat and app.chat[-1]["content"] == "ok")
                assert "dropped" in app.notice_text  # status bar, not chat
                assert "dropped" not in history_text(app)
                assert len(app.chat) < 13  # old turns were dropped
                # the newest exchange survived
                assert {"role": "user", "content": "fresh question"} in app.chat
        finally:
            client.chat_completion = old
    asyncio.run(go())


def test_compact_mode_summarizes_old_messages():
    async def go():
        def fake_chat(endpoint, model, messages, **k):
            if messages[0]["content"] == "You compress conversations.":
                return ChatResponse(content="They talked about weather.", completion_tokens=8, elapsed=0.2)
            return ChatResponse(content="post-compact reply", completion_tokens=5, elapsed=0.5)

        old = client.chat_completion
        client.chat_completion = fake_chat
        try:
            app = SekkaApp(make_config(model="test-model", context_window=4096, context_mode="compact"))
            async with app.run_test(size=(90, 30)) as pilot:
                fill_chat(app)
                await run_typing(pilot, "continue our chat")
                await pilot.press("enter")
                await wait_for(pilot, lambda: app.chat and app.chat[-1]["content"] == "post-compact reply")
                assert app.chat[0]["role"] == "system"
                assert app.chat[0]["content"].startswith("[Summary of earlier conversation]")
                assert "They talked about weather." in app.chat[0]["content"]
                assert "compacted" in app.notice_text
                assert "compacted" not in history_text(app)
        finally:
            client.chat_completion = old
    asyncio.run(go())


def test_save_keeps_messages_that_rolled_out(tmp_path):
    async def go():
        def fake_chat(*a, **k):
            return ChatResponse(content="ok", completion_tokens=5, elapsed=0.5)

        old = client.chat_completion
        client.chat_completion = fake_chat
        try:
            cfg = make_config(model="test-model", context_window=4096,
                              context_mode="rolling", save_dir=str(tmp_path))
            app = SekkaApp(cfg)
            async with app.run_test(size=(90, 30)) as pilot:
                fill_chat(app)  # 12 messages
                await run_typing(pilot, "the one that causes rolling")
                await pilot.press("enter")
                await wait_for(pilot, lambda: app.chat and app.chat[-1]["content"] == "ok")
                assert len(app.chat) < len(app.full_chat)  # context was rolled

                await run_typing(pilot, "/save")
                await pilot.press("enter")
                await wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
                app.screen.query_one("#yes").press()
                await wait_for(pilot, lambda: not isinstance(app.screen, ConfirmScreen))

            saved = list(Path(tmp_path).glob("sekka_*.json"))
            assert len(saved) == 1
            payload = json.loads(saved[0].read_text())
            assert len(payload["messages"]) == 14  # everything said, incl. rolled-out turns
            assert any(m["content"] == "the one that causes rolling" for m in payload["messages"])
        finally:
            client.chat_completion = old
    asyncio.run(go())


# ---------------------------------------------------- reasoning + knowledge


def test_reasoning_and_knowledge_config_validation():
    import pytest

    from sekka.config import ConfigError, validate_config

    for good in ("none", "minimal", "low", "medium", "high"):
        v = json.loads(json.dumps(DEFAULT_CONFIG))
        v["reasoning"] = good
        validate_config(v)
    bad = json.loads(json.dumps(DEFAULT_CONFIG))
    bad["reasoning"] = "ultra"
    with pytest.raises(ConfigError, match="reasoning"):
        validate_config(bad)

    good = json.loads(json.dumps(DEFAULT_CONFIG))
    good["knowledge"] = [{"file": "a.md", "description": "A thing", "enabled": True}]
    validate_config(good)
    for bad_entries in ([{"file": ""}], [{"description": "x"}], [{"file": "a", "description": "d"}], "nope"):
        bad = json.loads(json.dumps(DEFAULT_CONFIG))
        bad["knowledge"] = bad_entries
        with pytest.raises(ConfigError, match="knowledge"):
            validate_config(bad)


def test_knowledge_tools_only_enabled_and_no_args():
    app = SekkaApp(make_config(model="m", knowledge=[
        {"file": "docs/credit card policy.md", "description": "CC policy", "enabled": True},
        {"file": "secret.txt", "description": "nope", "enabled": False},
    ]))
    schemas, files = app._knowledge_tools()
    assert len(schemas) == 1
    fn = schemas[0]["function"]
    assert fn["name"] == "read_credit_card_policy"
    assert "CC policy" in fn["description"]
    assert fn["parameters"]["properties"] == {}  # model cannot pass a path
    assert list(files) == ["read_credit_card_policy"]
    assert files["read_credit_card_policy"].endswith("credit card policy.md")
    assert "secret" not in json.dumps(files)


def test_tool_roundtrip_reads_only_whitelisted_file(tmp_path):
    good = tmp_path / "kb.md"
    good.write_text("POLICY BODY")

    async def go():
        calls = []

        def fake(endpoint, model, messages, **kwargs):
            calls.append(list(messages))
            if len(calls) == 1:
                return ChatResponse(
                    content="", completion_tokens=5, elapsed=0.1,
                    tool_calls=[{"id": "c1", "function": {"name": "read_kb", "arguments": json.dumps({"file": "/etc/passwd"})}},
                                {"id": "c2", "function": {"name": "read_evil", "arguments": "{}"}}],
                    message={"role": "assistant", "content": None,
                             "tool_calls": [{"id": "c1", "function": {"name": "read_kb", "arguments": "{}"}},
                                            {"id": "c2", "function": {"name": "read_evil", "arguments": "{}"}}]},
                )
            return ChatResponse(content="answered from KB", completion_tokens=4, elapsed=0.2)

        old = client.chat_completion
        client.chat_completion = fake
        try:
            app = SekkaApp(make_config(
                model="m",
                knowledge=[{"file": str(good), "description": "the kb", "enabled": True}],
            ))
            async with app.run_test(size=(90, 30)) as pilot:
                await run_typing(pilot, "what is the policy?")
                await pilot.press("enter")
                await wait_for(pilot, lambda: app.chat and app.chat[-1]["content"] == "answered from KB")
                second = calls[1]
                tool_msgs = [m for m in second if m.get("role") == "tool"]
                # whitelisted file returned (arguments ignored - even /etc/passwd)
                assert any("POLICY BODY" in m["content"] for m in tool_msgs)
                # hallucinated tool name -> refused, nothing read
                assert any("unknown tool" in m["content"] for m in tool_msgs)
                assert "root:" not in json.dumps(second)  # never read an arbitrary file
                assert "tool call: read_kb" in history_text(app)
        finally:
            client.chat_completion = old
    asyncio.run(go())


def test_reasoning_hidden_then_ctrl_t_shows_including_past():
    async def go():
        def fake(*a, **k):
            return ChatResponse(content="answer", reasoning="deep thoughts here",
                                completion_tokens=3, elapsed=0.5)

        old = client.chat_completion
        client.chat_completion = fake
        try:
            app = SekkaApp(make_config(model="test-model"))
            async with app.run_test(size=(90, 30)) as pilot:
                await run_typing(pilot, "hi")
                await pilot.press("enter")
                await wait_for(pilot, lambda: app.chat and app.chat[-1]["content"] == "answer")
                assert "deep thoughts here" in history_text(app)      # logged
                assert not app.show_thinking                            # hidden at start
                from textual.widgets import Static
                hidden = [w for w in app.query(Static) if "msg-reasoning" in w.classes]
                assert hidden and all(w.styles.display == "none" for w in hidden)

                await pilot.press("ctrl+t")
                assert app.show_thinking
                shown = [w for w in app.query(Static) if "msg-reasoning" in w.classes]
                assert shown and all(w.styles.display == "block" for w in shown)
                # toggling back hides *earlier* turns too
                await pilot.press("ctrl+t")
                assert all(w.styles.display == "none" for w in app.query(Static) if "msg-reasoning" in w.classes)
        finally:
            client.chat_completion = old
    asyncio.run(go())


def test_escape_clears_input():
    async def go():
        app = SekkaApp(make_config(model="test-model"))
        async with app.run_test(size=(90, 30)) as pilot:
            await run_typing(pilot, "draft message")
            assert app.query_one("#input").text == "draft message"
            await pilot.press("escape")
            assert app.query_one("#input").text == ""
    asyncio.run(go())


def test_knowledge_browse_opens_file_browser(tmp_path):
    from textual.widgets import Button

    async def go():
        from sekka.tui import FileBrowseScreen, KnowledgeScreen

        app = SekkaApp(make_config(model="m"))
        async with app.run_test(size=(110, 36)) as pilot:
            await run_typing(pilot, "/knowledge")
            await pilot.press("enter")
            await wait_for(pilot, lambda: isinstance(app.screen, KnowledgeScreen))
            app.screen.query_one("#k_browse", Button).press()
            await wait_for(pilot, lambda: isinstance(app.screen, FileBrowseScreen))
            await pilot.press("escape")
            await wait_for(pilot, lambda: isinstance(app.screen, KnowledgeScreen))
    asyncio.run(go())


def test_ctrl_c_needs_two_presses_to_quit():
    async def go():
        app = SekkaApp(make_config(model="test-model"))
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert not app._exit  # armed but alive
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert app._exit  # second press within the window quits
    asyncio.run(go())


def test_knowledge_screen_add_enable_and_persist(tmp_path):
    cfg_file = tmp_path / "config.json"
    kb = tmp_path / "kb.md"
    kb.write_text("body")

    async def go():
        from textual.widgets import Button, Checkbox, Input

        from sekka.config import save_config
        from sekka.tui import KnowledgeScreen

        values = copy.deepcopy(DEFAULT_CONFIG)
        app = SekkaApp(Config(values, path=cfg_file))
        async with app.run_test(size=(110, 36)) as pilot:
            await run_typing(pilot, "/knowledge")
            await pilot.press("enter")
            await wait_for(pilot, lambda: isinstance(app.screen, KnowledgeScreen))
            screen = app.screen

            screen.query_one("#k_path", Input).value = str(kb)
            await pilot.pause()
            from textual.color import Color as TuiColor
            assert screen.query_one("#k_path", Input).styles.color == TuiColor.parse("green")

            screen.query_one("#k_desc", Input).value = "insurance verification process doc"
            screen.query_one("#k_add", Button).press()
            await wait_for(pilot, lambda: len(app.config["knowledge"]) == 1)
            entry = app.config["knowledge"][0]
            assert entry["enabled"] is False  # remembered but OFF until ticked
            assert cfg_file.exists()          # persisted immediately

            # tick the checkbox -> enabled + persisted
            screen.query_one("#k_en_0", Checkbox).toggle()
            await pilot.pause()
            assert app.config["knowledge"][0]["enabled"] is True
            saved = json.loads(cfg_file.read_text())
            assert saved["knowledge"][0]["enabled"] is True
            schemas, _files = app._knowledge_tools()
            assert len(schemas) == 1

            screen.query_one("#k_close", Button).press()
            await wait_for(pilot, lambda: not isinstance(app.screen, KnowledgeScreen))
    asyncio.run(go())


def test_config_screen_save_format_and_reasoning_applied(tmp_path):
    cfg_file = tmp_path / "config.json"

    async def go():
        values = copy.deepcopy(DEFAULT_CONFIG)
        app = SekkaApp(Config(values, path=cfg_file))
        async with app.run_test(size=(110, 40)) as pilot:
            await run_typing(pilot, "/config")
            await pilot.press("enter")
            await wait_for(pilot, lambda: isinstance(app.screen, ConfigScreen))
            screen = app.screen
            screen.query_one("#cfg_save_format", Select).value = "markdown"
            screen.query_one("#cfg_reasoning", Select).value = "high"
            screen.query_one("#config_save", Button).press()
            await wait_for(pilot, lambda: not isinstance(app.screen, ConfigScreen))
            assert app.config["save_format"] == "markdown"
            assert app.config["reasoning"] == "high"
            saved = json.loads(cfg_file.read_text())
            assert saved["save_format"] == "markdown" and saved["reasoning"] == "high"
    asyncio.run(go())

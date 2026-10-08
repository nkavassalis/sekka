import asyncio
import copy
import json
from pathlib import Path

from sekka import client, commands
from sekka.client import ChatResponse, ModelInfo
from sekka.config import DEFAULT_CONFIG, Config
from textual.widgets import Button, Checkbox, Input, ListItem, Select

_orig_stream = client.stream_chat_completion

from sekka.tui import ConfigScreen, ConfirmScreen, KnowledgeScreen, SekkaApp


def make_config(**overrides):
    # most TUI tests fake client.chat_completion; they opt out of streaming and
    # dedicated tests cover the streamed path (and a real SSE server covers it functionally)
    values = copy.deepcopy(DEFAULT_CONFIG)
    values["stream"] = False
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
                # summary lives in the system prompt, never mid-chat
                assert all(m["role"] != "system" for m in app.chat)
                assert app.chat[0]["role"] == "user"
                assert "They talked about weather." in app._system_text()
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


# ------------------------------------------------------------------- resume


def test_resume_file_loads_session(tmp_path):
    from sekka.storage import save_history

    session = save_history(
        [{"role": "user", "content": "earlier question"},
         {"role": "assistant", "content": "earlier answer"}],
        directory=tmp_path,
    )

    async def go():
        app = SekkaApp(make_config(model="m"), resume=str(session))
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            assert len(app.chat) == 2
            assert app.chat[0]["content"] == "earlier question"
            assert len(app.full_chat) == 2
            text = history_text(app)
            assert "earlier question" in text and "earlier answer" in text
            assert "resumed 2 messages" in text
    asyncio.run(go())


def test_resume_invalid_file_shows_error_and_keeps_app_usable(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"messages": [{"role": "definitely-not-real", "content": "x"}]}')

    async def go():
        app = SekkaApp(make_config(model="m"), resume=str(bad))
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            assert app.chat == []
            assert "unsupported role" in history_text(app)
    asyncio.run(go())


def test_resume_picker_uses_save_dir_and_loads_choice(tmp_path):
    from sekka.storage import save_history
    from sekka.tui import ResumeScreen

    save_history([{"role": "user", "content": "pick me"}], directory=tmp_path)

    async def go():
        app = SekkaApp(
            make_config(model="m", save_dir=str(tmp_path)), resume=""
        )
        async with app.run_test(size=(90, 30)) as pilot:
            await wait_for(pilot, lambda: isinstance(app.screen, ResumeScreen))
            assert app.screen.paths and app.screen.paths[0].parent == tmp_path
            await pilot.press("enter")
            await wait_for(pilot, lambda: not isinstance(app.screen, ResumeScreen))
            assert app.chat == [{"role": "user", "content": "pick me"}]
    asyncio.run(go())


def test_resume_picker_empty_dir_reports_and_continues(tmp_path):
    async def go():
        app = SekkaApp(
            make_config(model="m", save_dir=str(tmp_path / "nothing_here")), resume=""
        )
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            assert "No saved sessions" in history_text(app)
            assert app.chat == []
    asyncio.run(go())


def test_model_screen_handles_weird_model_ids():
    from sekka.client import ModelInfo
    from sekka.tui import ModelScreen

    async def go():
        def fake_models(endpoint, api_key=""):
            return [
                ModelInfo("org/model:v1", 8192),
                ModelInfo("other", None),
            ]

        old = client.list_models
        client.list_models = fake_models
        try:
            app = SekkaApp(make_config(model=""))  # no model -> picker at boot
            async with app.run_test(size=(90, 30)) as pilot:
                await wait_for(pilot, lambda: isinstance(app.screen, ModelScreen))
                assert len(list(app.screen.query(ListItem))) == 2
                await pilot.press("enter")  # pick the id with ':' and '/' in it
                await wait_for(pilot, lambda: app.config["model"] == "org/model:v1")
                assert app.context_total == 8192
        finally:
            client.list_models = old
    asyncio.run(go())


# ---------------------------------------------------------------- review fixes


def _fake(content="ok", **kw):
    def fake_chat(endpoint, model, messages, **k):
        fake_chat.calls.append(messages)
        return ChatResponse(content=content, completion_tokens=5, elapsed=0.1, **kw)
    fake_chat.calls = []
    return fake_chat


def test_brackets_in_text_do_not_crash_or_get_styled():
    async def go():
        old = client.chat_completion
        client.chat_completion = _fake("*grins* [OOC: roll d20] [/b] [bold]door")
        try:
            app = SekkaApp(make_config(model="m"))
            async with app.run_test(size=(90, 30)) as pilot:
                await run_typing(pilot, "hi [/i] there")
                await pilot.press("enter")
                await wait_for(pilot, lambda: len(app.chat) == 2)
                await pilot.pause()
                text = "\n".join(str(w.render()) for w in app.query("Static.msg-assistant"))
                assert "[OOC: roll d20] [/b] [bold]door" in text
        finally:
            client.chat_completion = old
    asyncio.run(go())


def test_api_error_removes_user_turn_from_saved_history_too():
    async def go():
        def boom(*a, **k):
            raise client.ClientError("down")
        old = client.chat_completion
        client.chat_completion = boom
        try:
            app = SekkaApp(make_config(model="m"))
            async with app.run_test(size=(90, 30)) as pilot:
                await run_typing(pilot, "hello")
                await pilot.press("enter")
                await wait_for(pilot, lambda: not app.busy and any(r == "error" for r, _ in app.ui_lines))
                assert app.chat == [] and app.full_chat == []
        finally:
            client.chat_completion = old
    asyncio.run(go())


def test_autosave_reuses_one_file(tmp_path):
    async def go():
        old = client.chat_completion
        client.chat_completion = _fake("reply")
        try:
            app = SekkaApp(make_config(model="m", autosave=True, save_dir=str(tmp_path)))
            async with app.run_test(size=(90, 30)) as pilot:
                for word in ("one", "two", "three"):
                    await run_typing(pilot, word)
                    await pilot.press("enter")
                    await wait_for(pilot, lambda w=word: not app.busy and app.chat and app.chat[-2]["content"] == w)
                files = list(tmp_path.glob("sekka_*.json"))
                assert len(files) == 1
                assert len(json.loads(files[0].read_text())["messages"]) == 6
        finally:
            client.chat_completion = old
    asyncio.run(go())


def test_rolling_never_starts_context_with_assistant():
    app = SekkaApp(make_config(model="m"))
    for i in range(6):
        app.chat.append({"role": "user", "content": "u" * 400})
        app.chat.append({"role": "assistant", "content": "a" * 400})
    app._roll_context(need=10_000, limit=500)
    assert app.chat[0]["role"] == "user"


def test_resume_folds_system_notes_into_system_prompt(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"messages": [
        {"role": "system", "content": "[Summary of earlier conversation]\nthe party met a dragon"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
    ]}))
    async def go():
        app = SekkaApp(make_config(model="m"), resume=str(f))
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            assert all(m["role"] != "system" for m in app.chat)
            assert "the party met a dragon" in app._system_text()
            assert len(app.full_chat) == 3
    asyncio.run(go())


def test_knowledge_read_once_then_kept_in_system_context(tmp_path):
    lore = tmp_path / "ünï-lore.md"
    lore.write_text("DRAGONS ARE BLUE")
    async def go():
        calls = []
        def fake_chat(endpoint, model, messages, **k):
            calls.append((messages, k.get("tools")))
            if len(calls) == 1:
                msg = {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "1", "type": "function", "function": {"name": k["tools"][0]["function"]["name"], "arguments": "{}"}}]}
                return ChatResponse(content="", tool_calls=msg["tool_calls"], message=msg, elapsed=0.1)
            return ChatResponse(content="answer", completion_tokens=3, elapsed=0.1)
        old = client.chat_completion
        client.chat_completion = fake_chat
        try:
            cfg = make_config(model="m", knowledge=[{"file": str(lore), "description": "d", "enabled": True}])
            app = SekkaApp(cfg)
            async with app.run_test(size=(90, 30)) as pilot:
                import re as _re
                await run_typing(pilot, "one")
                await pilot.press("enter")
                await wait_for(pilot, lambda: not app.busy and len(app.chat) == 2)
                name = calls[0][1][0]["function"]["name"]
                assert _re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name)
                n = len(calls)
                await run_typing(pilot, "two")
                await pilot.press("enter")
                await wait_for(pilot, lambda: not app.busy and len(app.chat) == 4)
                assert len(calls) == n + 1          # no second tool round trip
                msgs, tools = calls[-1]
                assert not tools                     # already loaded, not offered again
                assert "DRAGONS ARE BLUE" in msgs[0]["content"]
        finally:
            client.chat_completion = old
    asyncio.run(go())


# ---------------------------------------------------------------- streaming


def fake_stream(chunks, delay=0.0, **fixed):
    """Stand-in for client.stream_chat_completion: yields cumulative progress."""
    def fake(endpoint, model, messages, **kwargs):
        fake.seen_kwargs = kwargs
        text = ""
        reasoning = ""
        for kind, piece in chunks:
            if kind == "content":
                text += piece
            else:
                reasoning += piece
            yield client.StreamEvent(content=text, reasoning=reasoning)
            if delay:
                import time as _t; _t.sleep(delay)
        msg = {"role": "assistant", "content": text or None}
        if fixed.get("tool_calls"):
            msg["tool_calls"] = fixed["tool_calls"]
        yield client.StreamEvent(
            content=text, reasoning=reasoning, message=msg,
            prompt_tokens=fixed.get("prompt", 20), completion_tokens=fixed.get("completion", 4),
            elapsed=0.3, stopped=fixed.get("stopped", False),
        )
    fake.seen_kwargs = {}
    return fake


def test_reply_streams_into_the_view_token_by_token():
    async def go():
        old = client.stream_chat_completion
        client.stream_chat_completion = fake_stream(
            [("content", "One "), ("content", "two "), ("content", "three.")], delay=0.2)
        try:
            app = SekkaApp(make_config(model="m", stream=True))
            async with app.run_test(size=(90, 30)) as pilot:
                await run_typing(pilot, "go")
                await pilot.press("enter")
                await wait_for(pilot, lambda: app._stream_widget is not None)
                first = str(app._stream_widget.render())
                assert first.startswith("Assistant:\n") and "One two" not in first, (
                    f"expected a partial line, got {first!r}"
                )
                await wait_for(pilot, lambda: not app.busy)
                assert app.chat[-1]["content"] == "One two three."
                assert "Assistant:\nOne two three." in history_text(app)
                assert app._thinking_timer is None  # snowflake gone once tokens land
        finally:
            client.stream_chat_completion = old
    asyncio.run(go())


def test_ctrl_x_keeps_partial_reply():
    async def go():
        old = client.stream_chat_completion
        client.stream_chat_completion = fake_stream(
            [("content", "Alpha "), ("content", "Beta "), ("content", "Gamma")], delay=0.2)
        try:
            app = SekkaApp(make_config(model="m", stream=True))
            async with app.run_test(size=(90, 30)) as pilot:
                await run_typing(pilot, "go")
                await pilot.press("enter")
                await wait_for(pilot, lambda: app._stream_widget is not None)
                await pilot.press("ctrl+x")
                await wait_for(pilot, lambda: not app.busy, timeout=3.0)
                assert app.chat[-1]["content"].startswith("Alpha")
                assert "[stopped]" in app.chat[-1]["content"]
                assert "[stopped]" in history_text(app)
        finally:
            client.stream_chat_completion = old
    asyncio.run(go())


def test_ctrl_x_frees_the_ui_while_the_endpoint_is_quiet():
    """Stop must release the editor even when no more tokens are arriving."""
    async def go():
        def fake(endpoint, model, messages, **kwargs):
            yield client.StreamEvent(content="Partial")
            import time as _t
            _t.sleep(4)  # endpoint goes silent; client cannot be interrupted here
            yield client.StreamEvent(content="Partial Late")
        old = client.stream_chat_completion
        client.stream_chat_completion = fake
        try:
            app = SekkaApp(make_config(model="m", stream=True))
            async with app.run_test(size=(90, 30)) as pilot:
                await run_typing(pilot, "go")
                await pilot.press("enter")
                await wait_for(pilot, lambda: app._stream_widget is not None)
                await pilot.press("ctrl+x")
                await wait_for(pilot, lambda: not app.busy, timeout=2.0)
                assert "Partial" in app.chat[-1]["content"]
                assert "Late" not in app.chat[-1]["content"]
        finally:
            client.stream_chat_completion = old
    asyncio.run(go())


def test_stop_command_exists():
    assert commands.resolve_command("stop") == "stop"


def test_scrolled_up_history_is_not_yanked_by_new_replies():
    async def go():
        old = client.stream_chat_completion
        client.stream_chat_completion = fake_stream([("content", "x" * 2000)])
        try:
            app = SekkaApp(make_config(model="m", stream=True))
            async with app.run_test(size=(90, 24)) as pilot:
                for i in range(6):
                    await run_typing(pilot, f"filler message number {i} with some text")
                    await pilot.press("enter")
                    await wait_for(pilot, lambda: not app.busy)
                history = app.query_one("#history")
                await pilot.press("pageup")
                await pilot.press("pageup")
                parked = history.scroll_y
                assert parked < history.virtual_size.height - history.region.height - 1
                await run_typing(pilot, "triggers a reply")
                await pilot.press("enter")
                await wait_for(pilot, lambda: not app.busy)
                assert history.scroll_y == parked, "view was pulled to the bottom"
                # and once back at the bottom it follows again
                app.action_history_scroll_down()
                history.scroll_end(animate=False)
                await run_typing(pilot, "second")
                await pilot.press("enter")
                await wait_for(pilot, lambda: not app.busy)
                assert history.scroll_y >= history.virtual_size.height - history.region.height - 1
        finally:
            client.stream_chat_completion = old
    asyncio.run(go())


def test_config_screen_exposes_stream_toggle():
    async def go():
        app = SekkaApp(make_config(model="m", stream=True))
        async with app.run_test(size=(100, 40)) as pilot:
            await run_typing(pilot, "/config")
            await pilot.press("enter")
            await wait_for(pilot, lambda: isinstance(app.screen, ConfigScreen))
            screen = app.screen
            box = screen.query_one("#cfg_stream", Checkbox)
            assert box.value is True
            box.value = False
            screen.query_one("#config_save", Button).press()
            await wait_for(pilot, lambda: app.config["stream"] is False)
            assert app.config["stream"] is False
    asyncio.run(go())


# --------------------------------------------------- turn editing (RP loop)


async def _one_exchange(pilot, app, prompt="tell me a story", reply="Once upon a time."):
    client.stream_chat_completion = fake_stream([("content", reply)])
    await run_typing(pilot, prompt)
    await pilot.press("enter")
    await wait_for(pilot, lambda: not app.busy and len(app.chat) == 2)


def test_undo_removes_the_last_exchange():
    async def go():
        try:
            app = SekkaApp(make_config(model="m", stream=True))
            async with app.run_test(size=(90, 30)) as pilot:
                await _one_exchange(pilot, app)
                await run_typing(pilot, "/undo")
                await pilot.press("enter")
                await wait_for(pilot, lambda: app.full_chat == [])
                assert app.chat == []
                assert "Once upon a time." not in history_text(app)
        finally:
            client.stream_chat_completion = None
            old = getattr(client, "stream_chat_completion", None)
    asyncio.run(go())


def test_edit_puts_the_last_message_back_in_the_input():
    async def go():
        try:
            app = SekkaApp(make_config(model="m", stream=True))
            async with app.run_test(size=(90, 30)) as pilot:
                await _one_exchange(pilot, app, prompt="the original line")
                await run_typing(pilot, "/edit")
                await pilot.press("enter")
                await wait_for(pilot, lambda: app.full_chat == [])
                editor = app.query_one("#input")
                assert editor.text == "the original line"
                # resend it and it lands as a fresh exchange
                client.stream_chat_completion = fake_stream([("content", "second version")])
                await pilot.press("enter")
                await wait_for(pilot, lambda: not app.busy and len(app.chat) == 2)
                assert app.chat[0]["content"] == "the original line"
                assert app.chat[-1]["content"] == "second version"
        finally:
            pass
    asyncio.run(go())


def test_regen_keeps_alternatives_and_swipe_cycles_them():
    async def go():
        app = SekkaApp(make_config(model="m", stream=True))
        client.stream_chat_completion = fake_stream([("content", "version A")])
        try:
            async with app.run_test(size=(90, 30)) as pilot:
                await run_typing(pilot, "go")
                await pilot.press("enter")
                await wait_for(pilot, lambda: not app.busy and len(app.chat) == 2)
                client.stream_chat_completion = fake_stream([("content", "version B")])
                await run_typing(pilot, "/regen")
                await pilot.press("enter")
                await wait_for(pilot, lambda: not app.busy and len(app.turn_alts) == 2)
                assert app.chat[-1]["content"] == "version B"
                assert "version A" not in history_text(app)
                await run_typing(pilot, "/swipe")
                await pilot.press("enter")
                await wait_for(pilot, lambda: app.chat[-1]["content"] == "version A")
                assert app.full_chat[-1]["content"] == "version A"
                assert "version A" in history_text(app)
                await run_typing(pilot, "/swipe")
                await pilot.press("enter")
                await wait_for(pilot, lambda: app.chat[-1]["content"] == "version B")
                # context sent next turn uses the swiped version
                assert app.chat[-1]["content"] == "version B"
        finally:
            client.stream_chat_completion = _orig_stream
    asyncio.run(go())


def test_swipe_and_regen_without_alternatives_are_polite():
    async def go():
        app = SekkaApp(make_config(model="m", stream=True))
        client.stream_chat_completion = fake_stream([("content", "only one")])
        try:
            async with app.run_test(size=(90, 30)) as pilot:
                await run_typing(pilot, "/swipe")
                await pilot.press("enter")
                await wait_for(pilot, lambda: "Only one version" in history_text(app))
                await run_typing(pilot, "/undo")
                await pilot.press("enter")
                await wait_for(pilot, lambda: "Nothing to undo." in history_text(app))
        finally:
            client.stream_chat_completion = _orig_stream
    asyncio.run(go())


def test_turn_edit_commands_are_listed_in_help():
    async def go():
        client.stream_chat_completion = fake_stream([("content", "x")])
        try:
            app = SekkaApp(make_config(model="m", stream=True))
            async with app.run_test(size=(90, 30)) as pilot:
                await run_typing(pilot, "/help")
                await pilot.press("enter")
                await wait_for(pilot, lambda: "/swipe" in history_text(app))
                for name in ("/undo", "/edit", "/regen"):
                    assert name in history_text(app)
        finally:
            client.stream_chat_completion = _orig_stream
    asyncio.run(go())


# ---------------------------------------------------------------- campaigns


def write_campaign(dirpath, **values):
    dirpath.mkdir(parents=True, exist_ok=True)
    path = dirpath / "campaign.json"
    path.write_text(json.dumps(values))
    return path


def test_campaign_greeting_opens_the_scene_and_enters_context():
    async def go():
        app = SekkaApp(make_config(model="m", greeting="The tavern is smoky tonight."))
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            assert app.chat[0] == {"role": "assistant", "content": "The tavern is smoky tonight."}
            assert "The tavern is smoky tonight." in history_text(app)
            assert app.turn_alts == ["The tavern is smoky tonight."]
            assert app.full_chat == app.chat
    asyncio.run(go())


def test_player_character_is_folded_into_the_system_prompt():
    app = SekkaApp(make_config(model="m", player="Seraine, frost-mage, 12 shillings"))
    text = app._system_text()
    assert "You are a helpful assistant." in text
    assert "[The player's character]\nSeraine, frost-mage, 12 shillings" in text
    assert app._used_estimate() > 0


def test_campaign_command_loads_and_persists(tmp_path):
    async def go():
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({"model": "m", "save_dir": str(tmp_path)}))
        camp = write_campaign(tmp_path / "camp", system_prompt="You are the GM.",
                              labels={"user": "Player", "assistant": "GM"},
                              greeting="You stand at the lock gate.")
        from sekka.config import load_config
        config = load_config(config_path=str(cfg_path))
        app = SekkaApp(config)
        async with app.run_test(size=(90, 30)) as pilot:
            await run_typing(pilot, f"/campaign {camp}")
            await pilot.press("enter")
            await wait_for(pilot, lambda: "GM" in history_text(app))
            assert app.config["system_prompt"] == "You are the GM."
            assert app.config["labels"]["assistant"] == "GM"
            assert "You stand at the lock gate." in history_text(app)
            saved = json.loads(cfg_path.read_text())
            assert saved["campaign"] == str(camp)          # remembered for next time
            # campaign prose is never written into the config file
            assert saved.get("system_prompt", "") != "You are the GM."
    asyncio.run(go())


def test_save_records_meta_and_resume_restores_the_campaign(tmp_path):
    async def go():
        camp = write_campaign(
            tmp_path / "camp",
            system_prompt="You are the GM of the Marches.",
            labels={"user": "Player", "assistant": "GM"},
        )
        from sekka.config import load_config
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({"model": "m", "save_dir": str(tmp_path)}))
        config = load_config(config_path=str(cfg_path), campaign_path=str(camp))
        app = SekkaApp(config)
        client.stream_chat_completion = fake_stream([("content", "A hooded figure nods.")])
        try:
            async with app.run_test(size=(90, 30)) as pilot:
                await run_typing(pilot, "I look around")
                await pilot.press("enter")
                await wait_for(pilot, lambda: not app.busy and len(app.chat) == 2)
                path = app._save_history()
        finally:
            client.stream_chat_completion = _orig_stream
        payload = json.loads(Path(path).read_text())
        assert payload["sekka_session"] == 2
        assert payload["meta"]["campaign"] == str(camp)
        assert payload["meta"]["labels"] == {"user": "Player", "assistant": "GM"}

        # resume in a fresh app with no campaign loaded: it comes back as itself
        plain = load_config(config_path=str(cfg_path))
        assert plain.campaign_path is None
        resumed = SekkaApp(plain, resume=str(path))
        async with resumed.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            assert resumed.config["labels"]["assistant"] == "GM"
            assert "GM of the Marches" in resumed._system_text()
            assert len(resumed.full_chat) == 2
            assert "GM:\nA hooded figure nods." in history_text(resumed)
    asyncio.run(go())


def test_markdown_saves_carry_no_meta_but_still_write(tmp_path):
    from sekka import storage
    path = storage.save_history(
        [{"role": "user", "content": "hi"}], directory=tmp_path, fmt="markdown"
    )
    assert "hi" in path.read_text()
    json_path = storage.save_history([{"role": "user", "content": "hi"}], directory=tmp_path)
    assert "meta" not in json.loads(json_path.read_text())   # no session context to record


def test_hostile_session_meta_is_ignored(tmp_path):
    from sekka import storage
    f = tmp_path / "s.json"
    f.write_text(json.dumps({
        "messages": [{"role": "user", "content": "hi"}],
        "meta": {"campaign": {"not": "a string"}, "labels": {"user": 17, "evil": "x"},
                 "sudo": True},
    }))
    messages, meta = storage.load_session(f)
    assert meta == {}
    assert messages == [{"role": "user", "content": "hi"}]


def test_regen_of_greeting_only_context_is_refused_not_crashed():
    """Regression: /regen on a campaign greeting used to pop from an empty list."""
    async def go():
        def boom(*a, **k):
            raise client.ClientError("no user message")
        old = client.chat_completion
        client.chat_completion = boom
        try:
            app = SekkaApp(make_config(model="m", stream=False, greeting="Hello traveller."))
            async with app.run_test(size=(90, 30)) as pilot:
                await run_typing(pilot, "/regen")
                await pilot.press("enter")
                await wait_for(pilot, lambda: "no message to reply to" in history_text(app))
                assert app.chat == [{"role": "assistant", "content": "Hello traveller."}]
                await run_typing(pilot, "/undo")
                await pilot.press("enter")
                await wait_for(pilot, lambda: not app.chat)
                # undo on an empty log must also be survivable
                await run_typing(pilot, "/undo")
                await pilot.press("enter")
                await wait_for(pilot, lambda: "Nothing to undo." in history_text(app))
        finally:
            client.chat_completion = old
    asyncio.run(go())


def test_empty_reply_is_reported_and_not_stored_as_an_empty_turn():
    async def go():
        def fake(endpoint, model, messages, **kwargs):
            yield client.StreamEvent(reasoning="thinking hard", finish_reason="")
            yield client.StreamEvent(
                reasoning="thinking hard", message={"role": "assistant", "content": ""},
                completion_tokens=120, elapsed=0.2, finish_reason="length",
            )
        old = client.stream_chat_completion
        client.stream_chat_completion = fake
        try:
            app = SekkaApp(make_config(model="m", stream=True))
            async with app.run_test(size=(90, 30)) as pilot:
                await run_typing(pilot, "go")
                await pilot.press("enter")
                await wait_for(pilot, lambda: not app.busy)
                text = history_text(app)
                assert "Empty reply" in text and "max tokens" in text
                assert [m["role"] for m in app.chat] == ["user"]   # no blank assistant turn
                assert len(app.full_chat) == 1
        finally:
            client.stream_chat_completion = _orig_stream
    asyncio.run(go())

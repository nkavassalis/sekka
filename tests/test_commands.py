from sekka.commands import parse_input, resolve_command


def test_plain_text():
    parsed = parse_input("hello there")
    assert parsed.kind == "text"
    assert parsed.text == "hello there"


def test_multiline_text_is_not_a_command():
    parsed = parse_input("line one\nline two")
    assert parsed.kind == "text"


def test_command_without_args():
    parsed = parse_input("/save")
    assert parsed.kind == "command"
    assert parsed.name == "save"
    assert parsed.arg == ""


def test_command_with_whitespace_and_case():
    parsed = parse_input("  /HELP  ")
    assert parsed.kind == "command"
    assert parsed.name == "help"


def test_command_with_arg():
    parsed = parse_input("/something extra words")
    assert parsed.name == "something"
    assert parsed.arg == "extra words"


def test_bare_slash():
    parsed = parse_input("/")
    assert parsed.kind == "command"
    assert parsed.name == ""


def test_aliases_resolve():
    assert resolve_command("q") == "exit"
    assert resolve_command("quit") == "exit"
    assert resolve_command("exit") == "exit"
    assert resolve_command("bogus") is None
    assert resolve_command("") is None

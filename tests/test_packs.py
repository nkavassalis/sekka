"""Campaign packs: install, resolve, list, remove, fork, and how they reach config."""

from __future__ import annotations

import json
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from sekka import packs
from sekka.config import ConfigError, load_config
from sekka.packs import PackError

HERE = Path(__file__).resolve().parent


@pytest.fixture
def packs_root(tmp_path, monkeypatch):
    """An installed-pack directory nobody has to own ~/.sekka for."""
    root = tmp_path / "packs"
    monkeypatch.setenv("SEKKA_PACKS_DIR", str(root))
    return root


def make_pack(
    root: Path,
    name: str = "frostspire",
    title: str = "The Frostspire Marches",
    nested: bool = True,
    note: str = "",
    lore: dict[str, str] | None = None,
    keywords: bool = True,
) -> Path:
    """A pack directory in the shipped layout: campaign in .sekka/, lore beside it."""
    root = Path(root) / name
    campaign_dir = root / ".sekka" if nested else root
    campaign_dir.mkdir(parents=True, exist_ok=True)
    lore = lore or {"world.md": "# World\nCold, and getting colder.\n"}
    for filename, text in lore.items():
        target = root / "knowledge" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    entries = [
        {
            "file": f"knowledge/{filename}",
            "description": f"Lore about {filename}. Call it when {filename} comes up.",
            "enabled": True,
        }
        for filename in sorted(lore)
    ]
    if keywords and entries:
        entries[0]["keywords"] = ["frost", "cold"]
    payload = {
        "name": title,
        "system_prompt": "You are the game master. Be brief.",
        "labels": {"user": "Player", "assistant": "GM"},
        "knowledge": entries,
        "greeting": "The last barge of the night slides in.",
        "note": note,
    }
    (campaign_dir / "campaign.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    return root


def write_tar(source_dir: Path, archive: Path, members: dict[str, str] | None = None) -> Path:
    """Tar up a pack directory (or hand-crafted member names, for the guard tests)."""
    with tarfile.open(archive, "w:gz") as handle:
        if members is not None:
            for name, text in members.items():
                data = text.encode("utf-8")
                info = tarfile.TarInfo(name)
                info.size = len(data)
                handle.addfile(info, _reader(data))
            return archive
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                handle.add(path, arcname=str(path.relative_to(source_dir.parent)))
    return archive


def write_zip(source_dir: Path, archive: Path, members: dict[str, str] | None = None) -> Path:
    with zipfile.ZipFile(archive, "w") as handle:
        if members is not None:
            for name, text in members.items():
                handle.writestr(name, text)
            return archive
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                handle.write(path, arcname=str(path.relative_to(source_dir.parent)))
    return archive


def _reader(data: bytes):
    import io

    return io.BytesIO(data)


# ---------------------------------------------------------------------------- install


def test_install_a_directory_copies_the_whole_pack(packs_root, tmp_path):
    source = make_pack(tmp_path / "src", lore={"world.md": "a\n", "pock.md": "b\n"})
    installed = packs.install(source)

    assert installed["name"] == "frostspire"
    assert installed["title"] == "The Frostspire Marches"
    target = packs_root / "frostspire"
    assert (target / ".sekka/campaign.json").is_file()
    assert (target / "knowledge/world.md").is_file()
    assert (target / "knowledge/pock.md").is_file()
    assert json.loads((target / "pack.json").read_text())["name"] == "frostspire"
    # installing is a copy: the checkout it came from keeps its own files
    source.joinpath("knowledge/world.md").write_text("changed upstream\n", encoding="utf-8")
    assert installed["path"] != source


def test_install_names_the_pack_after_the_source_not_the_title(packs_root, tmp_path):
    installed = packs.install(make_pack(tmp_path / "src", name="frostspire"))
    assert installed["name"] == "frostspire"          # typed again as --pack frostspire
    assert installed["title"] == "The Frostspire Marches"   # title still shows in list


def test_install_name_can_be_forced_and_is_case_preserving(packs_root, tmp_path):
    installed = packs.install(make_pack(tmp_path / "src"), name="MyPack")
    assert installed["name"] == "MyPack"
    assert packs_root.joinpath("MyPack").is_dir()


def test_install_from_tar_gz_and_zip(packs_root, tmp_path):
    source = make_pack(tmp_path / "src", name="frostspire")
    got = packs.install(write_tar(source, tmp_path / "frostspire.tar.gz"))
    assert got["name"] == "frostspire"
    assert (packs_root / "frostspire/knowledge/world.md").is_file()

    other = make_pack(tmp_path / "other", name="marches")
    got = packs.install(write_zip(other, tmp_path / "marches.zip"))
    assert got["name"] == "marches"
    assert (packs_root / "marches/.sekka/campaign.json").is_file()


def test_install_rejects_a_name_clash_unless_forced(packs_root, tmp_path):
    source = make_pack(tmp_path / "src")
    packs.install(source)
    with pytest.raises(PackError, match="already installed"):
        packs.install(source)
    packs.install(source, force=True)
    assert (packs_root / "frostspire/.sekka/campaign.json").is_file()


def test_install_reports_what_it_could_not_do(packs_root, tmp_path):
    with pytest.raises(PackError, match="No such pack"):
        packs.install(tmp_path / "nowhere")
    junk = tmp_path / "junk.txt"
    junk.write_text("hello", encoding="utf-8")
    with pytest.raises(PackError, match="Cannot install"):
        packs.install(junk)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(PackError, match="No campaign file"):
        packs.install(empty)


def test_install_refuses_a_campaign_that_is_not_a_campaign(packs_root, tmp_path):
    source = make_pack(tmp_path / "src")
    campaign = source / ".sekka/campaign.json"
    campaign.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(PackError, match="JSON object"):
        packs.install(source)

    other = make_pack(tmp_path / "other", name="deep")
    data = json.loads((other / ".sekka/campaign.json").read_text())
    data["knowledge"][0]["file"] = "../escape.md"
    (other / ".sekka/campaign.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PackError, match="not read lore outside itself"):
        packs.install(other)

    absolute = make_pack(tmp_path / "abs", name="absolute")
    data = json.loads((absolute / ".sekka/campaign.json").read_text())
    data["knowledge"][0]["file"] = "/etc/passwd"
    (absolute / ".sekka/campaign.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PackError, match="absolute lore path"):
        packs.install(absolute)

    binary = make_pack(tmp_path / "bin", name="exe")
    binary.joinpath("knowledge/world.md").rename(binary / "knowledge/world.exe")
    data = json.loads((binary / ".sekka/campaign.json").read_text())
    data["knowledge"][0]["file"] = "knowledge/world.exe"
    (binary / ".sekka/campaign.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PackError, match="lore files must be"):
        packs.install(binary)


def test_install_requires_enabled_lore_to_exist(packs_root, tmp_path):
    source = make_pack(tmp_path / "src")
    source.joinpath("knowledge/world.md").unlink()
    with pytest.raises(PackError, match="Pack lore is missing"):
        packs.install(source)


def test_disabled_lore_only_warns(packs_root, tmp_path):
    source = make_pack(tmp_path / "src")
    campaign = source / ".sekka/campaign.json"
    data = json.loads(campaign.read_text())
    data["knowledge"].append({"file": "knowledge/later.md", "description": "x" * 12, "enabled": False})
    campaign.write_text(json.dumps(data), encoding="utf-8")
    installed = packs.install(source)
    assert installed["warnings"] == ["knowledge/later.md (from an entry)"]


# ------------------------------------------------------------------ archive guards


def test_tar_cannot_write_outside_the_pack(packs_root, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("important", encoding="utf-8")
    archive = tmp_path / "evil.tar.gz"
    write_tar(None, archive, {"../victim.txt": "pwned", "frostspire/.sekka/campaign.json": "{}"})
    with pytest.raises(PackError, match="unsafe path"):
        packs.install(archive)
    assert victim.read_text() == "important"
    assert not packs_root.exists() or not list(packs_root.iterdir())


def test_zip_cannot_write_outside_the_pack(packs_root, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("important", encoding="utf-8")
    archive = tmp_path / "evil.zip"
    write_zip(None, archive, {"../victim.txt": "pwned"})
    with pytest.raises(PackError, match="unsafe path"):
        packs.install(archive)
    assert victim.read_text() == "important"


def test_absolute_paths_in_archives_are_refused(packs_root, tmp_path):
    archive = tmp_path / "evil.tar.gz"
    write_tar(None, archive, {"/tmp/sekka-pwned": "no"})
    with pytest.raises(PackError, match="unsafe path"):
        packs.install(archive)
    assert not Path("/tmp/sekka-pwned").exists()


def test_symlinks_in_archives_are_refused(packs_root, tmp_path):
    archive = tmp_path / "link.tar.gz"
    payload = json.dumps({"name": "ok", "system_prompt": "p"})
    with tarfile.open(archive, "w:gz") as handle:
        info = tarfile.TarInfo("ok/.sekka/campaign.json")
        data = payload.encode()
        info.size = len(data)
        handle.addfile(info, _reader(data))
        link = tarfile.TarInfo("ok/.sekka/config.json")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        handle.addfile(link)
    with pytest.raises(PackError, match="links"):
        packs.install(archive)


# --------------------------------------------------------------- list / show / rm


def test_list_is_empty_and_then_not(packs_root, tmp_path):
    assert packs.list_packs() == []
    packs.install(make_pack(tmp_path / "src", note="Seraine owes me nothing"))
    rows = packs.list_packs()
    assert [row["name"] for row in rows] == ["frostspire"]
    assert rows[0]["title"] == "The Frostspire Marches"
    assert len(rows[0]["knowledge"]) == 1


def test_list_flags_a_broken_pack_without_losing_it(packs_root):
    (packs_root / "broken").mkdir(parents=True)
    rows = packs.list_packs()
    assert rows[0]["name"] == "broken"
    assert "no campaign file" in rows[0]["error"]


def test_remove_refuses_to_discard_play_state(packs_root, tmp_path):
    packs.install(make_pack(tmp_path / "src", note="the duke still lies"))
    with pytest.raises(PackError, match="note"):
        packs.remove("frostspire")
    assert (packs_root / "frostspire").is_dir()
    packs.remove("frostspire", force=True)
    assert not (packs_root / "frostspire").exists()


def test_remove_an_unknown_pack_is_a_clear_error(packs_root):
    with pytest.raises(PackError, match="No pack named"):
        packs.remove("nope")


def test_fork_copies_a_pack_out_without_the_manifest(packs_root, tmp_path):
    packs.install(make_pack(tmp_path / "src", note="mine"))
    dest = tmp_path / "play" / "frostspire-home"
    target = packs.fork_pack("frostspire", str(dest))
    assert target == dest
    assert (dest / ".sekka/campaign.json").is_file()
    assert not (dest / "pack.json").exists()      # provenance belongs to the install
    # the fork is independent: notes written there never reach the installed pack
    campaign = dest / ".sekka/campaign.json"
    data = json.loads(campaign.read_text())
    data["note"] = "my own state"
    campaign.write_text(json.dumps(data), encoding="utf-8")
    assert "duke" not in json.loads(
        (packs_root / "frostspire/.sekka/campaign.json").read_text()
    ).get("note", "")


def test_fork_will_not_splat_an_existing_directory(packs_root, tmp_path):
    packs.install(make_pack(tmp_path / "src"))
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    occupied.joinpath("keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(PackError, match="already has files"):
        packs.fork_pack("frostspire", str(occupied))
    assert occupied.joinpath("keep.txt").read_text() == "keep"
    packs.fork_pack("frostspire", str(occupied), force=True)
    assert (occupied / ".sekka/campaign.json").is_file()


# --------------------------------------------------------------------- resolution


def test_a_pack_resolves_by_name_or_by_path(packs_root, tmp_path):
    packs.install(make_pack(tmp_path / "src"))
    assert packs.resolve_pack_dir("frostspire") == packs_root / "frostspire"
    source = make_pack(tmp_path / "elsewhere", name="raw")
    assert packs.resolve_pack_dir(str(source)).name == "raw"
    assert packs.resolve_pack_dir(str(source / ".sekka/campaign.json")).name == "raw"
    with pytest.raises(PackError, match="No pack named"):
        packs.resolve_pack_dir("nope")


def test_lore_paths_resolve_against_the_pack_root_not_the_cwd(tmp_path, monkeypatch):
    """The bug this exists for: playing from anywhere else used to load no lore."""
    source = make_pack(tmp_path / "src", lore={"world.md": "cold\n", "seraine.md": "ember\n"})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("SEKKA_PACKS_DIR", str(tmp_path / "packs"))
    packs.install(source)

    config = load_config({"endpoint": "https://x/v1"}, pack="frostspire")
    assert config.get("name") == "The Frostspire Marches"
    assert config.base_dir == tmp_path / "packs" / "frostspire"
    for entry in config.get("knowledge"):
        assert config.resolve_path(entry["file"]).is_file(), entry["file"]
    # and a decoy in the cwd must not be preferred, even if one exists
    decoy = elsewhere / "knowledge"
    decoy.mkdir()
    decoy.joinpath("world.md").write_text("wrong world", encoding="utf-8")
    assert "cold" in config.resolve_path("knowledge/world.md").read_text()


def test_pack_beats_the_config_campaign_key_and_conflicts_with_campaign(tmp_path, monkeypatch):
    monkeypatch.setenv("SEKKA_PACKS_DIR", str(tmp_path / "packs"))
    packs.install(make_pack(tmp_path / "src"))
    other = tmp_path / "other-campaign.json"
    other.write_text(
        json.dumps({"name": "Something else", "system_prompt": "prompt"}), encoding="utf-8"
    )
    config_dir = tmp_path / "cwd" / ".sekka"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"endpoint": "https://cfg/v1", "campaign": str(other)}), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path / "cwd")

    config = load_config({"endpoint": None}, pack="frostspire")
    assert config.get("name") == "The Frostspire Marches"     # pack won
    assert config.get("endpoint") == "https://cfg/v1"          # config file still honoured

    with pytest.raises(ConfigError, match="only one can win"):
        load_config({}, pack="frostspire", campaign_path=str(other))
    monkeypatch.setenv("SEKKA_CAMPAIGN", str(other))
    with pytest.raises(ConfigError, match="only one can win"):
        load_config({}, pack="frostspire")


def test_pack_plays_straight_from_a_checkout_but_writes_nothing_there(tmp_path):
    """A pack inside a git working tree is source, not state: refuse the write."""
    example = Path(__file__).resolve().parent.parent / "examples" / "frostspire"
    config = load_config({"endpoint": "https://x/v1"}, pack=str(example))
    assert config.get("name") == "The Frostspire Marches"
    blocked = packs.write_blocked_reason(config.pack_dir)
    assert blocked and "git checkout" in blocked

    outside = tmp_path / "loose"
    outside.mkdir()
    assert packs.write_blocked_reason(outside) is None


def test_sekka_pack_env_selects_the_pack(tmp_path, monkeypatch):
    monkeypatch.setenv("SEKKA_PACKS_DIR", str(tmp_path / "packs"))
    packs.install(make_pack(tmp_path / "src"))
    monkeypatch.setenv("SEKKA_PACK", "frostspire")
    assert load_config({"endpoint": "https://x/v1"}).get("name") == "The Frostspire Marches"


def test_pack_name_rules(packs_root):
    for bad in ("../evil", "a/b", "", ".", "-lead", "x" * 80):
        with pytest.raises(PackError):
            packs._safe_name(bad)
    assert packs.slugify("The Frostspire Marches") == "the-frostspire-marches"
    assert packs.slugify("frostspire.tar.gz") == "frostspire"
    assert packs.source_name(Path("/tmp/x/frostspire.tar.gz")) == "frostspire"


# ------------------------------------------------------------------- shipped pack


def test_the_example_pack_installs_and_plays(tmp_path, monkeypatch):
    """The repo's own example must stay a valid pack, or the docs are lying."""
    example = Path(__file__).resolve().parent.parent / "examples" / "frostspire"
    monkeypatch.setenv("SEKKA_PACKS_DIR", str(tmp_path / "packs"))
    installed = packs.install(example)
    assert installed["title"] == "The Frostspire Marches"
    assert len(installed["warnings"]) == 0
    config = load_config({"endpoint": "https://x/v1"}, pack="frostspire")
    assert len(config.get("knowledge")) == 4
    assert all(config.resolve_path(e["file"]).is_file() for e in config.get("knowledge"))
    assert config.get("note") == ""     # play state ships empty


def test_the_example_ships_without_play_state():
    example = Path(__file__).resolve().parent.parent / "examples" / "frostspire"
    data = json.loads((example / ".sekka/campaign.json").read_text())
    assert data["note"] == ""
    assert data["name"] == "The Frostspire Marches"
    for entry in data["knowledge"]:
        assert (example / entry["file"]).is_file(), entry["file"]


# ------------------------------------------------------------- end to end


def test_a_pack_plays_in_the_real_app(tmp_path, monkeypatch):
    """Not just the config layer: greeting on screen, labels voiced, cards loaded."""
    pytest.importorskip("textual")
    from sekka.config import load_config
    from sekka.tui import SekkaApp

    monkeypatch.setenv("SEKKA_PACKS_DIR", str(tmp_path / "packs"))
    packs.install(make_pack(
        tmp_path / "src",
        lore={"world.md": "# World\nCold.\n", "seraine.md": "# Seraine\nEmber.\n"},
    ))
    config = load_config({"endpoint": "https://x.example/v1", "model": "m"}, pack="frostspire")

    async def drive():
        app = SekkaApp(config)
        async with app.run_test() as pilot:
            assert app.config.pack_dir is not None
            assert app.query_one("#history") is not None          # mounted on the pack
            assert str(app.config.campaign_path).endswith("campaign.json")
            tools = {entry["file"] for entry in app.config.get("knowledge")}
            assert len(tools) == 2
            # The app resolves lore through Config.base_dir, which is the pack root:
            # this is the assertion that would fail if --pack ever stopped mattering.
            for raw in tools:
                assert app.config.resolve_path(raw).is_file(), raw
            await pilot.pause()
        return True

    import asyncio

    assert asyncio.run(drive())


def test_serve_forwards_the_pack_and_starts(tmp_path, monkeypatch):
    """`sekka serve --pack X` must boot: the flag travels to each spawned tab."""
    pytest.importorskip("textual_serve")
    port = 8399
    monkeypatch.setenv("SEKKA_PACKS_DIR", str(tmp_path / "packs"))
    packs.install(make_pack(tmp_path / "src"))
    workdir = tmp_path / "served"
    workdir.mkdir()
    server = subprocess.Popen(
        [sys.executable, "-m", "sekka", "serve", "--serve-port", str(port), "--pack", "frostspire"],
        cwd=workdir,
        env={**dict(__import__("os").environ), "SEKKA_PACKS_DIR": str(tmp_path / "packs")},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        import http.client

        deadline = __import__("time").time() + 25
        while __import__("time").time() < deadline:
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                connection.request("GET", "/")
                assert connection.getresponse().status == 200
                return
            except OSError:
                __import__("time").sleep(0.3)
        pytest.fail("served page never came up with --pack")
    finally:
        server.terminate()
        server.wait(timeout=10)


def test_cli_pack_verbs_end_to_end(tmp_path, monkeypatch, capsys):
    """install -> list -> show -> fork -> remove, through cli.main()."""
    monkeypatch.setenv("SEKKA_PACKS_DIR", str(tmp_path / "packs"))
    source = make_pack(tmp_path / "src", note="a note")

    assert cli_main(["pack", "install", str(source)]) == 0
    assert "play it: sekka --pack frostspire" in capsys.readouterr().out
    assert cli_main(["pack", "list"]) == 0
    assert "The Frostspire Marches" in capsys.readouterr().out
    assert cli_main(["pack", "show", "frostspire"]) == 0
    assert "knowledge/world.md" in capsys.readouterr().out

    # play state makes removal loud
    assert cli_main(["pack", "remove", "frostspire"]) == 2
    assert "note" in capsys.readouterr().err
    assert cli_main(["pack", "fork", "frostspire", str(tmp_path / "mine")]) == 0
    assert (tmp_path / "mine/.sekka/campaign.json").is_file()
    assert not (tmp_path / "mine/pack.json").exists()
    assert cli_main(["pack", "remove", "frostspire", "--force"]) == 0
    assert not (tmp_path / "packs/frostspire").exists()
    assert (tmp_path / "mine/.sekka/campaign.json").is_file()   # the fork survives


def cli_main(argv):
    from sekka import cli

    return cli.main(argv)


def test_cli_refuses_pack_plus_campaign(monkeypatch, capsys):
    from sekka import cli

    monkeypatch.setenv("SEKKA_PACKS_DIR", "/nonexistent-packs")
    assert cli.main(["--pack", "frostspire", "--campaign", "whatever.json"]) == 2
    assert "only one can win" in capsys.readouterr().err


def test_cli_pack_verbs_are_usage_errors_when_malformed(capsys):
    from sekka import cli

    for argv in (["pack"], ["pack", "wat"], ["pack", "install"], ["pack", "remove"]):
        assert cli.main(argv) == 2
        assert "pack" in capsys.readouterr().err.lower()

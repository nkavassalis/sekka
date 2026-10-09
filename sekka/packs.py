"""Campaign packs: folders that bundle a campaign with its lore files.

A pack is a directory holding a campaign file and, beside it, whatever the campaign's
``knowledge`` entries point at::

    frostspire/
      .sekka/campaign.json     <- or campaign.json at the pack root
      .sekka/config.json       <- optional, and ignored on install (machine-local)
      knowledge/world.md
      ...

The problem this solves is *where relative lore paths are read from*. Lore paths are
relative to the campaign file's directory, so a pack that keeps ``knowledge/`` beside
``.sekka/`` only resolves when the current working directory is the pack root - which
is why playing an example meant ``cd``-ing into it, and why running it from anywhere
else silently loaded no lore (the greeting and system prompt arrive, the character
cards quietly do not). Installing a pack gives sekka a directory to resolve against,
so ``--pack frostspire`` works from any cwd and the trap is gone.

Installed at ``~/.sekka/packs/<name>/``, overridable with ``SEKKA_PACKS_DIR`` (the
tests live in it; so can anyone who keeps their packs elsewhere).

Installing copies rather than symlinks: ``/note`` writes play state into the pack, and
a pack whose notes are shared between two players is a bug, not a feature. It also
keeps an installed pack from being written back into the checkout it came from.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sekka.config import CAMPAIGN_KEYS, ConfigError, validate_campaign

PACK_SUBDIR = "packs"
PACKS_ENV = "SEKKA_PACKS_DIR"
PACK_CANDIDATES = ("campaign.json", ".sekka/campaign.json")
MANIFEST_NAME = "pack.json"
LORE_SUFFIXES = (".md", ".txt")
MAX_PACK_FILES = 2000
MAX_PACK_BYTES = 64 * 1024 * 1024
ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar", ".zip")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MISSING = object()


class PackError(ConfigError):
    """A pack is missing, malformed, unsafe, or already installed."""


# -------------------------------------------------------------- where packs live


def packs_dir(override: Optional[str] = None) -> Path:
    """Where installed packs live. Created lazily, never on import."""
    raw = override or os.environ.get(PACKS_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".sekka" / PACK_SUBDIR


def pack_path(name: str, root: Optional[Path] = None) -> Path:
    return packs_dir(root and str(root)) / name


# ------------------------------------------------------------------- name / slug


def slugify(text: str) -> str:
    """A filesystem-safe pack name from a title or a source path.

    Lower-cased: pack names are typed on a command line more often than they are read,
    and `--pack frostspire` should not depend on how the title happened to be capitalised.
    An explicit --name keeps whatever case the installer chose.
    """
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text or "")).strip("-._ ").lower()
    slug = re.sub(r"-{2,}", "-", slug)
    for suffix in ARCHIVE_SUFFIXES:
        if slug.lower().endswith(suffix):
            slug = slug[: -len(suffix)]
    return slug.strip("-._ ")[:64]


def _safe_name(name: str) -> str:
    slug = str(name).strip()
    if not NAME_RE.match(slug) or ".." in slug:
        raise PackError(
            f"Invalid pack name {name!r}: use letters, digits, dot, dash, underscore "
            "(starting with a letter or digit, at most 64 characters)."
        )
    return slug


# ----------------------------------------------------------- locating a pack dir


def _campaign_candidate(root: Path) -> Optional[Path]:
    """The campaign file directly inside ``root``, or None."""
    for rel in PACK_CANDIDATES:
        candidate = root / rel
        if candidate.is_file():
            return candidate
    return None


def find_campaign_file(root: Path) -> Path:
    """Locate a pack's campaign file, tolerating an archive's single wrapper folder."""
    root = Path(root)
    if not root.is_dir():
        raise PackError(f"Not a directory: {root}")
    found = _campaign_candidate(root)
    if found is not None:
        return found
    children = [child for child in root.iterdir() if child.is_dir()]
    if len(children) == 1:
        found = _campaign_candidate(children[0])
        if found is not None:
            return found
    raise PackError(
        f"No campaign file in {root}. A pack needs campaign.json (or .sekka/campaign.json) "
        "at its top level."
    )


def read_manifest(campaign_file: Path) -> dict[str, Any]:
    try:
        data = json.loads(campaign_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PackError(f"{campaign_file} is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise PackError(f"Could not read {campaign_file}: {exc}") from exc
    if not isinstance(data, dict):
        raise PackError(f"{campaign_file} must contain a JSON object.")
    return data


def source_name(source: Path) -> str:
    """The folder or archive name a pack is being installed from, without its suffix."""
    text = Path(source).name
    for suffix in ARCHIVE_SUFFIXES:
        if text.lower().endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text


def pack_name(campaign_file: Path, data: dict[str, Any], source: Any = None) -> str:
    """What a pack installs under: the source's own name, else the campaign title.

    Source first because that is what the installer typed and what they will type again:
    `pack install examples/frostspire` should answer with `--pack frostspire`, not with a
    re-spelling of the campaign's display title. The title still shows in the header and
    in `pack list`; it only names the directory when the source tells us nothing.
    """
    for candidate in (source_name(source) if source is not None else "", data.get("name") or ""):
        slug = slugify(candidate)
        if slug:
            return _safe_name(slug)
    return _safe_name(campaign_file.parent.parent.name if campaign_file.parent.name == ".sekka" else campaign_file.parent.name)


def resolve_pack_dir(spec: str, root: Optional[Path] = None) -> Path:
    """Turn ``--pack NAME|path`` into a directory.

    A path (existing directory, or a campaign file inside one) is taken literally, so
    a pack can be played straight from a checkout without installing it. Anything else
    must name an installed pack.
    """
    text = str(spec or "").strip()
    if not text:
        raise PackError("Empty pack name.")
    raw = Path(text).expanduser()
    if raw.exists():
        base = raw
        if raw.is_file():
            base = raw.parent
        if base.name == ".sekka":
            base = base.parent
        campaign = _campaign_candidate(Path(base))
        if campaign is None:
            raise PackError(
                f"No campaign file in {base}. A pack needs campaign.json (or "
                ".sekka/campaign.json) at its top level."
            )
        return Path(base).resolve()
    slug = _safe_name(text)
    installed = pack_path(slug, root)
    if installed.is_dir() and _campaign_candidate(installed) is not None:
        return installed
    have = sorted(item.name for item in packs_dir(root and str(root)).glob("*/") if item.is_dir())
    raise PackError(
        f"No pack named {text!r} installed."
        + (f" Installed: {', '.join(have)}." if have else " Nothing is installed yet: "
                                               "`sekka pack install <dir|archive>`.")
    )


def pack_base_dir(pack_dir: Path) -> Path:
    """What relative lore paths in this pack resolve against: the pack root."""
    pack_dir = Path(pack_dir)
    return pack_dir.parent if pack_dir.name == ".sekka" else pack_dir


# ------------------------------------------------------------------- validation


def _entry_path(entry: Any) -> str:
    return str(entry.get("file", "")).strip() if isinstance(entry, dict) else ""


def check_pack(campaign_file: Path, base: Optional[Path] = None) -> dict[str, Any]:
    """Validate a pack's campaign and make sure its lore can actually be read.

    Missing lore is fatal for an enabled entry: a campaign that loads half its own
    world is worse than one that refuses to install, because the model will improvise
    the missing half confidently. Disabled entries only warn, since the player may
    plan to add that file later.
    """
    data = read_manifest(campaign_file)
    validate_campaign(data)
    base = Path(base) if base is not None else pack_base_dir(campaign_file.parent)
    problems: list[str] = []
    warnings: list[str] = []
    for entry in data.get("knowledge", []) or []:
        raw = _entry_path(entry)
        if not raw:
            continue
        resolved = Path(raw).expanduser()
        if resolved.is_absolute():
            raise PackError(
                f"A pack may not name an absolute lore path ({raw}). Lore files are part of "
                "the pack: put them under its root and point at them relatively."
            )
        resolved = base / raw
        try:
            inside = resolved.resolve().is_relative_to(base.resolve())
        except OSError:
            inside = False
        if not inside:
            raise PackError(
                f"A pack may not read lore outside itself ({raw}). Lore paths must stay "
                "inside the pack directory."
            )
        if resolved.suffix.lower() not in LORE_SUFFIXES:
            raise PackError(
                f"A pack's lore files must be {' or '.join(LORE_SUFFIXES)} (got {raw})."
            )
        if not resolved.is_file():
            message = f"{raw} (from {entry.get('tool') or 'an entry'})"
            (problems if entry.get("enabled", True) else warnings).append(message)
    if problems:
        raise PackError(
            "Pack lore is missing: " + ", ".join(problems)
            + ". Lore paths are relative to the pack root; ship those files or disable "
              "the entries."
        )
    data["_pack_warnings"] = warnings
    data["_pack_base"] = str(base)
    data["_pack_name"] = pack_name(campaign_file, data)
    return data


# ---------------------------------------------------------------------- extraction


def _safe_member(name: str) -> str:
    text = str(name).replace("\\", "/")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise PackError(f"Refusing unsafe path in archive: {name}")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise PackError(f"Refusing unsafe path in archive: {name}")
    return "/".join(parts)


def _materialize(source: Path, dest: Path) -> None:
    """Copy or extract ``source`` into ``dest``, refusing anything sneaky.

    Archives are the interesting case: a tarball can carry absolute paths, ``..``
    segments, symlinks and device nodes, and extracting one of those blind is how a
    "pack install" becomes an arbitrary file write. Every member is checked, the entry
    count and total size are capped, and only regular files are written.
    """
    source = Path(source).expanduser().resolve()
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, dest, dirs_exist_ok=True, symlinks=False)
        return
    if not source.is_file():
        raise PackError(f"No such file or directory: {source}")

    lowered = source.name.lower()
    count = 0
    written = 0
    if lowered.endswith((".tar.gz", ".tgz", ".tar")):
        try:
            archive = tarfile.open(source, "r:*")
        except tarfile.TarError as exc:
            raise PackError(f"Not a readable tar archive: {source} ({exc})") from exc
        with archive:
            for member in archive.getmembers():
                name = _safe_member(member.name)
                if not name:
                    continue
                if member.issym() or member.islnk() or not member.isfile():
                    raise PackError(
                        f"Refusing links, devices and directories-as-entries in an archive: "
                        f"{member.name}"
                    )
                count += 1
                written += member.size
                if count > MAX_PACK_FILES or written > MAX_PACK_BYTES:
                    raise PackError("Archive is too large to install as a pack.")
                target = dest / name
                target.parent.mkdir(parents=True, exist_ok=True)
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                with handle, open(target, "wb") as out:
                    shutil.copyfileobj(handle, out)
        return
    if lowered.endswith(".zip"):
        try:
            archive = zipfile.ZipFile(source)
        except zipfile.BadZipFile as exc:
            raise PackError(f"Not a readable zip archive: {source} ({exc})") from exc
        with archive:
            for info in archive.infolist():
                name = _safe_member(info.filename)
                if not name:
                    continue
                if info.is_dir() or name.endswith("/"):
                    continue
                count += 1
                written += info.file_size
                if count > MAX_PACK_FILES or written > MAX_PACK_BYTES:
                    raise PackError("Archive is too large to install as a pack.")
                target = dest / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as handle, open(target, "wb") as out:
                    shutil.copyfileobj(handle, out)
        return
    raise PackError(
        f"Cannot install {source.name}: a pack is a directory, or an archive named "
        + " ".join(ARCHIVE_SUFFIXES)
    )


# -------------------------------------------------------------- install / remove


def install(
    source: str,
    name: Optional[str] = None,
    force: bool = False,
    root: Optional[Path] = None,
) -> dict[str, Any]:
    """Install a pack directory or archive. Returns a summary dict.

    ``source`` may be a directory or a ``.tar.gz``/``.tgz``/``.tar``/``.zip``. Nothing
    is fetched: installing from a URL would turn this into a supply chain, and a pack
    is five text files - ``scp`` it.
    """
    src = Path(str(source)).expanduser()
    if not src.exists():
        raise PackError(
            f"No such pack: {source}. `sekka pack install` takes a directory or a "
            + ", ".join(ARCHIVE_SUFFIXES)
            + " archive, and does not fetch URLs."
        )
    target = packs_dir(root and str(root))
    staging = Path(tempfile.mkdtemp(prefix="sekka-pack-"))
    try:
        _materialize(src, staging)
        campaign_file = find_campaign_file(staging)
        data = check_pack(campaign_file)
        slug = _safe_name(name) if name else pack_name(campaign_file, data, source=src)
        if name:
            # A --name rename changes the install dir only; the campaign's own title
            # stays what it is, because that is what shows in the header.
            data["_pack_name"] = slug
        pack_root = campaign_file.parent.parent if campaign_file.parent.name == ".sekka" else campaign_file.parent
        dest = target / slug
        if dest.exists() and not force:
            raise PackError(
                f"A pack named {slug!r} is already installed in {target}. Use --force to "
                "replace it."
            )
        if force and dest.is_dir():
            shutil.rmtree(dest)
        target.mkdir(parents=True, exist_ok=True)
        # Move the pack root itself: an archive usually wraps its contents in one
        # folder, and copying that wrapper in would bury the pack a level too deep.
        if pack_root == staging:
            shutil.move(str(staging), str(dest))
        else:
            shutil.move(str(pack_root), str(dest))
        installed_campaign = find_campaign_file(dest)
        written = read_manifest(installed_campaign)
        written["_installed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        written["_installed_from"] = str(src)
        (dest / MANIFEST_NAME).write_text(
            json.dumps(
                {
                    "name": slug,
                    "title": written.get("name") or slug,
                    "campaign": str(installed_campaign.relative_to(dest)),
                    "installed_from": str(src),
                    "installed_at": written["_installed_at"],
                    "knowledge": [
                        {"file": _entry_path(entry), "enabled": bool(entry.get("enabled", True))}
                        for entry in written.get("knowledge", []) or []
                        if _entry_path(entry)
                    ],
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return {
            "name": slug,
            "title": written.get("name") or slug,
            "path": dest,
            "campaign": installed_campaign,
            "base_dir": pack_base_dir(dest),
            "warnings": data.get("_pack_warnings", []),
        }
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def remove(name: str, root: Optional[Path] = None, force: bool = False) -> Path:
    """Delete an installed pack. Refuses to discard unsaved play state without force."""
    slug = _safe_name(name)
    target = packs_dir(root and str(root)) / slug
    if not target.is_dir():
        raise PackError(f"No pack named {slug!r} installed.")
    if not force:
        try:
            campaign = find_campaign_file(target)
        except PackError:
            campaign = None
        if campaign is not None:
            data = read_manifest(campaign)
            note = str(data.get("note") or "").strip()
            if note:
                raise PackError(
                    f"{slug} has a note in it (play state): {note[:60]}"
                    f"{'...' if len(note) > 60 else ''}. `/{'note'}` is stored inside the pack, "
                    "so removing it throws that away. Use --force to remove anyway."
                )
    shutil.rmtree(target)
    return target


def list_packs(root: Optional[Path] = None) -> list[dict[str, Any]]:
    """Installed packs, with whatever the manifest claims about them."""
    base = packs_dir(root and str(root))
    if not base.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for entry in sorted(base.iterdir(), key=lambda item: item.name.lower()):
        if not entry.is_dir():
            continue
        manifest = entry / MANIFEST_NAME
        info: dict[str, Any] = {}
        if manifest.is_file():
            try:
                info = read_manifest(manifest)
            except PackError:
                info = {}
        row = {
            "name": entry.name,
            "path": entry,
            "title": info.get("title") or info.get("name") or entry.name,
            "installed_from": info.get("installed_from", ""),
            "installed_at": info.get("installed_at", ""),
            "error": "",
            "knowledge": info.get("knowledge") or [],
        }
        if not _campaign_candidate(entry):
            row["error"] = "no campaign file"
        rows.append(row)
    return rows


def campaign_file_for(pack_dir: Path) -> Path:
    """The campaign file inside an installed/known pack directory."""
    return find_campaign_file(Path(pack_dir))


# --------------------------------------------------------------- work file helper


def write_blocked_reason(pack_dir: Any) -> Optional[str]:
    """Why writing play state into this pack directory would be rude, or None.

    An installed pack is a machine-local copy, so notes belong in it. A pack that is
    still sitting in a git working tree is somebody's source: /note quietly dirtying the
    checkout (and then `git status` blaming the player) is the wrong side effect, so the
    write is refused and the message says how to get a copy that is yours.
    """
    if pack_dir is None:
        return None
    path = Path(str(pack_dir)).resolve()
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return (
                f"{path} is inside a git checkout, so sekka will not write play state into it. "
                "Install it (`sekka pack install .`) or copy it out (`sekka pack fork NAME`) "
                "and play that instead."
            )
    return None


def fork_pack(name: str, dest_dir: str, root: Optional[Path] = None, force: bool = False) -> Path:
    """Copy an installed pack out to ``dest_dir`` so a player can edit it freely."""
    slug = _safe_name(name)
    source = packs_dir(root and str(root)) / slug
    if not source.is_dir():
        raise PackError(f"No pack named {slug!r} installed.")
    dest = Path(str(dest_dir)).expanduser()
    if dest.exists() and dest.is_file():
        raise PackError(f"{dest} is a file, not a directory.")
    if dest.is_dir() and any(dest.iterdir()) and not force:
        raise PackError(
            f"{dest} already has files in it. Use --force to overwrite it with the pack."
        )
    if dest.is_dir():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # The provenance manifest describes an installation, and a fork is not one.
    shutil.copytree(source, dest, symlinks=False, ignore=shutil.ignore_patterns(MANIFEST_NAME))
    return dest

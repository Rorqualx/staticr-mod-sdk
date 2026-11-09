"""The SDK's reader of staticR-native mod manifests and ``.staticrmod`` packages.

Written from staticR's own formats only: the manifest checks of the game's mod hosts
(``host/staticr_mod_tier0.cpp`` ``Engine::add_mod``, ``host/staticr_mod_tier1_common.cpp``
``parse_manifest``/``valid_id``/``plain_file_name``) and the ``.staticrmod`` package layout
(``docs/design/mod-sdk.md`` M.2e E3). Standard library only, so the standalone SDK ships it.

A manifest ``<id>.mod.toml`` is ``format = "staticr.mod/1"`` with ``id``, ``game`` and optionally
``name``, ``description``, ``author``, ``version``, plus either ``wasm = "<file>.wasm"`` (Tier 1)
or ``[[rule]]`` tables (Tier 0, whose contents :func:`staticr_mods.sdk.check_rules` checks). A
package is one zip holding, at its root and nowhere else, exactly that manifest, the module a
Tier 1 manifest names (at most 4 MiB), and optionally ``<id>.json`` (default settings, at most
64 KiB, JSON) and ``thumb.png`` (a PNG); nothing else.

``tests/test_sdk.py`` pins this module's verdicts to the launcher's own readers on a corpus of good
and bad manifests and packages, so the two cannot drift apart silently.
"""

from __future__ import annotations

import io
import json
import tomllib
import zipfile
from dataclasses import dataclass
from typing import Any

FORMAT = "staticr.mod/1"
SUFFIX = ".mod.toml"
PACKAGE_SUFFIX = ".staticrmod"
THUMBNAIL = "thumb.png"
WASM_MAX_BYTES = 4 * 1024 * 1024
SETTINGS_MAX_BYTES = 64 * 1024
COMMON_KEYS = frozenset({"format", "id", "name", "description", "author", "version", "game"})
_PNG = b"\x89PNG\r\n\x1a\n"
_WASM = b"\0asm"


class ManifestError(ValueError):
    """A manifest or package the hosts or the launcher would refuse, with the reason."""


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    game: str
    wasm: str | None  # Tier 1: the module's file name; Tier 0: None
    table: dict[str, Any]


def valid_id(mod_id: str) -> bool:
    """1-64 of A-Z a-z 0-9 _ . -, not starting with '.' (the hosts' ``valid_id``)."""
    return (
        0 < len(mod_id) <= 64
        and not mod_id.startswith(".")
        and all((c.isascii() and c.isalnum()) or c in "_.-" for c in mod_id)
    )


def plain_file_name(name: str) -> bool:
    """A file name with no directory part (the hosts' ``plain_file_name``)."""
    return (
        0 < len(name) <= 128
        and name not in (".", "..")
        and not any(c in "/\\:" or ord(c) < 0x20 for c in name)
    )


def _check_keys(table: dict[str, Any]) -> bool:
    """The top-level keys; True for a Tier 1 manifest (it has ``wasm``)."""
    tier1 = "wasm" in table
    allowed = COMMON_KEYS | ({"wasm"} if tier1 else {"rule"})
    for key in table:
        if key not in allowed:
            raise ManifestError(
                f"unknown key {key!r}" + (" (a WASM mod has no rules)" if tier1 else "")
            )
    if table.get("format") != FORMAT:
        raise ManifestError(f'format must be "{FORMAT}"')
    for key in ("name", "description", "author", "version", "game"):
        if key in table and not isinstance(table[key], str):
            raise ManifestError(f"{key} must be a string")
    return tier1


def parse_manifest(text: str) -> Manifest:
    """A manifest's text, checked as the host that would load it checks it. A Tier 0 manifest's
    ``[[rule]]`` tables are checked by :func:`staticr_mods.sdk.check_rules` (they need the game's
    field table); a package needs at least one (:func:`check_package`)."""
    try:
        table = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"TOML: {exc}") from exc
    tier1 = _check_keys(table)
    mod_id = table.get("id")
    if not isinstance(mod_id, str) or not valid_id(mod_id):
        raise ManifestError("id must be 1-64 characters of [A-Za-z0-9_.-], not starting with '.'")
    game = table.get("game", "")
    if not game:
        raise ManifestError('has no game = "<game>"')
    wasm = table.get("wasm") if tier1 else None
    if tier1 and (
        not isinstance(wasm, str) or not plain_file_name(wasm) or not wasm.endswith(".wasm")
    ):
        raise ManifestError(f"wasm = {wasm!r} must be a plain *.wasm file name")
    return Manifest(mod_id, table.get("name") or mod_id, game, wasm, table)


def _open_zip(data: bytes) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = z.infolist()
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise ManifestError(f"not a zip: {exc}") from exc
    names = [i.filename for i in infos]
    if len(set(names)) != len(names):
        raise ManifestError("a member name appears twice")
    for i in infos:
        if i.is_dir() or not plain_file_name(i.filename):
            raise ManifestError(f"{i.filename!r}: a package holds files at its root only")
    return z


def _package_manifest(z: zipfile.ZipFile) -> tuple[str, Manifest]:
    manifests = [n for n in z.namelist() if n.endswith(SUFFIX)]
    if len(manifests) != 1:
        raise ManifestError(f"a package holds exactly one *{SUFFIX}, this one {len(manifests)}")
    try:
        m = parse_manifest(z.read(manifests[0]).decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ManifestError(f"{manifests[0]} is not UTF-8") from exc
    rules = m.table.get("rule")
    if m.wasm is None and (not isinstance(rules, list) or not rules):
        raise ManifestError('no [[rule]] tables and no wasm = "<file>.wasm"')
    return manifests[0], m


def _check_module(z: zipfile.ZipFile, wasm: str, sizes: dict[str, int]) -> None:
    if wasm not in sizes:
        raise ManifestError(f"no {wasm}, the module the manifest names")
    if sizes[wasm] > WASM_MAX_BYTES:
        raise ManifestError(f"{wasm} is over {WASM_MAX_BYTES} bytes")
    if not z.read(wasm).startswith(_WASM):
        raise ManifestError(f"{wasm} is not a WebAssembly module")


def _check_settings(z: zipfile.ZipFile, settings: str, size: int) -> None:
    if size > SETTINGS_MAX_BYTES:
        raise ManifestError(f"{settings} is over {SETTINGS_MAX_BYTES} bytes")
    try:
        json.loads(z.read(settings).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ManifestError(f"{settings} is not JSON: {exc}") from exc


def _check_members(z: zipfile.ZipFile, manifest_name: str, m: Manifest) -> None:
    settings = f"{m.id}.json"
    expected = {manifest_name, settings, THUMBNAIL} | ({m.wasm} if m.wasm else set())
    sizes = {i.filename: i.file_size for i in z.infolist()}
    for n in sizes:
        if n not in expected:
            raise ManifestError(f"{n!r} does not belong in a package")
    if m.wasm is not None:
        _check_module(z, m.wasm, sizes)
    if settings in sizes:
        _check_settings(z, settings, sizes[settings])
    if THUMBNAIL in sizes and not z.read(THUMBNAIL).startswith(_PNG):
        raise ManifestError(f"{THUMBNAIL} is not a PNG")


def check_package(data: bytes, game: str | None = None) -> Manifest:
    """A ``.staticrmod``'s bytes, checked as the launcher checks one before installing it; the
    manifest it holds. ``game``: the game it must be for, when known."""
    z = _open_zip(data)
    name, m = _package_manifest(z)
    if game is not None and m.game != game:
        raise ManifestError(f"the mod is for {m.game!r}, not {game!r}")
    _check_members(z, name, m)
    return m


__all__ = [
    "FORMAT",
    "PACKAGE_SUFFIX",
    "SUFFIX",
    "THUMBNAIL",
    "WASM_MAX_BYTES",
    "Manifest",
    "ManifestError",
    "check_package",
    "parse_manifest",
    "plain_file_name",
    "valid_id",
]

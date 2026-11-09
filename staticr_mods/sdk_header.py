"""The mod SDK's tables and generated game header (``docs/design/mod-sdk.md`` M.2f).

Standard library only (no ``tomllib``), so ``bin/mod_tier1_check.py`` can generate the header under
any Python 3. :mod:`staticr_mods.sdk` builds on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

EVENTS_TSV = Path("configs/mods/events.tsv")
FIELDS_DIR = Path("configs/mods/fields")
ABI_HEADER = Path("host/staticr_mod_wasm.h")
HOOKS_DIR = Path("configs/hooks")
# The games list of a standalone SDK, which has no hook configs (one short name per line).
GAMES_TXT = Path("configs/mods/games.txt")
# A standalone SDK install (the staticr-mod-sdk repo, written by bin/mod_sdk_release.py) carries
# the tables above under this directory, laid out as in the staticR checkout, with this marker file.
BUNDLED = Path(__file__).resolve().parent / "sdk_data"
BUNDLED_MARKER = "STATICR-MOD-SDK"
CHECKOUT = Path(__file__).resolve().parents[2]


def data_root() -> Path:
    """Where the SDK reads its tables: the staticR checkout this package sits in, when it is one
    (never the bundled copy there); else the bundled copy of a standalone SDK install, which the
    release marks; else the checkout path, so a missing table names where it was looked for."""
    if (CHECKOUT / EVENTS_TSV).is_file() and (CHECKOUT / ABI_HEADER).is_file():
        return CHECKOUT
    if (BUNDLED / BUNDLED_MARKER).is_file():
        return BUNDLED
    return CHECKOUT


REPO = data_root()


class SdkError(ValueError):
    """A request the SDK refuses, with the reason in words."""


# --- the tables -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    id: int
    name: str
    a0: str  # "play", "actor" or "-"
    a1: str
    fires: str


@dataclass(frozen=True)
class Field:
    name: str
    base: str  # "play", "actor", "abs" or an earlier ptr field's name
    offset: int
    type: str  # u8 s8 u16 s16 u32 s32 f32 ptr


_TYPES = ("u8", "s8", "u16", "s16", "u32", "s32", "f32", "ptr")


def _rows(path: Path) -> list[list[str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SdkError(f"cannot read {path}: {exc}") from exc
    return [
        line.split("\t") for line in text.splitlines() if line.strip() and not line.startswith("#")
    ]


def load_events(repo: Path = REPO) -> list[Event]:
    """The event catalogue, in id order."""
    out: list[Event] = []
    for row in _rows(repo / EVENTS_TSV):
        if len(row) != 6 or not row[0].isdigit():
            raise SdkError(f"{EVENTS_TSV}: bad row {row!r}")
        out.append(Event(int(row[0]), row[1], row[2], row[3], row[5]))
    if [e.id for e in out] != list(range(len(out))):
        raise SdkError(f"{EVENTS_TSV}: ids are not 0..{len(out) - 1} in order")
    return out


def fields_path(game: str, repo: Path = REPO) -> Path:
    return repo / FIELDS_DIR / f"{game}.fields.tsv"


def has_fields(game: str, repo: Path = REPO) -> bool:
    return fields_path(game, repo).is_file()


_SIZE = {"u8": 1, "s8": 1, "u16": 2, "s16": 2, "u32": 4, "s32": 4, "f32": 4, "ptr": 4}
FIELDS_FORMAT = "# format: staticr.mod-fields/1"


_C_INT = re.compile(r"\s*(?:0[xX]([0-9a-fA-F]+)|(0[0-7]*)|([1-9][0-9]*))")


def _c_offset(text: str) -> int:
    """An offset as the engine reads it: ``strtoull(text, &end, 0)`` over the whole text (leading
    space allowed, 0x hex, 0 octal, no sign), or -1."""
    m = _C_INT.fullmatch(text)
    if m is None:
        return -1
    hexa, octal, dec = m.groups()
    return int(hexa, 16) if hexa else int(octal, 8) if octal else int(dec, 10)


def _field_row(game: str, row: list[str], earlier: list[Field], ptrs: set[str]) -> Field:
    """One row of a field table, checked against the rows before it."""
    if len(row) != 5:
        raise SdkError(f"{game}.fields.tsv: bad row {row!r}")
    name, base, off, typ, _source = row
    if any(f.name == name for f in earlier):
        raise SdkError(f"{game}.fields.tsv: duplicate name {name}")
    if typ not in _TYPES:
        raise SdkError(f"{game}.fields.tsv: {name}: unknown type {typ!r}")
    if base not in ("play", "actor", "abs") and base not in ptrs:
        raise SdkError(f"{game}.fields.tsv: {name}: base {base!r} is not an earlier ptr field")
    offset = _c_offset(off)
    if not 0 <= offset <= 0xFFFFFFFF:
        raise SdkError(f"{game}.fields.tsv: {name}: bad offset {off!r}")
    if offset % _SIZE[typ]:
        raise SdkError(f"{game}.fields.tsv: {name} is misaligned")
    return Field(name, base, offset, typ)


def load_fields(game: str, repo: Path = REPO) -> list[Field]:
    """A game's field table, with the Tier 0 engine's checks (``staticr_mod_tier0.cpp``): the
    format line, 5 columns, unique names, a known type, a non-negative offset of at most 32 bits
    aligned to its type, and a base that is play, actor, abs or an EARLIER ptr row."""
    path = fields_path(game, repo)
    try:
        first = path.read_text(encoding="utf-8").splitlines()[:1]
    except OSError as exc:
        raise SdkError(f"cannot read {path}: {exc}") from exc
    if first != [FIELDS_FORMAT]:
        raise SdkError(f"{game}.fields.tsv: line 1 is not {FIELDS_FORMAT!r}")
    out: list[Field] = []
    ptrs: set[str] = set()
    for row in _rows(path):
        f = _field_row(game, row, out, ptrs)
        out.append(f)
        if f.type == "ptr":
            ptrs.add(f.name)
    return out


def known_games(repo: Path = REPO) -> list[str]:
    """The games a mod can be made for: those whose hosts fire events (a mod-events hook file), or,
    in a standalone SDK, which has no hook configs, the games its release listed."""
    if (repo / HOOKS_DIR).is_dir():
        return sorted(
            p.name.removesuffix(".mod-events.hooks.tsv")
            for p in (repo / HOOKS_DIR).glob("*.mod-events.hooks.tsv")
        )
    if (repo / GAMES_TXT).is_file():
        return sorted(r[0] for r in _rows(repo / GAMES_TXT))
    return []


# --- the generated game header ----------------------------------------------------------------

_C_TYPE = {
    "u8": "uint8_t",
    "s8": "int8_t",
    "u16": "uint16_t",
    "s16": "int16_t",
    "u32": "uint32_t",
    "s32": "int32_t",
    "f32": "float",
    "ptr": "uint32_t",
}
_READ = {
    "u8": "u8",
    "s8": "u8",
    "u16": "u16",
    "s16": "u16",
    "u32": "u32",
    "s32": "u32",
    "f32": "f32",
    "ptr": "u32",
}
_RAW = {"u8": "uint8_t", "u16": "uint16_t", "u32": "uint32_t", "f32": "float"}


def _c_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", name)


def _root(field: Field, by_name: dict[str, Field]) -> str:
    while field.base not in ("play", "actor", "abs"):
        field = by_name[field.base]
    return field.base


def _field_helpers(game: str, fields: list[Field]) -> list[str]:
    """The C helpers of every field; refuses two fields whose generated names collide."""
    by_name = {f.name: f for f in fields}
    prefix = _c_name(game).lower()
    owner: dict[str, str] = {}
    out: list[str] = []
    for f in fields:
        cn = f"{prefix}_{_c_name(f.name)}"
        root = _root(f, by_name)
        chained = f.base not in ("play", "actor", "abs")
        names = [cn.upper() + "_OFF", cn]
        names += [] if f.type == "ptr" else [cn + "_set"]
        names += [cn + "_at"] + ([] if f.type == "ptr" else [cn + "_set_at"]) if chained else []
        for n in names:
            if n in owner:
                raise SdkError(f"{game}.fields.tsv: {f.name} and {owner[n]} both generate {n}")
            owner[n] = f.name
        ct, raw, rw = _C_TYPE[f.type], _RAW[_READ[f.type]], _READ[f.type]
        val = "value" if f.type == "f32" else f"(uint32_t)({raw})value"
        null = (
            ["    if (v == 0u) return -1; /* a null pointer: nothing to follow */"]
            if f.type == "ptr"
            else []
        )
        out.append(f"#define {cn.upper()}_OFF 0x{f.offset:X}u")
        # the address of the field: its base's value (or the event argument) plus the offset
        pre: list[str] = []
        if f.base == "abs":
            param, base_expr = "", "0u"
        elif not chained:
            param, base_expr = f"uint32_t {root}", f.base
        else:
            parent = f"{prefix}_{_c_name(f.base)}"
            call = f"{parent}({root}, &b)" if root != "abs" else f"{parent}(&b)"
            param = f"uint32_t {root}" if root != "abs" else ""
            base_expr, pre = "b", ["    uint32_t b = 0;", f"    if ({call} != 0) return -1;"]
        lead = f"{param}, " if param else ""
        if chained:  # the _at forms take the base pointer already read
            out += [
                f"static inline int32_t {cn}_at(uint32_t base, {ct}* out) {{",
                f"    {raw} v = 0;",
                f"    if (staticr_read_{rw}(base + 0x{f.offset:X}u, &v) != 0) return -1;",
                *null,
                f"    *out = ({ct})v;",
                "    return 0;",
                "}",
            ]
            if f.type != "ptr":
                out += [
                    f"static inline int32_t {cn}_set_at(uint32_t base, {ct} value) {{",
                    f"    return staticr_write_{rw}(base + 0x{f.offset:X}u, {val});",
                    "}",
                ]
            out += [
                f"static inline int32_t {cn}({lead}{ct}* out) {{",
                *pre,
                f"    return {cn}_at(b, out);",
                "}",
            ]
            if f.type != "ptr":
                out += [
                    f"static inline int32_t {cn}_set({lead}{ct} value) {{",
                    *pre,
                    f"    return {cn}_set_at(b, value);",
                    "}",
                ]
            continue
        out += [
            f"static inline int32_t {cn}({lead}{ct}* out) {{",
            f"    {raw} v = 0;",
            f"    if (staticr_read_{rw}({base_expr} + 0x{f.offset:X}u, &v) != 0) return -1;",
            *null,
            f"    *out = ({ct})v;",
            "    return 0;",
            "}",
        ]
        if f.type != "ptr":
            out += [
                f"static inline int32_t {cn}_set({lead}{ct} value) {{",
                f"    return staticr_write_{rw}({base_expr} + 0x{f.offset:X}u, {val});",
                "}",
            ]
    return out


def game_header(game: str, repo: Path = REPO) -> str:
    """``staticr_<game>.h``: the events and, when the game has a field table, its fields."""
    games = known_games(repo)
    if game not in games:
        raise SdkError(f"no mod events for game {game!r} (games: {', '.join(games)})")
    events = load_events(repo)
    fields = load_fields(game, repo) if has_fields(game, repo) else []
    guard = f"STATICR_{_c_name(game).upper()}_H"
    out = [
        f"/* staticr_{game}.h: generated by `staticr-mods header {game}`",
        " * (staticr_mods/sdk.py) from",
        f" * {EVENTS_TSV}" + (f" and {FIELDS_DIR}/{game}.fields.tsv." if fields else "."),
        " * Do not edit; regenerate. staticr_mod_wasm.h must be on the include path. */",
        f"#ifndef {guard}",
        f"#define {guard}",
        "",
        '#include "staticr_mod_wasm.h"',
        "",
        "/* Events: the id (for staticr_event_count) and a handler macro. A handler is",
        " *     STATICR_ON_PLAY_UPDATE(int32_t play, int32_t unused) { ... }",
        " * and the host subscribes the mod to that event because the mod exports it. */",
    ]
    for e in events:
        args = ", ".join(a if a != "-" else "unused" for a in (e.a0, e.a1))
        out.append(f"#define STATICR_EVENT_{e.name.upper()} {e.id} /* ({args}): {e.fires} */")
        out.append(
            f"#define STATICR_ON_{e.name.upper()} STATICR_EXPORT(on_{e.name}) void on_{e.name}"
        )
    if fields:
        out += [
            "",
            "#ifdef __wasm__",
            f"/* Fields ({game}.fields.tsv). For each: <game>_<field>_OFF, the offset from",
            " * its base; <game>_<field>(root, &out) and <game>_<field>_set(root, value), which",
            " * take the field's root (play or actor, the event's argument; nothing for an",
            " * absolute address) and follow the pointer fields on the way; and, for a field",
            " * whose base is a pointer field, <game>_<field>_at(base, &out) and",
            " * _set_at(base, value), which take that base already read (one pointer read for",
            " * several fields). All return 0, or -1 when a read is refused or a pointer is null",
            " * (outputs unchanged), as the Tier 0 engine skips such a rule. Pointer fields have",
            " * no setter. */",
        ]
        out += _field_helpers(game, fields)
        out.append("#endif /* __wasm__ */")
    out += ["", f"#endif /* {guard} */", ""]
    return "\n".join(out)

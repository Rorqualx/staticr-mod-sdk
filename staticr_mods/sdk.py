"""The staticR mod SDK (``docs/design/mod-sdk.md`` M.2f): start, check, build and pack a mod.

* :func:`game_header` generates ``staticr_<game>.h`` from the event catalogue
  (``configs/mods/events.tsv``) and the game's field table
  (``configs/mods/fields/<game>.fields.tsv``):
  event ids, one handler macro per event, and per field its offset plus read/write helpers that
  follow pointer fields (``play`` -> ``player`` -> ``player.shock_timer``) as the Tier 0
  engine does.
* :func:`new_mod` writes a starter mod: Tier 0 (one ``.mod.toml``) or Tier 1 (C source, manifest,
  both headers, a build script, a README).
* :func:`build_mod` compiles a Tier 1 mod with clang ``--target=wasm32`` and wasm-ld (or a stable
  Rust toolchain's rust-lld), then :func:`check_mod` checks what it can without a game: the
  manifest as the host checks it, a Tier 0 mod's rules against the catalogue and the field table, a
  Tier 1 module's imports and exports against the ABI (``host/staticr_mod_wasm.h``).
* :func:`pack_mod` writes a ``.staticrmod`` and checks it with
  :func:`staticr_mods.sdk_manifest.check_package`, which the tests pin to the launcher's reader.

The game's mod hosts stay the final judge (``host/staticr_mod_tier0.cpp``,
``host/staticr_mod_tier1*.cpp``); ``tests/test_sdk.py`` pins this module's copies of their tables
(rule keys, buttons, operators, imports) to those files, so they cannot drift apart silently.
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import sdk_manifest
from .sdk_header import (  # noqa: F401  (re-exported: the SDK's public names)
    ABI_HEADER,
    EVENTS_TSV,
    FIELDS_DIR,
    FIELDS_FORMAT,
    REPO,
    Event,
    Field,
    SdkError,
    _c_name,
    _root,
    fields_path,
    game_header,
    has_fields,
    known_games,
    load_events,
    load_fields,
)

# --- the ABI (imports) ------------------------------------------------------------------------

_WASM_TYPES = {"int32_t": "i32", "uint32_t": "i32", "int64_t": "i64", "float": "f32"}


def _c_param_type(decl: str) -> str:
    decl = decl.strip()
    if "*" in decl:
        return "i32"  # a pointer into the mod's memory
    head = decl.replace("const ", "").split()[0]
    if head not in _WASM_TYPES:
        raise SdkError(f"{ABI_HEADER}: no wasm type for {decl!r}")
    return _WASM_TYPES[head]


def abi_imports(repo: Path = REPO) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """The imports ``host/staticr_mod_wasm.h`` declares: name -> (params, results), wasm types."""
    text = (repo / ABI_HEADER).read_text(encoding="utf-8")
    out: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    pat = re.compile(r"STATICR_IMPORT\((\w+)\)\s+([\w ]+?)\s+staticr_\w+\(([^)]*)\)\s*;")
    for name, result, params in pat.findall(text):
        res = () if result.strip() == "void" else (_c_param_type(result),)
        ps = tuple(_c_param_type(p) for p in params.split(",") if p.strip() and p.strip() != "void")
        out[name] = (ps, res)
    if not out:
        raise SdkError(f"{ABI_HEADER}: no STATICR_IMPORT declarations found")
    declared = len(re.findall(r"^\s*STATICR_IMPORT\(\w+\)\s", text, re.MULTILINE))
    if declared != len(out):
        raise SdkError(
            f"{ABI_HEADER}: {declared} STATICR_IMPORT declarations, {len(out)} understood"
            " (a return or parameter type the SDK cannot read)"
        )
    return out


def abi_version(repo: Path = REPO) -> int:
    m = re.search(
        r"#define STATICR_MOD_WASM_ABI (\d+)", (repo / ABI_HEADER).read_text(encoding="utf-8")
    )
    if m is None:
        raise SdkError(f"{ABI_HEADER}: no STATICR_MOD_WASM_ABI")
    return int(m.group(1))


# --- a wasm module's imports and exports ------------------------------------------------------


Sig = tuple[tuple[str, ...], tuple[str, ...]]


@dataclass(frozen=True)
class WasmInterface:
    imports: list[tuple[str, str, str, Sig | None]]
    """(module, name, kind, signature for a function)"""
    exports: dict[str, tuple[str, Sig | None]]
    """name -> (kind, signature for a function)"""
    memory_pages: int | None = None
    """the initial size, in 64 KiB pages, of the module's own memory (None: it defines none)"""


_VAL = {
    0x7F: "i32",
    0x7E: "i64",
    0x7D: "f32",
    0x7C: "f64",
    0x7B: "v128",
    0x70: "funcref",
    0x6F: "externref",
}
_KIND = {0: "func", 1: "table", 2: "memory", 3: "global"}


class _Reader:
    def __init__(self, data: bytes, pos: int = 0, end: int | None = None) -> None:
        self.data, self.pos, self.end = data, pos, len(data) if end is None else end

    def byte(self) -> int:
        if self.pos >= self.end:
            raise SdkError("the module ends inside a section")
        b = self.data[self.pos]
        self.pos += 1
        return b

    def uleb(self) -> int:
        result = shift = 0
        while True:
            b = self.byte()
            result |= (b & 0x7F) << shift
            if not b & 0x80:
                return result
            shift += 7
            if shift > 35:
                raise SdkError("a LEB128 number is too long")

    def name(self) -> str:
        n = self.uleb()
        if self.pos + n > self.end:
            raise SdkError("a name runs past its section")
        raw = self.data[self.pos : self.pos + n]
        self.pos += n
        return raw.decode("utf-8", "replace")

    def limits(self) -> int:
        """Read a limits pair; return the minimum (the initial size)."""
        flags = self.byte()
        initial = self.uleb()
        if flags & 1:
            self.uleb()
        return initial


def _skip_import_desc(body: _Reader, kind: int) -> None:
    """Read past a table, memory or global import's description."""
    if kind == 1:
        body.byte()
        body.limits()
    elif kind == 2:
        body.limits()
    elif kind == 3:
        body.byte()
        body.byte()


def _read_types(body: _Reader) -> list[Sig]:
    types: list[Sig] = []
    for _ in range(body.uleb()):
        if body.byte() != 0x60:
            raise SdkError("a type that is not a function type")
        ps = tuple(_VAL.get(body.byte(), "?") for _ in range(body.uleb()))
        rs = tuple(_VAL.get(body.byte(), "?") for _ in range(body.uleb()))
        types.append((ps, rs))
    return types


def _read_imports(
    body: _Reader, types: list[Sig], func_types: list[int]
) -> list[tuple[str, str, str, Sig | None]]:
    """The import section; each imported function's type joins ``func_types``."""
    imports: list[tuple[str, str, str, Sig | None]] = []
    for _ in range(body.uleb()):
        mod, name, kind = body.name(), body.name(), body.byte()
        sig = None
        if kind == 0:
            t = body.uleb()
            sig = types[t] if t < len(types) else None
            func_types.append(t)
        _skip_import_desc(body, kind)
        imports.append((mod, name, _KIND.get(kind, "?"), sig))
    return imports


def read_interface(wasm: bytes) -> WasmInterface:
    """The imports and exports of a core module (sections 1, 2, 3 and 7)."""
    if wasm[:8] != b"\0asm\1\0\0\0":
        raise SdkError("not a WebAssembly 1.0 core module")
    types: list[Sig] = []
    imports: list[tuple[str, str, str, Sig | None]] = []
    func_types: list[int] = []  # the function index space: imported functions first
    exports_raw: list[tuple[str, int, int]] = []
    memory_pages: int | None = None
    pos = 8
    while pos < len(wasm):
        r = _Reader(wasm, pos)
        sid, size = r.byte(), r.uleb()
        body = _Reader(wasm, r.pos, r.pos + size)
        if body.end > len(wasm):
            raise SdkError("a section runs past the end of the module")
        if sid == 1:
            types = _read_types(body)
        elif sid == 2:
            imports += _read_imports(body, types, func_types)
        elif sid == 3:
            func_types += [body.uleb() for _ in range(body.uleb())]
        elif sid == 5:
            pages = [body.limits() for _ in range(body.uleb())]
            memory_pages = pages[0] if pages else None
        elif sid == 7:
            exports_raw += [(body.name(), body.byte(), body.uleb()) for _ in range(body.uleb())]
        pos = body.end
    exports: dict[str, tuple[str, Sig | None]] = {}
    for name, kind, index in exports_raw:
        ok = kind == 0 and index < len(func_types) and func_types[index] < len(types)
        exports[name] = (_KIND.get(kind, "?"), types[func_types[index]] if ok else None)
    return WasmInterface(imports, exports, memory_pages)


def _import_problems(iface: WasmInterface, abi: dict[str, Sig]) -> list[str]:
    problems: list[str] = []
    for mod, name, kind, sig in iface.imports:
        if mod != "staticr" or kind != "func":
            problems.append(
                f"imports {mod}.{name} ({kind}): the host gives only functions of module staticr"
            )
        elif name not in abi:
            problems.append(f"imports staticr.{name}, which the host does not have")
        elif sig != abi[name]:
            problems.append(f"imports staticr.{name} as {sig}, the host's is {abi[name]}")
    return problems


def _handler_problems(iface: WasmInterface, events: set[str]) -> list[str]:
    problems: list[str] = []
    handlers = 0
    for name, (kind, sig) in iface.exports.items():
        if not name.startswith("on_"):
            continue
        if name[3:] not in events:
            continue  # the host looks up only the catalogue's names (wasm_notes reports these)
        if kind != "func" or sig != (("i32", "i32"), ()):
            problems.append(f"{name} must be (i32, i32) -> (), it is {sig}")
        else:
            handlers += 1
    if handlers == 0:
        problems.append("exports no event handler (on_<event>), so it would never run")
    return problems


MEMORY_CAP = 16 << 20  # a mod's linear memory, both hosts (staticr_mod_tier1.h memory_cap)


def wasm_notes(wasm: bytes, repo: Path = REPO) -> list[str]:
    """What the host would load but ignore: ``on_*`` exports that name no event (a typo?)."""
    events = {e.name for e in load_events(repo)}
    return [
        f"exports {name}, but there is no event {name[3:]!r} ({EVENTS_TSV}); the host ignores it"
        for name in read_interface(wasm).exports
        if name.startswith("on_") and name[3:] not in events
    ]


def check_wasm(wasm: bytes, repo: Path = REPO) -> list[str]:
    """What the host would refuse in a Tier 1 module, as messages ([] = none)."""
    problems: list[str] = []
    if len(wasm) > sdk_manifest.WASM_MAX_BYTES:
        problems.append(f"the module is {len(wasm)} bytes, over {sdk_manifest.WASM_MAX_BYTES}")
    iface = read_interface(wasm)
    problems += _import_problems(iface, abi_imports(repo))
    abi_export = iface.exports.get("staticr_abi")
    if abi_export is None:
        problems.append(
            "does not export staticr_abi (use STATICR_MOD_ABI() from staticr_mod_wasm.h)"
        )
    elif abi_export != ("func", ((), ("i32",))):
        problems.append(f"staticr_abi must be a function () -> i32, it is {abi_export}")
    if iface.exports.get("memory", ("", None))[0] != "memory":
        problems.append("does not export its memory")
    if iface.memory_pages is not None and iface.memory_pages * 65536 > MEMORY_CAP:
        problems.append(
            f"its memory starts at {iface.memory_pages * 64} KiB, over the host's "
            f"{MEMORY_CAP >> 20} MiB cap (large static arrays count)"
        )
    init = iface.exports.get("staticr_init")
    if init is not None and init != ("func", ((), ("i32",))):
        problems.append(f"staticr_init must be () -> i32, it is {init}")
    return problems + _handler_problems(iface, {e.name for e in load_events(repo)})


# --- Tier 0 rules -----------------------------------------------------------------------------

RULE_KEYS = ("on", "pad_pressed", "pad_held", "when", "cooldown", "set", "add", "rumble", "log")
BUTTONS = (
    "A",
    "B",
    "Z",
    "START",
    "DUP",
    "DDOWN",
    "DLEFT",
    "DRIGHT",
    "L",
    "R",
    "CUP",
    "CDOWN",
    "CLEFT",
    "CRIGHT",
)
OPS = ("==", "!=", "<", "<=", ">", ">=", "&", "!&")
_LIMIT = 4294967296


def _number(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return -_LIMIT <= v <= _LIMIT
    return isinstance(v, float) and v == v and abs(v) <= _LIMIT  # NaN fails v == v


def _field_problem(name: Any, ev: Event, fields: dict[str, Field]) -> str | None:
    f = fields.get(name) if isinstance(name, str) else None
    if f is None:
        return f"unknown field {name!r}"
    root = _root(f, fields)
    if root in ("play", "actor") and root not in (ev.a0, ev.a1):
        need = "play" if root == "play" else "an actor"
        return f"field {name!r} needs {need}, which event {ev.name!r} does not carry"
    return None


def _pad_problem(rule: dict[str, Any]) -> str | None:
    for key in ("pad_pressed", "pad_held"):
        if key in rule:
            v = rule[key]
            if not isinstance(v, str):
                return f'{key} must be a string like "L+DLEFT"'
            bad = [b for b in v.split("+") if b not in BUTTONS]
            if bad:
                return f"unknown button {bad[0]!r} ({' '.join(BUTTONS)})"
    return None


def _when_problem(rule: dict[str, Any], ev: Event, fields: dict[str, Field]) -> str | None:
    when = rule.get("when")
    if when is None:
        return None
    if not isinstance(when, list):
        return "'when' must be an array of { field, op, value }"
    for c in when:
        if not isinstance(c, dict) or set(c) != {"field", "op", "value"}:
            return "'when' entries are { field, op, value }"
        if (err := _field_problem(c["field"], ev, fields)) is not None:
            return err
        if c["op"] not in OPS:
            return f"op must be one of {' '.join(OPS)}"
        if not _number(c["value"]):
            return "'when' value must be a number"
    return None


def _writes_problem(rule: dict[str, Any], ev: Event, fields: dict[str, Field]) -> str | None:
    for key in ("set", "add"):
        writes = rule.get(key)
        if writes is None:
            continue
        if not isinstance(writes, list):
            return f"{key} must be an array of {{ field, value }}"
        for w in writes:
            if not isinstance(w, dict) or set(w) != {"field", "value"}:
                return f"{key} entries are {{ field, value }}"
            if (err := _field_problem(w["field"], ev, fields)) is not None:
                return err
            if fields[w["field"]].type == "ptr":
                return f"field {w['field']!r} is a pointer; mods cannot write it"
            if not _number(w["value"]):
                return f"{key} value must be a number"
    return None


def _rest_problem(rule: dict[str, Any]) -> str | None:
    for key in ("cooldown", "rumble"):
        if key in rule:
            v = rule[key]
            if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 100000:
                return f"{key} must be a whole number of events, 0-100000"
    if "log" in rule and not isinstance(rule["log"], str):
        return "log must be a string"
    if not (rule.get("set") or rule.get("add") or rule.get("rumble") or rule.get("log")):
        return "does nothing (no set, add, rumble or log)"
    return None


def _rule_problem(rule: Any, events: dict[str, Event], fields: dict[str, Field]) -> str | None:
    """The engine's first refusal for one rule, in its order, or None."""
    if not isinstance(rule, dict):
        return "not a table"
    unknown = [k for k in rule if k not in RULE_KEYS]
    if unknown:
        return f"unknown key {unknown[0]!r}"
    on = rule.get("on")
    ev = events.get(on) if isinstance(on, str) else None
    if ev is None:
        return f"'on' must name an event ({EVENTS_TSV})"
    return (
        _pad_problem(rule)
        or _when_problem(rule, ev, fields)
        or _writes_problem(rule, ev, fields)
        or _rest_problem(rule)
    )


def check_rules(table: dict[str, Any], game: str, repo: Path = REPO) -> list[str]:
    """What the Tier 0 engine would refuse in a manifest's rules: the first refusal, as the engine
    refuses the whole mod at its first one."""
    if not has_fields(game, repo):
        return [f"game {game!r} has no field table ({FIELDS_DIR}), so it runs no Tier 0 mod"]
    fields = {f.name: f for f in load_fields(game, repo)}
    events = {e.name: e for e in load_events(repo)}
    rules = table.get("rule")
    if not isinstance(rules, list) or not rules:
        return ["no [[rule]]"]
    for n, rule in enumerate(rules, 1):
        if (err := _rule_problem(rule, events, fields)) is not None:
            return [f"rule {n}: {err}"]
    return []


# --- a mod directory --------------------------------------------------------------------------


@dataclass(frozen=True)
class ModDir:
    path: Path
    manifest: Path
    table: dict[str, Any]
    mod_id: str
    game: str
    wasm: str | None  # the module file name, for Tier 1


def read_mod_dir(path: str | Path) -> ModDir:
    """The one ``*.mod.toml`` in ``path``, checked as the host checks a manifest."""
    d = Path(path)
    manifests = sorted(d.glob("*" + sdk_manifest.SUFFIX))
    if len(manifests) != 1:
        raise SdkError(f"{d} must hold exactly one *.mod.toml, it has {len(manifests)}")
    text = manifests[0].read_text(encoding="utf-8")
    try:
        m = sdk_manifest.parse_manifest(text)
    except sdk_manifest.ManifestError as exc:
        raise SdkError(f"{manifests[0].name}: {exc}") from exc
    return ModDir(d, manifests[0], m.table, m.id, m.game, m.wasm)


def check_mod(path: str | Path, repo: Path = REPO) -> list[str]:
    """Everything the SDK can check without a game ([] = nothing found); for Tier 1, the built
    module."""
    m = read_mod_dir(path)
    games = known_games(repo)
    if m.game not in games:
        return [f"game {m.game!r} fires no mod events (games: {', '.join(games)})"]
    if m.wasm is None:
        return check_rules(m.table, m.game, repo)
    module = m.path / m.wasm
    if not module.is_file():
        return [f"no {m.wasm} yet (run `staticr-mods build {m.path}`)"]
    return check_wasm(module.read_bytes(), repo)


def mod_notes(path: str | Path, repo: Path = REPO) -> list[str]:
    """What is worth saying but is no refusal: a Tier 1 module older than a source next to it
    (rebuild before packing, if they changed), and ``on_*`` exports the host ignores."""
    m = read_mod_dir(path)
    module = m.path / m.wasm if m.wasm else None
    if module is None or not module.is_file():
        return []
    built = module.stat().st_mtime
    newer = [p.name for p in sorted(m.path.glob("*.[ch]")) if p.stat().st_mtime > built]
    notes = []
    if newer:
        notes.append(f"{m.wasm} is older than {', '.join(newer)}: build again if they changed")
    return notes + wasm_notes(module.read_bytes(), repo)


def wasm_toolchain() -> tuple[str, list[str]]:
    """(clang, the linker command) for wasm32, or SdkError naming what is missing."""
    clang = shutil.which("clang")
    if clang is None:
        raise SdkError("no clang on PATH (any clang with the wasm32 target: Xcode's, LLVM's)")
    lld = shutil.which("wasm-ld") or next(
        (
            str(p)
            for p in sorted(
                Path.home().glob(".rustup/toolchains/stable-*/lib/rustlib/*/bin/rust-lld*")
            )
            if p.suffix in ("", ".exe")
        ),
        None,
    )
    if lld is None:
        raise SdkError(
            "no wasm linker: wasm-ld (LLVM's lld) or rust-lld from a stable Rust toolchain"
        )
    return clang, [lld] + (["-flavor", "wasm"] if "rust-lld" in Path(lld).name else [])


def build_mod(path: str | Path, repo: Path = REPO) -> Path:
    """Compile a Tier 1 mod's ``*.c`` into the module its manifest names, then check it."""
    m = read_mod_dir(path)
    if m.wasm is None:
        raise SdkError(f"{m.manifest.name} is a Tier 0 mod (rules): there is nothing to build")
    sources = sorted(m.path.glob("*.c"))
    if not sources:
        raise SdkError(f"no *.c in {m.path}")
    clang, link = wasm_toolchain()
    out = m.path / m.wasm
    objs: list[Path] = []
    build = m.path / "build"
    build.mkdir(exist_ok=True)
    # the headers, current: in build/ (so a mod needs only its .c) and over the mod's own copies
    # when it has them (`new` writes them; a quoted include finds those first, and both are
    # generated, not edited)
    fresh = {
        "staticr_mod_wasm.h": (repo / ABI_HEADER).read_bytes(),
        f"staticr_{m.game}.h": game_header(m.game, repo).encode("utf-8"),
    }
    for name, data in fresh.items():
        (build / name).write_bytes(data)
        own = m.path / name
        if own.is_file() and own.read_bytes() != data:
            own.write_bytes(data)
    for src in sources:
        obj = build / (src.stem + ".o")
        cmd = [
            clang,
            "--target=wasm32",
            "-O2",
            "-nostdlib",
            "-fno-builtin",
            "-Wall",
            "-Wextra",
            "-I",
            str(m.path),
            "-I",
            str(build),
            "-c",
            str(src),
            "-o",
            str(obj),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if r.returncode != 0:
            raise SdkError(f"clang failed on {src.name}:\n{r.stderr[-3000:]}")
        objs.append(obj)
    r = subprocess.run(
        [*link, "--no-entry", "-o", str(out), *map(str, objs)],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        raise SdkError(f"the wasm link failed:\n{r.stderr[-3000:]}")
    problems = check_wasm(out.read_bytes(), repo)
    if problems:
        raise SdkError(f"{out.name} would be refused by the host:\n- " + "\n- ".join(problems))
    return out


def pack_mod(path: str | Path, out: str | Path | None = None, repo: Path = REPO) -> Path:
    """Write ``<id>.staticrmod`` (manifest, module, optional ``<id>.json`` and ``thumb.png``) after
    :func:`check_mod`, then read it back with the launcher's reader."""
    m = read_mod_dir(path)
    problems = check_mod(m.path, repo)
    if problems:
        raise SdkError(f"{m.mod_id} would be refused:\n- " + "\n- ".join(problems))
    members = [m.manifest] + ([m.path / m.wasm] if m.wasm else [])
    members += [
        p for p in (m.path / f"{m.mod_id}.json", m.path / sdk_manifest.THUMBNAIL) if p.is_file()
    ]
    dest = Path(out) if out is not None else m.path / f"{m.mod_id}{sdk_manifest.PACKAGE_SUFFIX}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in members:
            z.write(p, p.name)
    data = buf.getvalue()
    try:
        sdk_manifest.check_package(data, game=m.game)
    except sdk_manifest.ManifestError as exc:
        raise SdkError(f"the launcher would refuse the package: {exc}") from exc
    dest.write_bytes(data)
    return dest


# --- a new mod --------------------------------------------------------------------------------

_TIER0 = """\
# {name}: a staticR Tier 0 (declarative) mod for {game}. Made by `staticr-mods new`.
# A rule fires on an event (on = ...), when its conditions hold, and sets or adds to named fields.
# Check it: staticr-mods check .    Package it: staticr-mods pack .
# Install: the launcher's Mods page (Add...) takes the .staticrmod, or copy this file into
# <config dir>/staticr_mods/.
format = "staticr.mod/1"
id = "{id}"
name = "{name}"
description = "D-pad Left: Link flashes red with lightning over him (no damage)."
game = "{game}"

[[rule]]
on = "play_update"        # events: {events}
pad_pressed = "DLEFT"     # buttons: {buttons}
cooldown = 40             # events before the rule may fire again
rumble = 10               # events of rumble
set = [
  {{ field = "player.invincibility_timer", value = 20 }},
  {{ field = "player.shock_timer", value = 40 }},
]
log = "{id}: shock"
# when = [{{ field = "player.speed", op = ">", value = 0 }}]   # ops: {ops}
#
# Fields of {game} (name: type, base):
{field_list}
"""

_TIER1_MANIFEST = """\
# {name}: a staticR Tier 1 (WASM) mod for {game}. Made by `staticr-mods new`.
format = "staticr.mod/1"
id = "{id}"
name = "{name}"
description = "{description}"
game = "{game}"
wasm = "{id}.wasm"
"""

_TIER1_C_FIELDS = """\
/* {name}: a staticR Tier 1 (WASM) mod for {game}, made by `staticr-mods new`.
 * Build: staticr-mods build .   Package: staticr-mods pack .   (README.md says more)
 * staticr_mod_wasm.h is the host's API, staticr_{game}.h this game's events and fields. */
#include "staticr_{game}.h"

STATICR_MOD_ABI()

static int32_t last = -1; /* the last speed seen, in whole units; -1 = none yet */
static char text[64];
static uint32_t len;

static void put(const char* s) {{
    while (*s && len < sizeof text) text[len++] = *s++;
}}

static void put_int(int32_t v) {{
    char d[12];
    int n = 0;
    uint32_t u = v < 0 ? 0u - (uint32_t)v : (uint32_t)v;
    if (v < 0) put("-");
    do {{ d[n++] = (char)('0' + u % 10u); u /= 10u; }} while (u != 0u && n < 12);
    while (n > 0 && len < sizeof text) text[len++] = d[--n];
}}

STATICR_EXPORT(staticr_init) int32_t staticr_init(void) {{
    staticr_log(STATICR_LOG_INFO, "loaded", 6);
    return 0; /* non-zero refuses the mod */
}}

/* once per gameplay update (about 20 a second): log Link's speed when it changes */
STATICR_ON_PLAY_UPDATE(int32_t play, int32_t unused) {{
    (void)unused;
    float speed = 0.0f;
    if ({prefix}_player_speed((uint32_t)play, &speed) != 0) return;
    const int32_t now = (int32_t)speed;
    if (now == last) return;
    last = now;
    len = 0;
    put("speed ");
    put_int(now);
    staticr_log(STATICR_LOG_INFO, text, len);
}}
"""

_TIER1_C_EVENTS = """\
/* {name}: a staticR Tier 1 (WASM) mod for {game}, made by `staticr-mods new`.
 * Build: staticr-mods build .   Package: staticr-mods pack .   (README.md says more)
 * staticr_mod_wasm.h is the host's API, staticr_{game}.h this game's events ({game} has no field
 * table yet, so guest memory is read by address with staticr_read_u8/u16/u32/f32). */
#include "staticr_{game}.h"

STATICR_MOD_ABI()

STATICR_EXPORT(staticr_init) int32_t staticr_init(void) {{
    staticr_log(STATICR_LOG_INFO, "loaded", 6);
    return 0; /* non-zero refuses the mod */
}}

/* a gameplay scene starts */
STATICR_ON_PLAY_INIT(int32_t play, int32_t unused) {{
    (void)play; (void)unused;
    staticr_log(STATICR_LOG_INFO, "a scene starts", 14);
}}
"""

_BUILD_SH = """\
#!/bin/sh
# Build {id}.wasm without the SDK tool: clang with the wasm32 target, then wasm-ld (or rust-lld
# from a stable Rust toolchain: rust-lld -flavor wasm). `staticr-mods build .` does the same and
# also checks the module against the host's imports and exports.
set -eu
cd "$(dirname "$0")"
LD="${{WASM_LD:-wasm-ld}}"
mkdir -p build
for c in *.c; do
  clang --target=wasm32 -O2 -nostdlib -fno-builtin -Wall -Wextra -I . -c "$c" -o "build/${{c%.c}}.o"
done
$LD --no-entry -o {id}.wasm build/*.o
echo "built {id}.wasm"
"""

_README = """\
# {name}

A staticR Tier 1 (WASM) mod for {game}, made by `staticr-mods new`.

| File | What |
|---|---|
| `{id}.mod.toml` | the manifest: `id`, `name`, `game`, and `wasm`, the module's file name |
| `{id}.c` | the mod |
| `staticr_mod_wasm.h` | the host's API (ABI {abi}): guest memory, log, pad, rumble, settings |
| `staticr_{game}.h` | {game}'s events{fields_note} (generated: `staticr-mods header {game}`) |
| `build.sh` | the build without the SDK tool (`WASM_LD="rust-lld -flavor wasm"` for rust-lld) |

Build, check and package (needs clang, and wasm-ld or a stable Rust toolchain's rust-lld):

    staticr-mods build .     # {id}.wasm, checked against the host's imports and exports
    staticr-mods pack .      # {id}.staticrmod, checked by the launcher's own package reader

Install: the launcher's Mods page, Add..., takes the `.staticrmod`. A mod runs on the game thread
with a time budget per event; a mod that runs past it, or traps, is unloaded alone and the game
goes on. It keeps its state in its own globals, and can keep settings in `{id}.json` (put a
default one next to the manifest and `pack` includes it).
"""


def new_mod(
    game: str,
    dest: str | Path,
    *,
    tier: int,
    mod_id: str | None = None,
    name: str | None = None,
    repo: Path = REPO,
) -> list[Path]:
    """Write a starter mod into ``dest`` (created; refused when it already holds files)."""
    games = known_games(repo)
    if game not in games:
        raise SdkError(f"no mod events for game {game!r} (games: {', '.join(games)})")
    if tier not in (0, 1):
        raise SdkError("tier is 0 (rules) or 1 (WASM)")
    fields = load_fields(game, repo) if has_fields(game, repo) else []
    if tier == 0 and not fields:
        raise SdkError(
            f"game {game!r} has no field table ({FIELDS_DIR}), so only Tier 1 mods run on it"
        )
    d = Path(dest)
    mod_id = mod_id or d.resolve().name
    if not sdk_manifest.valid_id(mod_id):
        raise SdkError(
            f"{mod_id!r} is not a mod id (1-64 of A-Z a-z 0-9 _ . -, not starting with '.')"
        )
    name = name or mod_id
    if any(bad in name for bad in ('"', "\\", "*/")) or any(
        ord(c) < 0x20 or ord(c) == 0x7F for c in name
    ):
        raise SdkError("a name holds no quotes, backslashes, control characters or */")
    if d.exists() and any(d.iterdir()):
        raise SdkError(f"{d} is not empty")
    if tier == 0 and not {"player.shock_timer", "player.invincibility_timer"} <= {
        f.name for f in fields
    }:
        raise SdkError(
            f"the Tier 0 starter needs {game}'s player.shock_timer and invincibility_timer fields"
        )
    d.mkdir(parents=True, exist_ok=True)
    events = load_events(repo)
    written: list[Path] = []

    def write(rel: str, text: str) -> None:
        p = d / rel
        p.write_text(text, encoding="utf-8")
        written.append(p)

    if tier == 0:
        by_name = {f.name: f for f in fields}
        listing = "\n".join(f"#   {f.name}: {f.type}, {_root(f, by_name)}" for f in fields)
        write(
            f"{mod_id}.mod.toml",
            _TIER0.format(
                id=mod_id,
                name=name,
                game=game,
                events=" ".join(e.name for e in events),
                buttons=" ".join(BUTTONS),
                ops=" ".join(OPS),
                field_list=listing,
            ),
        )
        return written
    has_speed = any(f.name == "player.speed" for f in fields)
    description = (
        "Logs Link's speed each time it changes." if has_speed else "Logs each scene start."
    )
    write(
        f"{mod_id}.mod.toml",
        _TIER1_MANIFEST.format(id=mod_id, name=name, game=game, description=description),
    )
    src = _TIER1_C_FIELDS if has_speed else _TIER1_C_EVENTS
    write(f"{mod_id}.c", src.format(id=mod_id, name=name, game=game, prefix=_c_name(game).lower()))
    shutil.copyfile(repo / ABI_HEADER, d / "staticr_mod_wasm.h")
    written.append(d / "staticr_mod_wasm.h")
    write(f"staticr_{game}.h", game_header(game, repo))
    build_sh = d / "build.sh"
    build_sh.write_text(_BUILD_SH.format(id=mod_id), encoding="utf-8")
    build_sh.chmod(0o755)
    written.append(build_sh)
    write(
        "README.md",
        _README.format(
            id=mod_id,
            name=name,
            game=game,
            abi=abi_version(repo),
            fields_note=" and fields" if fields else "",
        ),
    )
    return written


__all__ = [
    "Event",
    "Field",
    "ModDir",
    "SdkError",
    "WasmInterface",
    "abi_imports",
    "abi_version",
    "build_mod",
    "check_mod",
    "check_rules",
    "check_wasm",
    "game_header",
    "known_games",
    "load_events",
    "load_fields",
    "mod_notes",
    "new_mod",
    "pack_mod",
    "read_interface",
    "read_mod_dir",
    "wasm_notes",
    "wasm_toolchain",
]

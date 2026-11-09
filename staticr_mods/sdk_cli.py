"""The mod SDK's commands: ``new``, ``header``, ``check``, ``build`` and ``pack``.

Usage::

    staticr-mods new      <game> <dir> --tier 0|1 [--id ID] [--name NAME]
    staticr-mods header   <game> [-o staticr_<game>.h]
    staticr-mods check    <mod-dir>
    staticr-mods build    <mod-dir>
    staticr-mods pack     <mod-dir> [-o <id>.staticrmod]

The full ``staticr-mods`` (:mod:`staticr_mods.cli`, the mod manager) adds these to its own
commands. The standalone SDK (the staticr-mod-sdk repo, ``bin/mod_sdk_release.py``) ships only this
module and :mod:`staticr_mods.sdk` with what they import, so :func:`main` is its entry point.
Errors go to stderr with exit 1.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from . import sdk


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the SDK's subcommands to a ``staticr-mods`` parser."""
    new_p = sub.add_parser("new", help="start a staticR-native mod (Tier 0 rules or Tier 1 WASM)")
    new_p.add_argument("game", help="the game's short name (oot, mm)")
    new_p.add_argument("dir")
    new_p.add_argument("--tier", type=int, required=True, choices=(0, 1))
    new_p.add_argument("--id", default=None, help="mod id (default: the directory's name)")
    new_p.add_argument("--name", default=None)

    header_p = sub.add_parser("header", help="print (or write) a game's generated mod header")
    header_p.add_argument("game")
    header_p.add_argument("-o", "--out", default=None)

    check_p = sub.add_parser("check", help="check a mod directory as the host would")
    check_p.add_argument("dir")
    build_p = sub.add_parser("build", help="compile a Tier 1 mod to wasm32 and check it")
    build_p.add_argument("dir")
    pack_p = sub.add_parser("pack", help="write a checked .staticrmod package")
    pack_p.add_argument("dir")
    pack_p.add_argument("-o", "--out", default=None)


def _new(args: argparse.Namespace) -> int:
    written = sdk.new_mod(args.game, args.dir, tier=args.tier, mod_id=args.id, name=args.name)
    for path in written:
        print(path)
    print(f"{len(written)} file(s): a Tier {args.tier} mod for {args.game} in {args.dir}")
    return 0


def _header(args: argparse.Namespace) -> int:
    text = sdk.game_header(args.game)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(args.out)
    else:
        sys.stdout.write(text)
    return 0


def _check(args: argparse.Namespace) -> int:
    problems = sdk.check_mod(args.dir)
    for problem in problems:
        print(f"refused: {problem}", file=sys.stderr)
    if problems:
        return 1
    _notes(args.dir)
    print(f"ok: {sdk.read_mod_dir(args.dir).mod_id}")
    return 0


def _notes(directory: str) -> None:
    for note in sdk.mod_notes(directory):
        print(f"note: {note}", file=sys.stderr)


def _build(args: argparse.Namespace) -> int:
    out = sdk.build_mod(args.dir)
    _notes(args.dir)
    print(f"built {out} ({out.stat().st_size} bytes), checked against the host's ABI")
    return 0


def _pack(args: argparse.Namespace) -> int:
    _notes(args.dir)
    out = sdk.pack_mod(args.dir, args.out)
    print(f"packed {out} ({out.stat().st_size} bytes), checked as the launcher checks a package")
    return 0


HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "new": _new,
    "header": _header,
    "check": _check,
    "build": _build,
    "pack": _pack,
}


def main(argv: list[str] | None = None) -> int:
    """The standalone SDK's ``staticr-mods``: only the SDK commands."""
    parser = argparse.ArgumentParser(prog="staticr-mods", description=__doc__)
    add_commands(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    try:
        return HANDLERS[args.command](args)
    except (ValueError, OSError) as exc:  # SdkError is a ValueError
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

__all__ = ["HANDLERS", "add_commands", "main"]

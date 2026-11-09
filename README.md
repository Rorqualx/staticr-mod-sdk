# staticR mod SDK

Write mods for the games staticR runs (on Mac, Windows, Linux and the web), without the staticR
source tree:

- **Tier 0**: one `<id>.mod.toml` of rules (on an event, when conditions hold, set or add to named
  fields, rumble, log). Nothing to compile.
- **Tier 1**: C compiled to a wasm32 module, against the host API in
  `include/staticr_mod_wasm.h` (ABI 1) and a generated header per game.

The guide is [docs/guide.md](docs/guide.md).

## Install

Python 3.11 or newer:

    git clone <this repo> staticr-mod-sdk
    python3 -m venv .venv && . .venv/bin/activate
    pip install ./staticr-mod-sdk

That puts `staticr-mods` on your PATH. A Tier 1 mod also needs clang with the wasm32 target, and
wasm-ld (LLVM's lld) or the rust-lld of a stable Rust toolchain.

## Use

    staticr-mods new oot my-shock --tier 0     # a rules mod
    staticr-mods new oot my-mod --tier 1       # a WASM mod: C source, manifest, headers, build.sh
    staticr-mods build my-mod                  # my-mod.wasm, checked against the host's ABI
    staticr-mods check my-shock                # the rules, checked as the game checks them
    staticr-mods pack my-mod                   # my-mod.staticrmod for the launcher's Mods page

Games: mm, oot. Tier 0 needs a field table, so it is oot only for now.

## What is here

| Path | What |
|---|---|
| `staticr_mods/` | the `staticr-mods` tool (standard library only); `sdk_data/` holds its tables |
| `include/` | `staticr_mod_wasm.h` (the host API) and `staticr_<game>.h` (generated per game) |
| `tables/` | `events.tsv` (the mod events) and `fields/<game>.fields.tsv` (named fields) |
| `starters/` | what `staticr-mods new` writes, one per game and tier |
| `examples/` | the sample mods staticR ships (Tier 0 `.mod.toml`; Tier 1 under `wasm/`) |
| `docs/guide.md` | how to write, build, package and install a mod |
| `VERSION` | `<ABI>.<SDK revision>`: 1.1 |
| `SDK-MANIFEST.json` | every file's sha256 and the staticR commit it came from |

## Where it comes from

This repo is generated from the staticR source tree, which stays the single source: change the
SDK there, not here (a hand edit here is overwritten by the next release). Everything here is
MIT ([LICENSE](LICENSE)). The field tables hold struct offsets of the retail games, measured from
and checked against staticR's recompiled build of each game; they are numbers and names, not game
code or data. No ROM, game asset or decompiled code is in this repo, and none is needed to write a
mod: a mod runs only inside a staticR port of a game you own.

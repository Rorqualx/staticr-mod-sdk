# my-mod

A staticR Tier 1 (WASM) mod for oot, made by `staticr-mods new`.

| File | What |
|---|---|
| `my-mod.mod.toml` | the manifest: `id`, `name`, `game`, and `wasm`, the module's file name |
| `my-mod.c` | the mod |
| `staticr_mod_wasm.h` | the host's API (ABI 1): guest memory, log, pad, rumble, settings |
| `staticr_oot.h` | oot's events and fields (generated: `staticr-mods header oot`) |
| `build.sh` | the build without the SDK tool (`WASM_LD="rust-lld -flavor wasm"` for rust-lld) |

Build, check and package (needs clang, and wasm-ld or a stable Rust toolchain's rust-lld):

    staticr-mods build .     # my-mod.wasm, checked against the host's imports and exports
    staticr-mods pack .      # my-mod.staticrmod, checked by the launcher's own package reader

Install: the launcher's Mods page, Add..., takes the `.staticrmod`. A mod runs on the game thread
with a time budget per event; a mod that runs past it, or traps, is unloaded alone and the game
goes on. It keeps its state in its own globals, and can keep settings in `my-mod.json` (put a
default one next to the manifest and `pack` includes it).

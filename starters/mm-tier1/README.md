# my-mm-mod

A staticR Tier 1 (WASM) mod for mm, made by `staticr-mods new`.

| File | What |
|---|---|
| `my-mm-mod.mod.toml` | the manifest: `id`, `name`, `game`, and `wasm`, the module's file name |
| `my-mm-mod.c` | the mod |
| `staticr_mod_wasm.h` | the host's API (ABI 1): guest memory, log, pad, rumble, settings |
| `staticr_mm.h` | mm's events (generated: `staticr-mods header mm`) |
| `build.sh` | the build without the SDK tool (`WASM_LD="rust-lld -flavor wasm"` for rust-lld) |

Build, check and package (needs clang, and wasm-ld or a stable Rust toolchain's rust-lld):

    staticr-mods build .     # my-mm-mod.wasm, checked against the host's imports and exports
    staticr-mods pack .      # my-mm-mod.staticrmod, checked by the launcher's own package reader

Install: the launcher's Mods page, Add..., takes the `.staticrmod`. A mod runs on the game thread
with a time budget per event; a mod that runs past it, or traps, is unloaded alone and the game
goes on. It keeps its state in its own globals, and can keep settings in `my-mm-mod.json` (put a
default one next to the manifest and `pack` includes it).

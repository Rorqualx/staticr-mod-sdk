# Writing a staticR mod

How to write, build, package and install a staticR-native mod. The design and its measurements are
in staticR's `docs/design/mod-sdk.md`. The tool is `staticr-mods`: install it once with
`pip install .` in a clone of this repo (see README.md).

A staticR-native mod comes in one of two tiers:

| | Tier 0, rules | Tier 1, WASM |
|---|---|---|
| What you write | one `<id>.mod.toml` with `[[rule]]` tables | C compiled to a wasm32 module, plus a manifest |
| Can do | on an event, when conditions hold, set or add to named fields, rumble, log | anything the host API allows: guest memory, pad, rumble, log, a settings file, state kept between events |
| Games | those with a field table (`tables/fields/<game>.fields.tsv`: OoT today) | every game that fires mod events (OoT, MM) |
| Needs | nothing | clang with the wasm32 target, and wasm-ld or a stable Rust toolchain's rust-lld |

Both run on every platform the game runs on: Mac, Windows, Linux and the web.

## Start one

    staticr-mods new oot my-shock --tier 0
    staticr-mods new oot my-mod --tier 1 [--id my-mod] [--name "My mod"]

A Tier 0 starter is a single `my-shock.mod.toml`: D-pad Left gives Link the shock look. Its comments
list the events, buttons, operators and every field of the game, with each field's type and base.

A Tier 1 starter is a directory:

| File | What |
|---|---|
| `my-mod.mod.toml` | the manifest: `id`, `name`, `game`, and `wasm`, the module's file name |
| `my-mod.c` | the mod: logs Link's speed each time it changes (OoT), or each scene start (MM) |
| `staticr_mod_wasm.h` | the host API (ABI 1), a copy of `include/staticr_mod_wasm.h` |
| `staticr_<game>.h` | the game's events and fields, generated (`staticr-mods header <game>`) |
| `build.sh` | the build without the SDK tool |
| `README.md` | these steps |

## Write it

A Tier 1 mod exports one handler per event it wants; the host subscribes it to exactly those. With
the generated header:

```c
#include "staticr_oot.h"

STATICR_MOD_ABI()

STATICR_ON_PLAY_UPDATE(int32_t play, int32_t unused) {
    (void)unused;
    uint8_t shock;
    if (oot_player_shock_timer((uint32_t)play, &shock) == 0 && shock == 0)
        oot_player_shock_timer_set((uint32_t)play, 40u);
}
```

`staticr_<game>.h` gives, per event, `STATICR_EVENT_<NAME>` (its id) and `STATICR_ON_<NAME>` (the
handler), and per field `<game>_<field>_OFF` plus `<game>_<field>(root, &out)` and
`<game>_<field>_set(root, value)`. The root is `play` or `actor`, the event's argument. The helpers
follow pointer fields (`play` to `player` to `player.shock_timer`). They return 0, or -1 when a
read is refused or a pointer is null, and leave the output unchanged then. Pointer fields have no
setter. A field whose base is a pointer field also has `<game>_<field>_at(base, &out)` and
`_set_at(base, value)`, which take that pointer already read, so a handler that touches several of
the player's fields reads the player pointer once:

```c
uint32_t player;
if (oot_player((uint32_t)play, &player) == 0) {
    float speed;
    if (oot_player_speed_at(player, &speed) == 0 && speed > 5.0f)
        oot_player_shock_timer_set_at(player, 40u);
}
```

Everything else is in `staticr_mod_wasm.h`: `staticr_read_*`/`staticr_write_*` by N64
address, `staticr_get_pad`, `staticr_set_rumble`, `staticr_log`, `staticr_config_read`/`_write`
(the mod's own `<id>.json`, up to 64 KiB) and `staticr_event_count`.

A handler runs on the game thread, inside the game's own call, with a time budget (5 ms). A mod
that runs past it, or traps, is unloaded alone and the game goes on. A mod keeps its state in its
own globals.

The events are in `tables/events.tsv`; the Tier 0 rule keys are `on`, `pad_pressed`,
`pad_held`, `when`, `cooldown`, `set`, `add`, `rumble` and `log` (see the starter).

## Build, check, package

    staticr-mods build my-mod     # Tier 1: my-mod.wasm, checked against the host's ABI
    staticr-mods check my-shock   # Tier 0: the rules, checked as the engine checks them
    staticr-mods pack my-mod      # my-mod.staticrmod, read back by the launcher's reader

`build` supplies `staticr_mod_wasm.h` and `staticr_<game>.h` itself, and rewrites the mod's own
copies of them when they are out of date (they are generated: don't edit them), so a mod needs only
its `.c` and manifest.

`check` and `build` refuse what the game's host would refuse, with the reason: an unknown event,
field or button, a field the event does not carry, a write to a pointer, an import the host does not
have or has with another signature, a handler with the wrong signature, no `staticr_abi`, no
handler at all, a memory that starts over the 16 MiB cap. Two things are only notes: a module
older than its sources (build again), and an `on_*` export that names no event (the host ignores
it). The host still has the last word: it also checks the game, the ABI version and the module's
limits when it loads the mod.

A package holds the manifest, the module, and, when they are next to the manifest, `<id>.json`
(default settings, written only when the user has none) and `thumb.png` (a PNG).

## Install

On the launcher's Mods page, Add... takes the `.staticrmod`. A new mod is on by default; switch it
off, or move it in the load order, on the same page. The game loads it at its next start.

Without the launcher, copy the manifest and the module into `<config dir>/staticr_mods/`.

/* staticR Tier 1 mod ABI, version 1: the mod side (docs/design/mod-sdk.md M.2d D2).
 *
 * A Tier 1 mod is a wasm32 core module (no WASI) built from C with
 *     clang --target=wasm32 -O2 -nostdlib -fno-builtin -c mod.c
 *     wasm-ld --no-entry --export-dynamic mod.o -o mod.wasm
 * plus a manifest `<id>.mod.toml` holding `id`, `game`, `name` and `wasm = "<file>.wasm"` (no rules).
 * Desktop ports run it on wasmtime (host/staticr_mod_tier1.cpp), the web port on the browser's engine.
 *
 * What the mod exports:
 *   staticr_abi()                 -> the STATICR_MOD_WASM_ABI it was built for (STATICR_MOD_ABI below)
 *   staticr_init()                -> optional; non-zero refuses the mod (logged)
 *   on_<event>(a0, a1)            -> one per catalogue event it wants (configs/mods/events.tsv), e.g.
 *                                    on_play_update(play, 0), on_actor_init(actor, play)
 *   memory                        -> its linear memory (wasm-ld exports it by default)
 * The host subscribes only to the events a mod exports. Handlers run on the game thread, inside the
 * game's own call, with a time budget: a handler that runs past it, or traps, unloads that mod alone
 * (logged); the game goes on. Mods keep state in their own globals between events.
 *
 * What the host gives (import module "staticr"); every call is 0 on success, -1 on refusal, and leaves
 * outputs unchanged on refusal. Guest memory is addressed by N64 virtual address, as in
 * host/staticr_mod_api.h: KSEG0 over the guest window, natural alignment, N64 values (the host does
 * the byte swapping). */
#ifndef STATICR_MOD_WASM_H
#define STATICR_MOD_WASM_H

#include <stdint.h>

#define STATICR_MOD_WASM_ABI 1

#ifdef __wasm__
#define STATICR_IMPORT(name) __attribute__((import_module("staticr"), import_name(#name)))
#define STATICR_EXPORT(name) __attribute__((export_name(#name)))

STATICR_IMPORT(read_u8) int32_t staticr_read_u8(uint32_t vaddr, uint8_t* out);
STATICR_IMPORT(read_u16) int32_t staticr_read_u16(uint32_t vaddr, uint16_t* out);
STATICR_IMPORT(read_u32) int32_t staticr_read_u32(uint32_t vaddr, uint32_t* out);
STATICR_IMPORT(read_f32) int32_t staticr_read_f32(uint32_t vaddr, float* out);
STATICR_IMPORT(write_u8) int32_t staticr_write_u8(uint32_t vaddr, uint32_t value);
STATICR_IMPORT(write_u16) int32_t staticr_write_u16(uint32_t vaddr, uint32_t value);
STATICR_IMPORT(write_u32) int32_t staticr_write_u32(uint32_t vaddr, uint32_t value);
STATICR_IMPORT(write_f32) int32_t staticr_write_f32(uint32_t vaddr, float value);

enum { STATICR_LOG_INFO = 0, STATICR_LOG_WARN = 1, STATICR_LOG_ERROR = 2 };
/* "[staticr-mod] <mod id> <level>: <text>"; at most 512 bytes of text are kept. */
STATICR_IMPORT(log) void staticr_log(int32_t level, const char* text, uint32_t len);

/* The pad as the game received it on its last poll: 8 bytes, the layout of staticr_mod_pad. */
typedef struct staticr_pad {
    uint16_t buttons; /* A 0x8000, B 0x4000, Z 0x2000, START 0x1000, D-pad U/D/L/R 0x0800/0400/0200/0100,
                         L 0x0020, R 0x0010, C U/D/L/R 0x0008/0004/0002/0001 */
    int8_t stick_x, stick_y;
    uint32_t polls;
} staticr_pad;
STATICR_IMPORT(get_pad) int32_t staticr_get_pad(uint32_t port, staticr_pad* out);
/* 1 while the game's own motor on `port` is on, 0 off, -1 bad port */
STATICR_IMPORT(get_rumble) int32_t staticr_get_rumble(uint32_t port);
/* This mod's rumble wish; the motor runs while the game or any mod wants it. Released on unload. */
STATICR_IMPORT(set_rumble) int32_t staticr_set_rumble(uint32_t port, int32_t on);
/* How many times the event fired (catalogue id) */
STATICR_IMPORT(event_count) int64_t staticr_event_count(uint32_t event_id);
/* The mod's own settings file, <config dir>/staticr_mods/<id>.json (at most 64 KiB). config_read
 * returns the byte count copied into buf (the whole file must fit in cap), 0 when there is no file,
 * -1 on refusal; config_write replaces the file and returns 0 or -1. */
STATICR_IMPORT(config_read) int32_t staticr_config_read(void* buf, uint32_t cap);
STATICR_IMPORT(config_write) int32_t staticr_config_write(const void* buf, uint32_t len);

#define STATICR_MOD_ABI() STATICR_EXPORT(staticr_abi) int32_t staticr_abi(void) { return STATICR_MOD_WASM_ABI; }
#endif /* __wasm__ */

#endif

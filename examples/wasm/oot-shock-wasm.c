/* staticR Tier 1 sample mod (docs/design/mod-sdk.md M.2d D3): the Tier 0 shock-flash sample
 * (../oot-shock-flash.mod.toml) written in C and run as WASM. D-pad Left: Link flashes red with
 * lightning over him, with a short rumble. Same fields, edge, cooldown and rumble as the Tier 0 rule.
 *
 * Build with the SDK (M.2f): copy this file and its manifest into a directory, then
 * `staticr-mods build .` (it supplies staticr_mod_wasm.h and the generated staticr_oot.h) and
 * `staticr-mods pack .`; the launcher's Mods page installs the .staticrmod. */
#include "staticr_oot.h" /* the SDK's generated OoT events and fields; includes staticr_mod_wasm.h */

STATICR_MOD_ABI()

#define DLEFT 0x0200u
#define COOLDOWN 40u                /* play_update events skipped after firing */
#define RUMBLE 10u                  /* play_update events of rumble */

static uint32_t was_down, cooldown_left, rumble_left;

STATICR_ON_PLAY_UPDATE(int32_t play, int32_t unused) {
    (void)unused;
    if (rumble_left > 0u && --rumble_left == 0u) staticr_set_rumble(0, 0);
    staticr_pad pad;
    if (staticr_get_pad(0, &pad) != 0) return;
    const uint32_t down = (pad.buttons & DLEFT) == DLEFT;
    const uint32_t pressed = down && !was_down;
    was_down = down;
    if (cooldown_left > 0u) {
        cooldown_left--;
        return;
    }
    if (!pressed) return;
    /* play -> player once, then read both targets first, so a null or bad pointer changes nothing */
    uint32_t player;
    uint8_t shock;
    int8_t inv;
    if (oot_player((uint32_t)play, &player) != 0 || oot_player_shock_timer_at(player, &shock) != 0 ||
        oot_player_invincibility_timer_at(player, &inv) != 0)
        return;
    oot_player_invincibility_timer_set_at(player, 20);
    oot_player_shock_timer_set_at(player, 40u);
    cooldown_left = COOLDOWN;
    rumble_left = RUMBLE;
    staticr_set_rumble(0, 1);
    staticr_log(STATICR_LOG_INFO, "shock flash", 11);
}

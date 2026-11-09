/* my-mm-mod: a staticR Tier 1 (WASM) mod for mm, made by `staticr-mods new`.
 * Build: staticr-mods build .   Package: staticr-mods pack .   (README.md says more)
 * staticr_mod_wasm.h is the host's API, staticr_mm.h this game's events (mm has no field
 * table yet, so guest memory is read by address with staticr_read_u8/u16/u32/f32). */
#include "staticr_mm.h"

STATICR_MOD_ABI()

STATICR_EXPORT(staticr_init) int32_t staticr_init(void) {
    staticr_log(STATICR_LOG_INFO, "loaded", 6);
    return 0; /* non-zero refuses the mod */
}

/* a gameplay scene starts */
STATICR_ON_PLAY_INIT(int32_t play, int32_t unused) {
    (void)play; (void)unused;
    staticr_log(STATICR_LOG_INFO, "a scene starts", 14);
}

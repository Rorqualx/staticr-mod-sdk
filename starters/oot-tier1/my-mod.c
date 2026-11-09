/* my-mod: a staticR Tier 1 (WASM) mod for oot, made by `staticr-mods new`.
 * Build: staticr-mods build .   Package: staticr-mods pack .   (README.md says more)
 * staticr_mod_wasm.h is the host's API, staticr_oot.h this game's events and fields. */
#include "staticr_oot.h"

STATICR_MOD_ABI()

static int32_t last = -1; /* the last speed seen, in whole units; -1 = none yet */
static char text[64];
static uint32_t len;

static void put(const char* s) {
    while (*s && len < sizeof text) text[len++] = *s++;
}

static void put_int(int32_t v) {
    char d[12];
    int n = 0;
    uint32_t u = v < 0 ? 0u - (uint32_t)v : (uint32_t)v;
    if (v < 0) put("-");
    do { d[n++] = (char)('0' + u % 10u); u /= 10u; } while (u != 0u && n < 12);
    while (n > 0 && len < sizeof text) text[len++] = d[--n];
}

STATICR_EXPORT(staticr_init) int32_t staticr_init(void) {
    staticr_log(STATICR_LOG_INFO, "loaded", 6);
    return 0; /* non-zero refuses the mod */
}

/* once per gameplay update (about 20 a second): log Link's speed when it changes */
STATICR_ON_PLAY_UPDATE(int32_t play, int32_t unused) {
    (void)unused;
    float speed = 0.0f;
    if (oot_player_speed((uint32_t)play, &speed) != 0) return;
    const int32_t now = (int32_t)speed;
    if (now == last) return;
    last = now;
    len = 0;
    put("speed ");
    put_int(now);
    staticr_log(STATICR_LOG_INFO, text, len);
}

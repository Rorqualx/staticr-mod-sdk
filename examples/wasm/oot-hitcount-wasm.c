/* staticR Tier 1 sample mod (docs/design/mod-sdk.md M.2d D5): a mod that keeps state, which a Tier 0
 * rule cannot. Each time Link loses health, he flashes red with lightning over him and a short
 * rumble, and the hit is counted. The count lives in the mod's own settings file
 * (<config dir>/staticr_mods/oot-hitcount-wasm.json, {"hits":N}), so it carries over to the next
 * session; every 10th hit is announced in the log.
 *
 * State it keeps: the health it saw last (between play_update events), the count (in memory and in
 * the file), the flash's rumble countdown, and a merge window: health that keeps going down within
 * about a second of a hit (a multi-step loss, a heat or cold-water drain) is the same hit, so the
 * count and the file change once. A scene start (play_init) forgets the last health, so a refill or a
 * file load is never taken for a hit.
 *
 * Build with the SDK (M.2f): copy this file and its manifest into a directory, then
 * `staticr-mods build .` (it supplies staticr_mod_wasm.h and the generated staticr_oot.h) and
 * `staticr-mods pack .`; the launcher's Mods page installs the .staticrmod. */
#include "staticr_oot.h" /* the SDK's generated OoT events and fields; includes staticr_mod_wasm.h */

STATICR_MOD_ABI()

/* OoT NTSC 1.2: gSaveContext (configs/transcribed/symbols/oot-ntsc-1.2.symbols.txt), its
 * healthCapacity (+0x2E) and health (+0x30), s16 in sixteenths of a heart */
#define SAVE_CONTEXT 0x8011AC80u
#define HEALTH_CAPACITY (SAVE_CONTEXT + 0x2Eu)
#define HEALTH (SAVE_CONTEXT + 0x30u)
#define RUMBLE 10u                  /* play_update events of rumble */
#define MERGE 20u                   /* play_update events (~1 s) in which further loss is the same hit */

static int32_t last_health = -1;  /* -1: not seen since the scene started */
static uint32_t hits, rumble_left, merge_left;
static char text[96];
static uint32_t len;

static void put(const char* s) {
    while (*s && len < sizeof text) text[len++] = *s++;
}

static void put_u32(uint32_t v) {
    char d[10];
    int n = 0;
    do { d[n++] = (char)('0' + v % 10u); v /= 10u; } while (v != 0u && n < 10);
    while (n > 0 && len < sizeof text) text[len++] = d[--n];
}

/* {"hits":N}; anything else (no file, another shape) starts from 0 */
static uint32_t parse_hits(const char* s, int32_t n) {
    static const char key[] = "\"hits\"";
    for (int32_t i = 0; i + 6 <= n; i++) {
        int32_t k = 0;
        while (k < 6 && s[i + k] == key[k]) k++;
        if (k < 6) continue;
        int32_t j = i + 6;
        while (j < n && (s[j] == ' ' || s[j] == ':')) j++;
        uint32_t v = 0;
        int32_t digits = 0;
        while (j < n && s[j] >= '0' && s[j] <= '9' && digits < 9) { v = v * 10u + (uint32_t)(s[j++] - '0'); digits++; }
        return digits > 0 ? v : 0u;
    }
    return 0u;
}

static void save(void) {
    len = 0;
    put("{\"hits\":");
    put_u32(hits);
    put("}\n");
    if (staticr_config_write(text, len) != 0) staticr_log(STATICR_LOG_WARN, "could not save the hit count", 28);
}

STATICR_EXPORT(staticr_init) int32_t staticr_init(void) {
    char buf[64];
    const int32_t n = staticr_config_read(buf, sizeof buf);
    hits = n > 0 ? parse_hits(buf, n) : 0u;
    len = 0;
    put("hits so far: ");
    put_u32(hits);
    staticr_log(STATICR_LOG_INFO, text, len);
    return 0;
}

STATICR_ON_PLAY_INIT(int32_t play, int32_t unused) {
    (void)play; (void)unused;
    last_health = -1;
}

static void flash(uint32_t play) {
    /* play -> player once, then read both targets first, so a null or bad pointer changes nothing */
    uint32_t player;
    uint8_t shock;
    int8_t inv;
    if (oot_player(play, &player) != 0 || oot_player_shock_timer_at(player, &shock) != 0 ||
        oot_player_invincibility_timer_at(player, &inv) != 0)
        return;
    /* the red flash: only when the game has not set its own invincibility for this hit */
    if (inv == 0) oot_player_invincibility_timer_set_at(player, 20);
    oot_player_shock_timer_set_at(player, 40u);
    rumble_left = RUMBLE;
    staticr_set_rumble(0, 1);
}

STATICR_ON_PLAY_UPDATE(int32_t play, int32_t unused) {
    (void)unused;
    if (rumble_left > 0u && --rumble_left == 0u) staticr_set_rumble(0, 0);
    if (merge_left > 0u) merge_left--;
    uint16_t cap = 0, hp = 0;
    if (staticr_read_u16(HEALTH_CAPACITY, &cap) != 0 || staticr_read_u16(HEALTH, &hp) != 0) return;
    const int32_t health = (int16_t)hp;
    /* a save that is loaded: 3 to 20 hearts, health within them */
    if ((int16_t)cap < 0x30 || (int16_t)cap > 0x140 || health < 0 || health > (int16_t)cap) {
        last_health = -1;
        return;
    }
    if (last_health >= 0 && health < last_health && merge_left > 0u) {
        merge_left = MERGE;  /* still the same hit */
    } else if (last_health >= 0 && health < last_health) {
        merge_left = MERGE;
        hits++;
        save();
        flash((uint32_t)play);
        len = 0;
        put("hit ");
        put_u32(hits);
        put(" (health ");
        put_u32((uint32_t)last_health);
        put(" -> ");
        put_u32((uint32_t)health);
        put(")");
        if (hits % 10u == 0u) put(", another ten");
        staticr_log(STATICR_LOG_INFO, text, len);
    }
    last_health = health;
}

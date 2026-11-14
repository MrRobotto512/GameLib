/* Game Shop on the PS5: the store as an app of its own, drawn with SDL like FLEX's player.
 * It talks to the Game Shop running on the PC (the address the PC wrote to url.txt beside the
 * eboot when it installed the app) through the same API the web store uses.
 *
 * Shelf: every console is a 3D shelf of boxes, the selected one facing you and the rest standing
 * spine-out on either side, laid out like Boxshelf's (https://github.com/sassiestrhihno/boxshelf-source,
 * GPL-3.0; see draw_shelf). Left/Right browse, Up/Down or L1/R1 change console (first "Recently
 * added", last "Downloads"), Square flips the box, Cross opens a game, Triangle reloads, holding
 * Circle closes the app. Game page: Cross sends it to the PS5, Circle goes back. Downloads: Cross
 * cancels a copy (or tries a failed one again). */

#include <SDL.h>
#include <SDL_image.h>
#include <SDL_ttf.h>
#include <math.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "app.h"
#include "cJSON.h"
#include "covers.h"
#include "net.h"

#define URL_FILE APP_HOME "/url.txt"
#define SCREEN_W 1920
#define SCREEN_H 1080

/* page margins at 1080p (the game page and Downloads) */
#define MARGIN_X 75
#define GRID_TOP 190
#define THUMB_W 300  /* cover width asked of the PC for the shelf */
#define DETAIL_W 640 /* and for a game page */
#define HOLD_EXIT_MS 1000

static const SDL_Color WHITE = {255, 255, 255, 255}, DIM = {160, 164, 184, 255}, GOLD = {245, 196, 81, 255},
                       BG = {11, 11, 20, 255}, PANEL = {24, 24, 38, 255}, BAR = {16, 16, 28, 255},
                       TRACK = {50, 50, 70, 255}, RED = {235, 90, 90, 255}, GREEN = {110, 210, 140, 255};

typedef struct {
    char id[128], title[192], sub[160], sys[32];
    SDL_Color color;
    int art;
    long key;
    SDL_Texture *tex, *spine, *back; /* the cover, the title as the spine prints it, the back cover */
    int tw, th, asked, sw, sh, back_state; /* back_state: 0 not asked, 1 asked, 2 there is none */
} Item;

enum { TAB_RECENT, TAB_SYSTEM, TAB_DOWNLOADS };

typedef struct {
    int kind;
    char id[32], name[64];
    SDL_Color color;
    int count;
    float ratio; /* the boxes' width / height */
    Item *items;
    int n, loaded, sel, first;
} Tab;

typedef struct {
    long id;
    char title[192], state[16], step[96], error[256];
    double done, total;
} Job;

static char g_host[128], g_url[256];
static int g_port;
static char g_name[64];
static TTF_Font *g_big, *g_font, *g_small, *g_bold;
static Tab *g_tabs;
static int g_ntabs, g_tab;
static long g_gen = 1; /* bumped when lists are reloaded, so late covers for old lists are dropped */
static Job *g_jobs;
static int g_njobs, g_jobsel;
static char g_toast[256];
static Uint32 g_toast_until;

/* ---------- small helpers ---------- */

static SDL_Color parse_color(const char *hex) {
    unsigned r = 0x88, g = 0x88, b = 0x88;
    if (hex && hex[0] == '#') sscanf(hex + 1, "%02x%02x%02x", &r, &g, &b);
    return (SDL_Color){r, g, b, 255};
}

static SDL_Color mix(SDL_Color a, SDL_Color b, int pct_a) {
    return (SDL_Color){(a.r * pct_a + b.r * (100 - pct_a)) / 100, (a.g * pct_a + b.g * (100 - pct_a)) / 100,
                       (a.b * pct_a + b.b * (100 - pct_a)) / 100, 255};
}

static const char *jstr(cJSON *o, const char *key) {
    cJSON *v = cJSON_GetObjectItem(o, key);
    return cJSON_IsString(v) ? v->valuestring : "";
}

static double jnum(cJSON *o, const char *key) {
    cJSON *v = cJSON_GetObjectItem(o, key);
    return cJSON_IsNumber(v) ? v->valuedouble : 0;
}

static void human_size(double bytes, char *out, size_t n) {
    const char *u[] = {"B", "KB", "MB", "GB", "TB"};
    int i = 0;
    while (bytes >= 1024 && i < 4) bytes /= 1024, i++;
    snprintf(out, n, i ? "%.2f %s" : "%.0f %s", bytes, u[i]);
}

static void toast(const char *msg) {
    snprintf(g_toast, sizeof g_toast, "%s", msg);
    g_toast_until = SDL_GetTicks() + 4000;
}

/* error_text: Game Shop's reason for the last failed request, or what to check. */
static const char *error_text(void) {
    static char msg[512];
    cJSON *j = gs_error[0] ? cJSON_Parse(gs_error) : NULL;
    const char *d = j ? jstr(j, "detail") : "";
    snprintf(msg, sizeof msg, "%s", d[0] ? d : gs_error[0] ? "Game Shop said no." : "Game Shop on the PC is not answering.");
    cJSON_Delete(j);
    return msg;
}

/* read_url takes the PC's address from url.txt ("http://192.168.1.20:8750/"). */
static int read_url(void) {
    FILE *f = fopen(URL_FILE, "r");
    if (!f) return -1;
    int ok = fgets(g_url, sizeof g_url, f) != NULL;
    fclose(f);
    if (!ok) return -1;
    g_url[strcspn(g_url, "\r\n")] = 0;
    const char *h = strstr(g_url, "://");
    h = h ? h + 3 : g_url;
    size_t hl = strcspn(h, ":/");
    if (!hl || hl >= sizeof g_host) return -1;
    memcpy(g_host, h, hl);
    g_host[hl] = 0;
    g_port = h[hl] == ':' ? atoi(h + hl + 1) : 80;
    return g_port > 0 ? 0 : -1;
}

/* ---------- loading ---------- */

static void parse_item(cJSON *o, Item *it) {
    memset(it, 0, sizeof *it);
    snprintf(it->id, sizeof it->id, "%s", jstr(o, "id"));
    snprintf(it->title, sizeof it->title, "%s", jstr(o, "title"));
    snprintf(it->sys, sizeof it->sys, "%s", jstr(o, "system"));
    it->color = parse_color(jstr(o, "color"));
    it->art = cJSON_IsTrue(cJSON_GetObjectItem(o, "art"));
    char size[32];
    human_size(jnum(o, "size"), size, sizeof size);
    int year = (int)jnum(cJSON_GetObjectItem(o, "facts"), "year");
    if (year) snprintf(it->sub, sizeof it->sub, "%s · %d · %s", jstr(o, "system_name"), year, size);
    else snprintf(it->sub, sizeof it->sub, "%s · %s", jstr(o, "system_name"), size);
}

static void free_items(Tab *t) {
    for (int i = 0; i < t->n; i++) {
        if (t->items[i].tex) SDL_DestroyTexture(t->items[i].tex);
        if (t->items[i].spine) SDL_DestroyTexture(t->items[i].spine);
        if (t->items[i].back) SDL_DestroyTexture(t->items[i].back);
    }
    free(t->items);
    t->items = NULL;
    t->n = t->loaded = t->sel = t->first = 0;
}

/* width / height of each kind of box (systems.toml's case names), as the web shelf has them */
static float case_ratio(const char *c) {
    static const struct { const char *name; float r; } R[] = {
        {"dvd", .70f}, {"bd", .80f}, {"mini-dvd", .72f}, {"umd", .58f}, {"switch", .62f}, {"jewel", 1.0f},
        {"cart-snes", 1.40f}, {"cart-n64", 1.40f}, {"cart-gb", .82f}, {"cart-lynx", .82f}, {"cart-ds", .88f},
        {"cart-genesis", .72f}, {"cart-sms", .72f}, {"cart-", .72f}, {"generic", .75f}};
    for (size_t i = 0; i < sizeof R / sizeof R[0]; i++)
        if (!strncmp(c, R[i].name, strlen(R[i].name))) return R[i].r;
    return .72f;
}

static int load_state(void) {
    char *body = gs_get(g_host, g_port, "/api/state");
    cJSON *j = body ? cJSON_Parse(body) : NULL;
    free(body);
    if (!j) return -1;
    snprintf(g_name, sizeof g_name, "%s", jstr(j, "name"));
    for (int i = 0; i < g_ntabs; i++) free_items(&g_tabs[i]);
    free(g_tabs);
    cJSON *systems = cJSON_GetObjectItem(j, "systems"), *s;
    g_tabs = calloc(cJSON_GetArraySize(systems) + 2, sizeof *g_tabs);
    g_ntabs = 0;
    g_tabs[g_ntabs++] = (Tab){.kind = TAB_RECENT, .name = "Recently added", .color = GOLD, .ratio = .72f};
    cJSON_ArrayForEach(s, systems) {
        int count = (int)jnum(s, "count");
        if (!count) continue;
        Tab *t = &g_tabs[g_ntabs++];
        t->kind = TAB_SYSTEM;
        t->count = count;
        snprintf(t->id, sizeof t->id, "%s", jstr(s, "id"));
        snprintf(t->name, sizeof t->name, "%s", jstr(s, "name"));
        t->color = parse_color(jstr(s, "color"));
        t->ratio = case_ratio(jstr(s, "case"));
    }
    g_tabs[g_ntabs++] = (Tab){.kind = TAB_DOWNLOADS, .name = "Downloads", .color = GOLD, .ratio = .72f};
    if (g_tab >= g_ntabs) g_tab = 0;
    cJSON_Delete(j);
    g_gen++;
    covers_forget();
    return 0;
}

static void load_tab(Tab *t) {
    if (t->loaded || t->kind == TAB_DOWNLOADS) return;
    char path[128];
    if (t->kind == TAB_RECENT) snprintf(path, sizeof path, "/api/discover");
    else snprintf(path, sizeof path, "/api/items?system=%s", t->id);
    char *body = gs_get(g_host, g_port, path);
    cJSON *j = body ? cJSON_Parse(body) : NULL;
    free(body);
    if (!j) {
        toast(error_text());
        return;
    }
    cJSON *list = t->kind == TAB_RECENT ? cJSON_GetObjectItem(j, "recent") : j, *o;
    t->n = cJSON_GetArraySize(list);
    t->items = calloc(t->n ? t->n : 1, sizeof *t->items);
    int i = 0;
    cJSON_ArrayForEach(o, list) {
        parse_item(o, &t->items[i]);
        t->items[i].key = g_gen * 1000000000L + (long)(t - g_tabs) * 100000L + i;
        i++;
    }
    t->loaded = 1;
    t->sel = t->first = 0;
    cJSON_Delete(j);
}

static void load_jobs(void) {
    char *body = gs_get(g_host, g_port, "/api/jobs");
    cJSON *j = body ? cJSON_Parse(body) : NULL, *o;
    free(body);
    if (!j) return;
    free(g_jobs);
    g_njobs = cJSON_GetArraySize(j);
    g_jobs = calloc(g_njobs ? g_njobs : 1, sizeof *g_jobs);
    int i = 0;
    cJSON_ArrayForEach(o, j) {
        Job *b = &g_jobs[i++];
        b->id = (long)jnum(o, "id");
        snprintf(b->title, sizeof b->title, "%s", jstr(o, "title"));
        snprintf(b->state, sizeof b->state, "%s", jstr(o, "state"));
        snprintf(b->step, sizeof b->step, "%s", jstr(o, "step"));
        snprintf(b->error, sizeof b->error, "%s", jstr(o, "error"));
        b->done = jnum(o, "done");
        b->total = jnum(o, "total");
    }
    if (g_jobsel >= g_njobs) g_jobsel = g_njobs ? g_njobs - 1 : 0;
    cJSON_Delete(j);
}

/* take_covers turns decoded covers into textures for the items that asked for them. */
static int take_covers(SDL_Renderer *ren, Item *detail) {
    long key;
    SDL_Surface *s;
    int any = 0;
    while (covers_take(&key, &s)) {
        Item *it = NULL;
        if (key < -1000000000L) { /* a back cover: its item's key, negated */
            long k = -key, tab = (k / 100000L) % 10000L, idx = k % 100000L;
            if (k / 1000000000L == g_gen && tab < g_ntabs && idx < g_tabs[tab].n) {
                Item *b = &g_tabs[tab].items[idx];
                if (s && !b->back && (b->back = SDL_CreateTextureFromSurface(ren, s))) SDL_SetTextureBlendMode(b->back, SDL_BLENDMODE_NONE);
                if (!s) b->back_state = 2;
                any = 1;
            }
            if (s) SDL_FreeSurface(s);
            continue;
        }
        if (detail && key == detail->key) it = detail;
        long tab = (key / 100000L) % 10000L, idx = key % 100000L;
        if (!it && key > 0 && key / 1000000000L == g_gen && tab < g_ntabs && idx < g_tabs[tab].n) it = &g_tabs[tab].items[idx];
        if (it && s && !it->tex) {
            it->tex = SDL_CreateTextureFromSurface(ren, s);
            if (it->tex) SDL_SetTextureBlendMode(it->tex, SDL_BLENDMODE_NONE); /* opaque: far cheaper to draw */
            it->tw = s->w;
            it->th = s->h;
            any = 1;
        } else if (it && !s) {
            it->art = 0;
            any = 1;
        }
        if (s) SDL_FreeSurface(s);
    }
    return any;
}

#define SHELF_WIN 7 /* boxes drawn on each side of the selection */

/* want_visible asks for the covers on the shelf, the selected one last (covers_want serves the
 * newest first), and lets go of textures far from it so memory stays flat however far one goes. */
static void want_visible(Tab *t) {
    int lo = t->sel - SHELF_WIN - 2, hi = t->sel + SHELF_WIN + 2;
    for (int i = 0; i < t->n; i++) {
        Item *it = &t->items[i];
        if (i >= lo - 10 && i <= hi + 10) continue;
        if (it->tex) SDL_DestroyTexture(it->tex), it->tex = NULL, it->asked = 0;
        if (it->spine) SDL_DestroyTexture(it->spine), it->spine = NULL;
        if (it->back) SDL_DestroyTexture(it->back), it->back = NULL, it->back_state = 0;
    }
    for (int d = hi - t->sel; d >= 0; d--)
        for (int k = 0; k < 2; k++) {
            int i = t->sel + (k ? -d : d);
            if (i < 0 || i >= t->n || (k && !d)) continue;
            Item *it = &t->items[i];
            if (it->art && !it->tex && !it->asked) {
                covers_want(it->key, it->id, THUMB_W, 0);
                it->asked = 1;
            }
        }
}

/* ---------- drawing ---------- */

static void fill(SDL_Renderer *ren, SDL_Rect r, SDL_Color c) {
    SDL_SetRenderDrawColor(ren, c.r, c.g, c.b, c.a);
    SDL_RenderFillRect(ren, &r);
}

static void frame(SDL_Renderer *ren, SDL_Rect r, int w, SDL_Color c) {
    fill(ren, (SDL_Rect){r.x - w, r.y - w, r.w + 2 * w, w}, c);
    fill(ren, (SDL_Rect){r.x - w, r.y + r.h, r.w + 2 * w, w}, c);
    fill(ren, (SDL_Rect){r.x - w, r.y, w, r.h}, c);
    fill(ren, (SDL_Rect){r.x + r.w, r.y, w, r.h}, c);
}

/* Strings drawn lately, kept as textures: the renderer is software only, and rendering every label
 * from the font on every frame was most of a frame's time while the shelf slides. */
#define TEXT_CACHE 96
static struct {
    TTF_Font *font;
    SDL_Color c;
    char text[200];
    SDL_Texture *tex;
    int w, h;
    Uint32 used;
} g_text[TEXT_CACHE];

static int draw_text(SDL_Renderer *ren, TTF_Font *font, const char *text, int x, int y, SDL_Color c) {
    if (!text[0]) return 0;
    int slot = -1, old = 0;
    for (int i = 0; i < TEXT_CACHE; i++) {
        if (g_text[i].tex && g_text[i].font == font && !memcmp(&g_text[i].c, &c, sizeof c) && !strcmp(g_text[i].text, text)) {
            slot = i;
            break;
        }
        if (g_text[i].used < g_text[old].used) old = i;
    }
    if (slot < 0 || strlen(text) >= sizeof g_text[0].text) {
        SDL_Surface *s = TTF_RenderUTF8_Blended(font, text, c);
        if (!s) return 0;
        SDL_Texture *t = SDL_CreateTextureFromSurface(ren, s);
        int w = s->w, h = s->h;
        SDL_FreeSurface(s);
        if (!t) return 0;
        if (strlen(text) >= sizeof g_text[0].text) { /* too long to keep: draw it once */
            SDL_RenderCopy(ren, t, NULL, &(SDL_Rect){x, y, w, h});
            SDL_DestroyTexture(t);
            return w;
        }
        slot = old;
        if (g_text[slot].tex) SDL_DestroyTexture(g_text[slot].tex);
        g_text[slot].font = font, g_text[slot].c = c, g_text[slot].tex = t, g_text[slot].w = w, g_text[slot].h = h;
        snprintf(g_text[slot].text, sizeof g_text[slot].text, "%s", text);
    }
    g_text[slot].used = SDL_GetTicks();
    SDL_RenderCopy(ren, g_text[slot].tex, NULL, &(SDL_Rect){x, y, g_text[slot].w, g_text[slot].h});
    return g_text[slot].w;
}

/* draw_text_fit truncates with an ellipsis so the text fits in max_w pixels. */
static int draw_text_fit(SDL_Renderer *ren, TTF_Font *font, const char *text, int x, int y, int max_w, SDL_Color c) {
    int w = 0, h = 0;
    if (TTF_SizeUTF8(font, text, &w, &h) == 0 && w <= max_w) return draw_text(ren, font, text, x, y, c);
    char buf[256];
    size_t len = strlen(text);
    if (len >= sizeof buf - 4) len = sizeof buf - 4;
    while (len > 0) {
        do len--; while (len > 0 && (text[len] & 0xC0) == 0x80); /* back up to a UTF-8 character boundary */
        memcpy(buf, text, len);
        strcpy(buf + len, "\xE2\x80\xA6");
        if (TTF_SizeUTF8(font, buf, &w, &h) == 0 && w <= max_w) break;
    }
    return draw_text(ren, font, buf, x, y, c);
}

static int draw_wrapped(SDL_Renderer *ren, TTF_Font *font, const char *text, SDL_Rect box, SDL_Color c) {
    if (!text[0]) return 0;
    SDL_Surface *s = TTF_RenderUTF8_Blended_Wrapped(font, text, c, box.w);
    if (!s) return 0;
    int h = s->h;
    SDL_Texture *t = SDL_CreateTextureFromSurface(ren, s);
    if (t) {
        SDL_RenderSetClipRect(ren, &box);
        SDL_RenderCopy(ren, t, NULL, &(SDL_Rect){box.x, box.y, s->w, s->h});
        SDL_RenderSetClipRect(ren, NULL);
        SDL_DestroyTexture(t);
    }
    SDL_FreeSurface(s);
    return h < box.h ? h : box.h;
}

/* draw_cover fits a cover into box keeping its shape; without one, the title on the console's
 * colour, like the web store's generated cards. */
static void draw_cover(SDL_Renderer *ren, Item *it, SDL_Rect box, TTF_Font *font) {
    if (it->tex) {
        double sc = (double)box.w / it->tw;
        if (it->th * sc > box.h) sc = (double)box.h / it->th;
        int w = (int)(it->tw * sc), h = (int)(it->th * sc);
        SDL_RenderCopy(ren, it->tex, NULL, &(SDL_Rect){box.x + (box.w - w) / 2, box.y + (box.h - h) / 2, w, h});
        return;
    }
    SDL_Rect card = {box.x + box.w / 8, box.y, box.w * 3 / 4, box.h};
    fill(ren, card, mix(it->color, BG, 70));
    draw_wrapped(ren, font, it->title, (SDL_Rect){card.x + 14, card.y + card.h / 2, card.w - 28, card.h / 2 - 10}, WHITE);
}

static void draw_header(SDL_Renderer *ren) {
    fill(ren, (SDL_Rect){0, 0, SCREEN_W, 150}, BAR);
    fill(ren, (SDL_Rect){MARGIN_X, 46, 18, 46}, GOLD);
    int x = MARGIN_X + 34;
    x += draw_text(ren, g_bold, "Game Shop", x, 40, WHITE) + 16;
    draw_text(ren, g_small, g_name, x, 62, DIM);
    /* the console tabs, the current one in the middle */
    Tab *t = &g_tabs[g_tab];
    char count[64];
    if (t->kind == TAB_SYSTEM) snprintf(count, sizeof count, "%d games", t->count);
    else if (t->kind == TAB_RECENT) snprintf(count, sizeof count, "%d games", t->n);
    else snprintf(count, sizeof count, "%d copies", g_njobs);
    int w = 0, h = 0;
    TTF_SizeUTF8(g_font, t->name, &w, &h);
    int cx = SCREEN_W / 2 + 120;
    fill(ren, (SDL_Rect){cx - w / 2 - 24, 36, w + 48, 60}, mix(t->color, BG, 45));
    draw_text(ren, g_font, t->name, cx - w / 2, 44, WHITE);
    if (g_tab > 0) {
        draw_text(ren, g_small, "L1", cx - w / 2 - 260, 54, GOLD);
        draw_text_fit(ren, g_small, g_tabs[g_tab - 1].name, cx - w / 2 - 220, 54, 180, DIM);
    }
    if (g_tab + 1 < g_ntabs) {
        draw_text_fit(ren, g_small, g_tabs[g_tab + 1].name, cx + w / 2 + 40, 54, 180, DIM);
        draw_text(ren, g_small, "R1", cx + w / 2 + 230, 54, GOLD);
    }
    TTF_SizeUTF8(g_small, count, &w, &h);
    draw_text(ren, g_small, count, SCREEN_W - MARGIN_X - w, 54, DIM);
}

static void draw_legend(SDL_Renderer *ren, const char *text) {
    fill(ren, (SDL_Rect){0, SCREEN_H - 70, SCREEN_W, 70}, BAR);
    draw_text(ren, g_small, text, MARGIN_X, SCREEN_H - 52, DIM);
    if (g_toast[0] && SDL_GetTicks() < g_toast_until) {
        int w = 0, h = 0;
        TTF_SizeUTF8(g_small, g_toast, &w, &h);
        if (w > SCREEN_W - 2 * MARGIN_X - 40) w = SCREEN_W - 2 * MARGIN_X - 40;
        SDL_Rect r = {SCREEN_W - MARGIN_X - w - 40, SCREEN_H - 150, w + 40, 60};
        fill(ren, r, PANEL);
        frame(ren, r, 2, GOLD);
        draw_text_fit(ren, g_small, g_toast, r.x + 20, r.y + 16, w, WHITE);
    }
}

/* ---------- the shelf ----------
 * Laid out like Boxshelf's coverflow (its renderer/app.js, place()): the selected box sits forward,
 * turned 22 degrees so its spine shows; the rest recede in a tight stack turned 60 degrees, so their
 * spines read like a shelf. Each box is a real 3D box (front, back and both spines) seen through a
 * perspective of 1800 px, drawn with SDL_RenderGeometry, since this SDL has only the software renderer. */
#define CAM_P 1800.0f
#define SHELF_Y 454 /* where the selected box's centre sits: 42% down the screen */
static float g_pos;            /* the shelf's position, easing towards the selection */
static float g_flip, g_flip_to; /* the selected box's extra turn, 0 or 180 */
static SDL_Texture *g_glow;    /* a soft round light, for the halo and the floor */

typedef struct { float x, y; } P2;
typedef struct { float x, y, z; } P3;

static float tab_ratio_of(const Item *it, const Tab *t) {
    if (t->kind == TAB_SYSTEM) return t->ratio;
    for (int i = 0; i < g_ntabs; i++)
        if (g_tabs[i].kind == TAB_SYSTEM && !strcmp(g_tabs[i].id, it->sys)) return g_tabs[i].ratio;
    return .72f;
}

/* where a point of a box lands on screen: the box turned by ry (degrees, CSS rotateY) about its
 * upright axis and moved to (bx, bz); z comes towards the viewer */
static P2 project(P3 p, float bx, float bz, float ry) {
    float a = ry * (float)M_PI / 180.0f, c = cosf(a), sn = sinf(a);
    float x = p.x * c + p.z * sn + bx, z = -p.x * sn + p.z * c + bz;
    float k = CAM_P / (CAM_P - z);
    return (P2){SCREEN_W / 2 + x * k, SHELF_Y + p.y * k};
}

/* a point (s across, t down) of a face given by its corners tl, tr, bl */
static P3 on_face(const P3 *f, float u, float v) {
    return (P3){f[0].x + (f[1].x - f[0].x) * u + (f[3].x - f[0].x) * v, f[0].y + (f[1].y - f[0].y) * u + (f[3].y - f[0].y) * v,
                f[0].z + (f[1].z - f[0].z) * u + (f[3].z - f[0].z) * v};
}

static void quad(SDL_Renderer *ren, SDL_Texture *tex, const P2 *p, const SDL_FPoint *uv, SDL_Color c) {
    SDL_Vertex v[4];
    for (int i = 0; i < 4; i++) v[i] = (SDL_Vertex){{p[i].x, p[i].y}, c, uv ? uv[i] : (SDL_FPoint){0, 0}};
    static const int idx[6] = {0, 1, 2, 0, 2, 3};
    SDL_RenderGeometry(ren, tex, v, 4, idx, 6);
}

/* a part of a face (u0..u1 across, v0..v1 down), filled or textured */
static void face_part(SDL_Renderer *ren, const P3 *f, float u0, float v0, float u1, float v1, float bx, float bz, float ry,
                      SDL_Texture *tex, const SDL_FPoint *uv, SDL_Color c) {
    P2 p[4] = {project(on_face(f, u0, v0), bx, bz, ry), project(on_face(f, u1, v0), bx, bz, ry),
               project(on_face(f, u1, v1), bx, bz, ry), project(on_face(f, u0, v1), bx, bz, ry)};
    quad(ren, tex, p, uv, c);
}

/* seen from outside: its corners run clockwise on screen */
static int facing(const P3 *f, float bx, float bz, float ry) {
    P2 a = project(f[0], bx, bz, ry), b = project(f[1], bx, bz, ry), d = project(f[3], bx, bz, ry);
    return (b.x - a.x) * (d.y - a.y) - (b.y - a.y) * (d.x - a.x) > 0;
}

static SDL_Color shade(SDL_Color c, int pct) { return (SDL_Color){c.r * pct / 100, c.g * pct / 100, c.b * pct / 100, c.a}; }

static void draw_spine(SDL_Renderer *ren, Item *it, const P3 *f, float bx, float bz, float ry, int lit, SDL_Color band) {
    SDL_Color k = {lit, lit, lit, 255};
    face_part(ren, f, 0, 0, 1, 1, bx, bz, ry, NULL, NULL, shade((SDL_Color){22, 22, 28, 255}, lit * 100 / 255));
    face_part(ren, f, 0, 0, 1, .09f, bx, bz, ry, NULL, NULL, shade(band, lit * 100 / 255));
    if (!it->spine) {
        SDL_Surface *t = TTF_RenderUTF8_Blended(g_small, it->title, (SDL_Color){230, 230, 238, 255});
        if (!t) return;
        it->spine = SDL_CreateTextureFromSurface(ren, t);
        it->sw = t->w, it->sh = t->h;
        SDL_FreeSurface(t);
    }
    /* the title runs down the spine, the tops of its letters towards the front (CSS vertical-rl) */
    float len = (f[3].y - f[0].y), across = fabsf(f[1].z - f[0].z) + fabsf(f[1].x - f[0].x);
    float sc = across * .62f / it->sh, tl = it->sw * sc / len;
    if (tl > .84f) tl = .84f;
    float u0 = .19f, u1 = .81f, v0 = .12f, v1 = .12f + tl, uend = it->sw * sc / len > .84f ? .84f / (it->sw * sc / len) : 1;
    SDL_FPoint uv[4] = {{0, 1}, {0, 0}, {uend, 0}, {uend, 1}};
    face_part(ren, f, u0, v0, u1, v1, bx, bz, ry, it->spine, uv, k);
}

static void draw_box(SDL_Renderer *ren, Item *it, const Tab *t, float bx, float bz, float ry, float h, int sel, int lit) {
    float w = h * tab_ratio_of(it, t), d = w * .12f, X = w / 2, Y = h / 2, Z = d / 2;
    P3 front[4] = {{-X, -Y, Z}, {X, -Y, Z}, {X, Y, Z}, {-X, Y, Z}};
    P3 back[4] = {{X, -Y, -Z}, {-X, -Y, -Z}, {-X, Y, -Z}, {X, Y, -Z}};
    P3 left[4] = {{-X, -Y, -Z}, {-X, -Y, Z}, {-X, Y, Z}, {-X, Y, -Z}};
    P3 right[4] = {{X, -Y, Z}, {X, -Y, -Z}, {X, Y, -Z}, {X, Y, Z}};
    SDL_Color band = it->color.a ? it->color : GOLD, cs = shade((SDL_Color){20, 20, 24, 255}, lit * 100 / 255);
    if (facing(left, bx, bz, ry)) draw_spine(ren, it, left, bx, bz, ry, lit, band);
    if (facing(right, bx, bz, ry)) draw_spine(ren, it, right, bx, bz, ry, lit, band);
    if (facing(back, bx, bz, ry) && it->back) { /* the back cover (Settings → Download back covers on the PC) */
        SDL_FPoint uv[4] = {{0, 0}, {1, 0}, {1, 1}, {0, 1}};
        face_part(ren, back, 0, 0, 1, 1, bx, bz, ry, it->back, uv, (SDL_Color){lit, lit, lit, 255});
    } else if (facing(back, bx, bz, ry)) {
        face_part(ren, back, 0, 0, 1, 1, bx, bz, ry, NULL, NULL, cs);
        face_part(ren, back, .06f, .06f, .94f, .94f, bx, bz, ry, NULL, NULL, shade((SDL_Color){30, 31, 40, 255}, lit * 100 / 255));
    }
    if (!facing(front, bx, bz, ry)) return;
    if (sel) { /* the glow round the selected box, in its console's colour */
        P2 p[4];
        for (int i = 0; i < 4; i++) p[i] = project(front[i], bx, bz, ry);
        P2 q[4];
        for (int i = 0; i < 4; i++) q[i] = (P2){p[i].x + (i == 0 || i == 3 ? -16 : 16), p[i].y + (i < 2 ? -16 : 16)};
        quad(ren, NULL, q, NULL, (SDL_Color){band.r, band.g, band.b, 90});
        P2 o[4];
        for (int i = 0; i < 4; i++) o[i] = (P2){p[i].x + (i == 0 || i == 3 ? -2.5f : 2.5f), p[i].y + (i < 2 ? -2.5f : 2.5f)};
        quad(ren, NULL, o, NULL, (SDL_Color){235, 235, 240, 220});
    }
    if (!it->tex) face_part(ren, front, 0, 0, 1, 1, bx, bz, ry, NULL, NULL, cs);
    SDL_Color k = {lit, lit, lit, 255};
    if (it->tex) {
        /* the cover fills the face, cropped to it; a square icon (Switch, PS4 packages) sits whole */
        float fa = w / h, ta = (float)it->tw / it->th;
        if (ta > fa * 1.2f) {
            float vh = fa / ta * .95f;
            face_part(ren, front, 0, 0, 1, 1, bx, bz, ry, NULL, NULL, cs);
            SDL_FPoint uv[4] = {{0, 0}, {1, 0}, {1, 1}, {0, 1}};
            face_part(ren, front, .025f, .5f - vh / 2, .975f, .5f + vh / 2, bx, bz, ry, it->tex, uv, k);
        } else {
            float u0 = 0, u1 = 1, v0 = 0, v1 = 1;
            if (ta > fa) u0 = (1 - fa / ta) / 2, u1 = 1 - u0;
            else v0 = (1 - ta / fa) / 2, v1 = 1 - v0;
            SDL_FPoint uv[4] = {{u0, v0}, {u1, v0}, {u1, v1}, {u0, v1}};
            face_part(ren, front, 0, 0, 1, 1, bx, bz, ry, it->tex, uv, k); /* the cover is the whole face */
        }
    } else {
        face_part(ren, front, .025f, .018f, .975f, .982f, bx, bz, ry, NULL, NULL, shade(mix(band, BG, 45), lit * 100 / 255));
        if (sel) {
            P2 a = project(on_face(front, .1f, .42f), bx, bz, ry), b = project(on_face(front, .9f, .42f), bx, bz, ry);
            draw_wrapped(ren, g_small, it->title, (SDL_Rect){(int)a.x, (int)a.y, (int)(b.x - a.x), (int)(h / 2)}, WHITE);
        }
    }
    /* the clear plastic: a soft light along the top of the selected box */
    if (sel) face_part(ren, front, 0, 0, 1, .2f, bx, bz, ry, NULL, NULL, (SDL_Color){255, 255, 255, 22});
}

/* where the box at d places from the selection sits (Boxshelf's place(), made continuous so the
 * shelf can slide between two selections) */
static void shelf_slot(float d, float bw, float *x, float *z, float *ry) {
    float ad = fabsf(d), sg = d < 0 ? -1.0f : 1.0f, bd = bw * .12f;
    if (ad <= 1) {
        *x = sg * bw * 1.22f * ad;
        *z = bw * 1.05f + (-bw * .7f - 6 - bw * 1.05f) * ad;
        *ry = 22 + (-sg * 60 - 22) * ad;
    } else {
        *x = sg * (bw * 1.22f + (ad - 1) * (bw * .22f + bd * .9f));
        *z = -bw * .7f - ad * 6;
        *ry = -sg * 60;
    }
}

static void glow_at(SDL_Renderer *ren, int cx, int cy, int rw, int rh, SDL_Color c) {
    if (!g_glow) {
        SDL_Surface *s = SDL_CreateRGBSurfaceWithFormat(0, 128, 128, 32, SDL_PIXELFORMAT_ARGB8888);
        if (!s) return;
        Uint32 *px = s->pixels;
        for (int y = 0; y < 128; y++)
            for (int x = 0; x < 128; x++) {
                float dx = (x - 63.5f) / 64, dy = (y - 63.5f) / 64, r = sqrtf(dx * dx + dy * dy);
                float a = r >= 1 ? 0 : (1 - r) * (1 - r);
                px[y * 128 + x] = ((Uint32)(a * 255) << 24) | 0xffffff;
            }
        g_glow = SDL_CreateTextureFromSurface(ren, s);
        SDL_FreeSurface(s);
        SDL_SetTextureBlendMode(g_glow, SDL_BLENDMODE_BLEND);
    }
    SDL_SetTextureColorMod(g_glow, c.r, c.g, c.b);
    SDL_SetTextureAlphaMod(g_glow, c.a);
    SDL_RenderCopy(ren, g_glow, NULL, &(SDL_Rect){cx - rw / 2, cy - rh / 2, rw, rh});
}

static int text_w(TTF_Font *f, const char *s) {
    int w = 0, h = 0;
    TTF_SizeUTF8(f, s, &w, &h);
    return w;
}

static void draw_shelf_hud(SDL_Renderer *ren, Tab *t, float h) {
    /* the console's name, big, top left; the consoles as a row of pills; the counter top right */
    char name[96], count[64];
    snprintf(name, sizeof name, "%s", t->name);
    for (char *c = name; *c; c++)
        if (*c >= 'a' && *c <= 'z') *c -= 32;
    draw_text_fit(ren, g_bold, name, 64, 44, 560, WHITE);
    snprintf(count, sizeof count, t->kind == TAB_RECENT ? "NEWEST FIRST" : "%d GAMES", t->count);
    draw_text(ren, g_small, count, 66, 98, DIM);
    int lo = g_tab, hi = g_tab, wsum = text_w(g_small, g_tabs[g_tab].name) + 40;
    while (lo > 0 || hi + 1 < g_ntabs) { /* as many neighbours as fit in 760 px */
        int grew = 0;
        if (hi + 1 < g_ntabs && wsum + text_w(g_small, g_tabs[hi + 1].name) + 40 <= 760) wsum += text_w(g_small, g_tabs[++hi].name) + 40, grew = 1;
        if (lo > 0 && wsum + text_w(g_small, g_tabs[lo - 1].name) + 40 <= 760) wsum += text_w(g_small, g_tabs[--lo].name) + 40, grew = 1;
        if (!grew) break;
    }
    int x = SCREEN_W / 2 - wsum / 2;
    fill(ren, (SDL_Rect){x - 6, 36, wsum + 12, 52}, (SDL_Color){8, 10, 20, 150});
    for (int i = lo; i <= hi; i++) {
        int w = text_w(g_small, g_tabs[i].name) + 40;
        if (i == g_tab) fill(ren, (SDL_Rect){x, 42, w, 40}, (SDL_Color){235, 235, 240, 255});
        draw_text(ren, g_small, g_tabs[i].name, x + 20, 49, i == g_tab ? (SDL_Color){16, 18, 26, 255} : DIM);
        x += w;
    }
    draw_text(ren, g_small, "L1", SCREEN_W / 2 - wsum / 2 - 52, 51, DIM);
    draw_text(ren, g_small, "R1", SCREEN_W / 2 + wsum / 2 + 22, 51, DIM);
    if (t->n) {
        snprintf(count, sizeof count, "%d / %d", t->sel + 1, t->n);
        draw_text(ren, g_font, count, SCREEN_W - 64 - text_w(g_font, count), 44, WHITE);
    }
    /* the title strip under the box */
    if (t->sel < t->n) {
        Item *it = &t->items[t->sel];
        int y = SHELF_Y + (int)(h / 2) + 70;
        int w = text_w(g_big, it->title);
        if (w > SCREEN_W - 200) w = SCREEN_W - 200;
        draw_text_fit(ren, g_big, it->title, SCREEN_W / 2 - w / 2, y, SCREEN_W - 200, WHITE);
        w = text_w(g_small, it->sub);
        draw_text(ren, g_small, it->sub, SCREEN_W / 2 - w / 2, y + 74, DIM);
    }
}

/* the backdrop: the console's colour washing down into black, a halo and a floor glow. Drawn once
 * per colour and shape into a texture; each frame only copies it. */
static void draw_backdrop(SDL_Renderer *ren, SDL_Color c, float bw, float h) {
    static SDL_Texture *tex;
    static SDL_Color made;
    static float made_bw;
    if (tex && !memcmp(&made, &c, sizeof c) && made_bw == bw) {
        SDL_RenderCopy(ren, tex, NULL, NULL);
        return;
    }
    if (!tex) {
        tex = SDL_CreateTexture(ren, SDL_PIXELFORMAT_ARGB8888, SDL_TEXTUREACCESS_TARGET, SCREEN_W, SCREEN_H);
        if (tex) SDL_SetTextureBlendMode(tex, SDL_BLENDMODE_NONE);
    }
    if (tex) SDL_SetRenderTarget(ren, tex);
    SDL_Vertex bg[4] = {{{0, 0}, mix(c, BG, 22), {0, 0}}, {{SCREEN_W, 0}, mix(c, BG, 22), {0, 0}},
                        {{SCREEN_W, SCREEN_H}, {4, 4, 8, 255}, {0, 0}}, {{0, SCREEN_H}, {4, 4, 8, 255}, {0, 0}}};
    static const int idx[6] = {0, 1, 2, 0, 2, 3};
    SDL_RenderGeometry(ren, NULL, bg, 4, idx, 6);
    glow_at(ren, SCREEN_W / 2, SHELF_Y, (int)(bw * 2.6f), (int)(h * 1.7f), (SDL_Color){c.r, c.g, c.b, 90});
    glow_at(ren, SCREEN_W / 2, SHELF_Y + (int)(h / 2) + 24, (int)(bw * 2.4f), 70, (SDL_Color){c.r, c.g, c.b, 150});
    if (!tex) return;
    SDL_SetRenderTarget(ren, NULL);
    made = c, made_bw = bw;
    SDL_RenderCopy(ren, tex, NULL, NULL);
}

static void draw_shelf(SDL_Renderer *ren, Tab *t) {
    SDL_Color c = t->kind == TAB_RECENT && t->sel < t->n ? t->items[t->sel].color : t->color;
    if (!c.a) c = GOLD;
    float h = 470, bw = h * t->ratio;
    draw_backdrop(ren, c, bw, h);
    if (!t->loaded || !t->n) {
        const char *msg = t->loaded ? "Nothing here yet." : "Couldn't load this shelf. Triangle: try again.";
        draw_text(ren, g_font, msg, SCREEN_W / 2 - text_w(g_font, msg) / 2, SHELF_Y, DIM);
        draw_shelf_hud(ren, t, h);
        return;
    }
    /* the outermost boxes first, the selected one last */
    int base = (int)floorf(g_pos + .5f);
    for (int k = SHELF_WIN; k >= 0; k--)
        for (int side = 1; side >= -1; side -= 2) {
            int i = base + side * k;
            if ((k == 0 && side < 0) || i < 0 || i >= t->n) continue;
            float d = i - g_pos, x, z, ry;
            shelf_slot(d, bw, &x, &z, &ry);
            int sel = i == t->sel && fabsf(d) < .5f;
            if (sel) ry += g_flip;
            float ad = fabsf(d);
            int lit = sel ? 255 : ad > SHELF_WIN - 1 ? (int)(170 * (SHELF_WIN - ad)) : 170;
            if (lit <= 0) continue;
            draw_box(ren, &t->items[i], t, x, z, ry, h, sel, lit);
        }
    draw_shelf_hud(ren, t, h);
}

/* eases the shelf and the flip towards where they should be; 1 while still moving */
static int shelf_step(Tab *t) {
    int moving = 0;
    float target = (float)t->sel;
    if (fabsf(target - g_pos) > .002f) g_pos += (target - g_pos) * .28f, moving = 1;
    else g_pos = target;
    if (fabsf(g_flip_to - g_flip) > .5f) g_flip += (g_flip_to - g_flip) * .2f, moving = 1;
    else g_flip = g_flip_to;
    return moving;
}

static void draw_downloads(SDL_Renderer *ren) {
    if (!g_njobs) {
        draw_text(ren, g_font, "No copies yet. Open a game and press Cross to send it to this PS5.", MARGIN_X, GRID_TOP, DIM);
        return;
    }
    int rows = 7, first = g_jobsel >= rows ? g_jobsel - rows + 1 : 0;
    for (int i = first; i < g_njobs && i < first + rows; i++) {
        Job *b = &g_jobs[i];
        SDL_Rect r = {MARGIN_X, GRID_TOP + (i - first) * 112, SCREEN_W - 2 * MARGIN_X, 100};
        fill(ren, r, PANEL);
        if (i == g_jobsel) frame(ren, r, 4, GOLD);
        draw_text_fit(ren, g_font, b->title, r.x + 24, r.y + 12, r.w - 560, WHITE);
        char line[384], done[32], total[32];
        human_size(b->done, done, sizeof done);
        human_size(b->total, total, sizeof total);
        int active = !strcmp(b->state, "running") || !strcmp(b->state, "verifying");
        if (active) snprintf(line, sizeof line, "%s · %s of %s", b->step, done, total);
        else if (!strcmp(b->state, "failed")) snprintf(line, sizeof line, "Failed: %s", b->error);
        else snprintf(line, sizeof line, "%s", b->step[0] ? b->step : !strcmp(b->state, "queued") ? "Waiting" : b->state);
        SDL_Color lc = !strcmp(b->state, "failed") ? RED : !strcmp(b->state, "done") ? GREEN : DIM;
        draw_text_fit(ren, g_small, line, r.x + 24, r.y + 60, r.w - 560, lc);
        SDL_Rect bar = {r.x + r.w - 500, r.y + 44, 460, 12};
        fill(ren, bar, TRACK);
        double frac = b->total > 0 ? b->done / b->total : !strcmp(b->state, "done");
        bar.w = (int)(bar.w * (frac > 1 ? 1 : frac));
        fill(ren, bar, !strcmp(b->state, "failed") ? RED : GOLD);
    }
}

/* ---------- a game's page ---------- */

typedef struct {
    Item it;
    char system[64], region[64], facts[6][160], state[24], step[128], reason[256];
    double done, total;
    int nfacts;
} Detail;

static int load_detail(Detail *d, const char *id) {
    char path[192];
    snprintf(path, sizeof path, "/api/items/%s", id);
    char *body = gs_get(g_host, g_port, path);
    cJSON *j = body ? cJSON_Parse(body) : NULL;
    free(body);
    if (!j) return -1;
    Item keep = d->it; /* the cover stays while the page refreshes */
    memset(d, 0, sizeof *d);
    parse_item(j, &d->it);
    d->it.key = keep.key, d->it.tex = keep.tex, d->it.tw = keep.tw, d->it.th = keep.th, d->it.asked = keep.asked;
    snprintf(d->system, sizeof d->system, "%s", jstr(j, "system_name"));
    snprintf(d->region, sizeof d->region, "%s", jstr(j, "region"));
    cJSON *f = cJSON_GetObjectItem(j, "facts");
    const char *keys[] = {"developer", "publisher", "genre", "rating"}, *names[] = {"Developer", "Publisher", "Genre", "Rating"};
    int year = (int)jnum(f, "year"), players = (int)jnum(f, "players");
    if (year) snprintf(d->facts[d->nfacts++], 160, "Released  %d", year);
    for (int k = 0; k < 4 && d->nfacts < 6; k++)
        if (jstr(f, keys[k])[0]) snprintf(d->facts[d->nfacts++], 160, "%s  %s", names[k], jstr(f, keys[k]));
    if (players && d->nfacts < 6) snprintf(d->facts[d->nfacts++], 160, "Players  %d", players);
    cJSON *st = cJSON_GetObjectItem(j, "status");
    snprintf(d->state, sizeof d->state, "%s", jstr(st, "state"));
    snprintf(d->step, sizeof d->step, "%s", jstr(st, "step"));
    snprintf(d->reason, sizeof d->reason, "%s", jstr(st, "reason")[0] ? jstr(st, "reason") : jstr(st, "error"));
    d->done = jnum(st, "done");
    d->total = jnum(st, "total");
    cJSON_Delete(j);
    return 0;
}

static int detail_busy(const Detail *d) {
    return !strcmp(d->state, "queued") || !strcmp(d->state, "running") || !strcmp(d->state, "verifying");
}

static int detail_sendable(const Detail *d) {
    return d->state[0] && strcmp(d->state, "unsupported") && strcmp(d->state, "nodest") && !detail_busy(d);
}

static void draw_detail(SDL_Renderer *ren, Detail *d) {
    fill(ren, (SDL_Rect){MARGIN_X, 180, 680, 800}, mix(d->it.color, PANEL, 22));
    draw_cover(ren, &d->it, (SDL_Rect){MARGIN_X + 40, 200, 600, 760}, g_font);
    int x = MARGIN_X + 760, y = 200, w = SCREEN_W - x - MARGIN_X;
    y += draw_wrapped(ren, g_big, d->it.title, (SDL_Rect){x, y, w, 200}, WHITE) + 16;
    char line[256];
    snprintf(line, sizeof line, "%s%s%s", d->system, d->region[0] ? " · " : "", d->region);
    draw_text_fit(ren, g_font, line, x, y, w, DIM);
    y += 56;
    draw_text_fit(ren, g_small, d->it.sub, x, y, w, DIM);
    y += 56;
    for (int i = 0; i < d->nfacts; i++, y += 40) draw_text_fit(ren, g_small, d->facts[i], x, y, w, WHITE);

    SDL_Rect panel = {x, 760, w, 200};
    fill(ren, panel, PANEL);
    const char *head = "", *hint = "";
    SDL_Color hc = WHITE;
    if (!strcmp(d->state, "ready")) head = "Ready to copy to this PS5", hint = "Cross  Send to PS5";
    else if (!strcmp(d->state, "onps5")) head = "Already on this PS5", hc = GREEN, hint = "Cross  Copy it again";
    else if (!strcmp(d->state, "sent")) head = d->step[0] ? d->step : "Sent", hc = GREEN, hint = "Cross  Send it again";
    else if (!strcmp(d->state, "failed")) head = "The last copy failed", hc = RED, hint = "Cross  Try again";
    else if (!strcmp(d->state, "unsupported")) head = "No PS5 emulator for this console yet", hc = DIM;
    else if (!strcmp(d->state, "nodest")) head = "No PS5 folder chosen for this console", hc = DIM;
    else if (!strcmp(d->state, "queued")) head = "Waiting to copy";
    else if (detail_busy(d)) head = d->step[0] ? d->step : "Copying";
    draw_text_fit(ren, g_font, head, panel.x + 30, panel.y + 26, panel.w - 60, hc);
    if (detail_busy(d) && d->total > 0) {
        SDL_Rect bar = {panel.x + 30, panel.y + 92, panel.w - 60, 14};
        fill(ren, bar, TRACK);
        double frac = d->done / d->total;
        bar.w = (int)(bar.w * (frac > 1 ? 1 : frac));
        fill(ren, bar, GOLD);
        char done[32], total[32];
        human_size(d->done, done, sizeof done);
        human_size(d->total, total, sizeof total);
        snprintf(line, sizeof line, "%s of %s", done, total);
        draw_text(ren, g_small, line, panel.x + 30, panel.y + 126, DIM);
    } else if (d->reason[0]) {
        draw_wrapped(ren, g_small, d->reason, (SDL_Rect){panel.x + 30, panel.y + 86, panel.w - 60, 60}, DIM);
    }
    if (hint[0]) draw_text(ren, g_small, hint, panel.x + 30, panel.y + 150, GOLD);
}

static void draw_message(SDL_Renderer *ren, const char *l1, const char *l2) {
    SDL_SetRenderDrawColor(ren, BG.r, BG.g, BG.b, 255);
    SDL_RenderClear(ren);
    fill(ren, (SDL_Rect){MARGIN_X, 420, 18, 46}, GOLD);
    draw_text(ren, g_bold, "Game Shop", MARGIN_X + 34, 414, WHITE);
    draw_text(ren, g_font, l1, MARGIN_X, 500, WHITE);
    draw_wrapped(ren, g_small, l2, (SDL_Rect){MARGIN_X, 560, SCREEN_W - 2 * MARGIN_X, 300}, DIM);
    SDL_RenderPresent(ren);
}

/* ---------- input ---------- */

static void move_shelf(Tab *t, int button) {
    if (button == SDL_CONTROLLER_BUTTON_DPAD_LEFT && t->sel > 0) t->sel--, g_flip_to = 0;
    if (button == SDL_CONTROLLER_BUTTON_DPAD_RIGHT && t->sel + 1 < t->n) t->sel++, g_flip_to = 0;
}

static int key_to_button(SDL_Keycode k) {
    switch (k) {
    case SDLK_UP: return SDL_CONTROLLER_BUTTON_DPAD_UP;
    case SDLK_DOWN: return SDL_CONTROLLER_BUTTON_DPAD_DOWN;
    case SDLK_LEFT: return SDL_CONTROLLER_BUTTON_DPAD_LEFT;
    case SDLK_RIGHT: return SDL_CONTROLLER_BUTTON_DPAD_RIGHT;
    case SDLK_RETURN: return SDL_CONTROLLER_BUTTON_A;
    case SDLK_ESCAPE: return SDL_CONTROLLER_BUTTON_B;
    case SDLK_r: return SDL_CONTROLLER_BUTTON_Y;
    case SDLK_f: return SDL_CONTROLLER_BUTTON_X;
    case SDLK_q: return SDL_CONTROLLER_BUTTON_LEFTSHOULDER;
    case SDLK_e: return SDL_CONTROLLER_BUTTON_RIGHTSHOULDER;
    default: return -1;
    }
}

static int is_dpad(int b) {
    return b == SDL_CONTROLLER_BUTTON_DPAD_LEFT || b == SDL_CONTROLLER_BUTTON_DPAD_RIGHT ||
           b == SDL_CONTROLLER_BUTTON_DPAD_UP || b == SDL_CONTROLLER_BUTTON_DPAD_DOWN;
}

static void open_tab(int t) {
    g_tab = t;
    covers_forget();
    Tab *tab = &g_tabs[t];
    for (int i = 0; i < tab->n; i++) tab->items[i].asked = tab->items[i].tex != NULL;
    if (tab->kind == TAB_DOWNLOADS) load_jobs();
    else load_tab(tab);
    g_pos = (float)tab->sel, g_flip = g_flip_to = 0;
}

static void send_game(Detail *d) {
    char path[192];
    snprintf(path, sizeof path, "/api/items/%s/download", d->it.id);
    char *r = gs_http(g_host, g_port, "POST", path, "{}", NULL);
    toast(r ? "Added to Downloads." : error_text());
    free(r);
    load_detail(d, d->it.id);
}

static void job_action(void) {
    if (g_jobsel >= g_njobs) return;
    Job *b = &g_jobs[g_jobsel];
    int retry = !strcmp(b->state, "failed") || !strcmp(b->state, "cancelled");
    if (!retry && !strcmp(b->state, "done")) return;
    char path[96];
    snprintf(path, sizeof path, "/api/jobs/%ld/%s", b->id, retry ? "retry" : "cancel");
    char *r = gs_http(g_host, g_port, "POST", path, NULL, NULL);
    toast(r ? (retry ? "Trying again." : "Cancelled.") : error_text());
    free(r);
    load_jobs();
}

int main(int argc, char **argv) {
    (void)argc, (void)argv;
    signal(SIGPIPE, SIG_IGN);
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_GAMECONTROLLER) != 0) {
        fprintf(stderr, "Game Shop: SDL_Init failed: %s\n", SDL_GetError());
        return 1;
    }
    if (TTF_Init() != 0) fprintf(stderr, "TTF_Init: %s\n", TTF_GetError());
    IMG_Init(IMG_INIT_JPG | IMG_INIT_PNG);
    /* the setup FLEX found works on the console: a plain 1920x1080 window and SDL's software
     * renderer (the only render driver this SDL port has) */
    SDL_Window *win = SDL_CreateWindow("Game Shop", SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED, SCREEN_W, SCREEN_H, 0);
    SDL_SetHint(SDL_HINT_RENDER_DRIVER, "software");
    SDL_SetHint(SDL_HINT_RENDER_SCALE_QUALITY, "linear");
    SDL_Renderer *ren = win ? SDL_CreateRenderer(win, -1, SDL_RENDERER_SOFTWARE | SDL_RENDERER_PRESENTVSYNC) : NULL;
    if (!ren) {
        fprintf(stderr, "Game Shop: no window or renderer: %s\n", SDL_GetError());
        return 1;
    }
    SDL_SetRenderDrawBlendMode(ren, SDL_BLENDMODE_BLEND);
    for (int i = 0; i < SDL_NumJoysticks(); i++)
        if (SDL_IsGameController(i)) SDL_GameControllerOpen(i);

    const char *dirs[] = {"/app0/assets/", APP_HOME "/assets/"}; /* the app's own folder, by either name */
    for (int i = 0; i < 2 && !g_bold; i++) {
        char p[256];
        snprintf(p, sizeof p, "%sDejaVuSans.ttf", dirs[i]);
        g_big = TTF_OpenFont(p, 52);
        g_font = TTF_OpenFont(p, 34);
        g_small = TTF_OpenFont(p, 22);
        snprintf(p, sizeof p, "%sDejaVuSans-Bold.ttf", dirs[i]);
        g_bold = TTF_OpenFont(p, 40);
    }
    if (!g_big || !g_font || !g_small || !g_bold) {
        fprintf(stderr, "Game Shop: fonts missing from the app's assets: %s\n", TTF_GetError());
        return 1;
    }

    int have_url = read_url() == 0;
    if (have_url) covers_init(g_host, g_port);
    int connected = have_url && load_state() == 0;
    fprintf(stderr, "Game Shop: PC %s %s\n", have_url ? g_url : "(no url.txt)", connected ? "answered" : "did not answer");
    if (connected) open_tab(0);

    Detail detail = {0};
    int in_detail = 0, dirty = 1, quit = 0, held = -1, moving = 0;
    Uint32 circle_down = 0, last_poll = SDL_GetTicks(), repeat_at = 0;
    while (!quit) {
        if (!connected && dirty) {
            char l2[512];
            if (!have_url)
                snprintf(l2, sizeof l2, "This app doesn't know where your PC is yet. In Game Shop on the PC, open Settings "
                                        "and press \"Put a Game Shop tile on the PS5\".     Hold Circle to close.");
            else
                snprintf(l2, sizeof l2, "Tried %s. Make sure Game Shop is running on the PC and the PS5 is on the same "
                                        "network.     Triangle  Try again     Hold Circle  Close", g_url);
            draw_message(ren, "Can't reach Game Shop on your PC", l2);
            dirty = 0;
        }
        SDL_Event e;
        int button = -1;
        if (SDL_WaitEventTimeout(&e, moving ? 1 : 50)) {
            if (e.type == SDL_QUIT) quit = 1;
            if (e.type == SDL_CONTROLLERBUTTONDOWN) button = e.cbutton.button;
            if (e.type == SDL_KEYDOWN && !e.key.repeat) button = key_to_button(e.key.keysym.sym);
            if (e.type == SDL_CONTROLLERBUTTONUP && e.cbutton.button == held) held = -1;
            if ((e.type == SDL_CONTROLLERBUTTONUP && e.cbutton.button == SDL_CONTROLLER_BUTTON_B) ||
                (e.type == SDL_KEYUP && e.key.keysym.sym == SDLK_ESCAPE)) {
                if (circle_down && in_detail) in_detail = 0, dirty = 1; /* a short press: back */
                circle_down = 0;
            }
        }
        Uint32 now = SDL_GetTicks();
        if (button >= 0 && is_dpad(button)) held = button, repeat_at = now + 380; /* a held direction repeats */
        else if (button < 0 && held >= 0 && now >= repeat_at) button = held, repeat_at = now + 90;
        if (button == SDL_CONTROLLER_BUTTON_B) circle_down = now;
        if (circle_down && now - circle_down >= HOLD_EXIT_MS) quit = 1;

        if (!connected) {
            if (button == SDL_CONTROLLER_BUTTON_Y && have_url) {
                draw_message(ren, "Looking for Game Shop on your PC…", g_url);
                connected = load_state() == 0;
                if (connected) open_tab(0);
                dirty = 1;
            }
            continue;
        }

        Tab *t = &g_tabs[g_tab];
        if (in_detail) {
            if (button == SDL_CONTROLLER_BUTTON_A && detail_sendable(&detail)) send_game(&detail), dirty = 1;
            if (button == SDL_CONTROLLER_BUTTON_Y) load_detail(&detail, detail.it.id), dirty = 1;
            if (detail_busy(&detail) && now - last_poll > 2000) {
                load_detail(&detail, detail.it.id);
                last_poll = now;
                dirty = 1;
            }
        } else if (button >= 0) {
            dirty = 1;
            int shelf = t->kind != TAB_DOWNLOADS;
            if ((button == SDL_CONTROLLER_BUTTON_LEFTSHOULDER || (shelf && button == SDL_CONTROLLER_BUTTON_DPAD_UP)) && g_tab > 0)
                open_tab(g_tab - 1);
            else if ((button == SDL_CONTROLLER_BUTTON_RIGHTSHOULDER || (shelf && button == SDL_CONTROLLER_BUTTON_DPAD_DOWN)) &&
                     g_tab + 1 < g_ntabs)
                open_tab(g_tab + 1);
            else if (button == SDL_CONTROLLER_BUTTON_Y) {
                if (load_state() == 0) open_tab(g_tab);
                else toast(error_text());
            } else if (t->kind == TAB_DOWNLOADS) {
                if (button == SDL_CONTROLLER_BUTTON_DPAD_UP && g_jobsel > 0) g_jobsel--;
                if (button == SDL_CONTROLLER_BUTTON_DPAD_DOWN && g_jobsel + 1 < g_njobs) g_jobsel++;
                if (button == SDL_CONTROLLER_BUTTON_A) job_action();
            } else if (is_dpad(button)) {
                move_shelf(t, button);
            } else if (button == SDL_CONTROLLER_BUTTON_X) {
                g_flip_to = g_flip_to ? 0 : 180;
                Item *it = t->sel < t->n ? &t->items[t->sel] : NULL;
                if (it && !it->back_state) covers_want(-it->key, it->id, THUMB_W, 1), it->back_state = 1;
            } else if (button == SDL_CONTROLLER_BUTTON_A && t->sel < t->n) {
                draw_message(ren, "Opening…", t->items[t->sel].title);
                if (detail.it.tex) SDL_DestroyTexture(detail.it.tex);
                memset(&detail, 0, sizeof detail);
                detail.it.key = -g_gen; /* never a grid key */
                if (load_detail(&detail, t->items[t->sel].id) == 0) {
                    in_detail = 1;
                    if (detail.it.art) covers_want(detail.it.key, detail.it.id, DETAIL_W, 0);
                } else {
                    toast(error_text());
                }
            }
        }
        if (!in_detail && g_tabs[g_tab].kind == TAB_DOWNLOADS && now - last_poll > 2000) {
            load_jobs();
            last_poll = now;
            dirty = 1;
        }
        if (take_covers(ren, in_detail ? &detail.it : NULL)) dirty = 1;
        if (g_toast[0] && now >= g_toast_until) g_toast[0] = 0, dirty = 1;
        moving = !in_detail && g_tabs[g_tab].kind != TAB_DOWNLOADS && shelf_step(&g_tabs[g_tab]);
        if (moving) dirty = 1;
        if (!dirty) continue;
        dirty = 0;

        SDL_SetRenderDrawColor(ren, BG.r, BG.g, BG.b, 255);
        SDL_RenderClear(ren);
        t = &g_tabs[g_tab];
        if (in_detail || t->kind == TAB_DOWNLOADS) draw_header(ren);
        if (in_detail) {
            draw_detail(ren, &detail);
            draw_legend(ren, detail_sendable(&detail) ? "Cross  Send to PS5     Triangle  Refresh     Circle  Back"
                                                      : "Triangle  Refresh     Circle  Back");
        } else if (t->kind == TAB_DOWNLOADS) {
            draw_downloads(ren);
            draw_legend(ren, "Up/Down  Choose     Cross  Cancel or try again     L1/R1  Console     Hold Circle  Close");
        } else {
            want_visible(t);
            draw_shelf(ren, t);
            draw_legend(ren, "Left/Right  Browse     L1/R1  Console     Cross  Open     Square  Flip     Triangle  Reload     Hold Circle  Close");
        }
        SDL_RenderPresent(ren);
        if (moving) { /* how long a frame takes while the shelf slides, once in a while in the log */
            static Uint32 frames, since;
            if (!frames) since = now;
            if (++frames == 120) {
                fprintf(stderr, "Game Shop: shelf %.1f ms a frame\n", (SDL_GetTicks() - since) / 120.0);
                frames = 0;
            }
        }
    }
    SDL_Quit();
    return 0;
}

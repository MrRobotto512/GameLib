#include "covers.h"

#include <SDL_image.h>
#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "app.h"
#include "net.h"

#define CACHE_DIR APP_DATA "/covers"
#define WORKERS 4
#define MAX_DONE 256

typedef struct {
    long key;
    char id[128];
    int width, back;
} Job;

typedef struct {
    long key;
    SDL_Surface *s;
} Done;

static SDL_mutex *mu;
static SDL_cond *cond;
static char host[128];
static int port;
static Job *wanted; /* a stack: newest first */
static int nwanted, capwanted;
static Done done[MAX_DONE];
static int ndone;

static void cache_path(const Job *j, char *out, size_t n) {
    char safe[128];
    size_t i = 0;
    for (const char *c = j->id; *c && i < sizeof safe - 1; c++) safe[i++] = isalnum((unsigned char)*c) ? *c : '_';
    safe[i] = 0;
    snprintf(out, n, CACHE_DIR "/%s.w%d%s.img", safe, j->width, j->back ? ".back" : "");
}

static char *read_file(const char *p, size_t *len) {
    FILE *f = fopen(p, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *b = n > 0 ? malloc(n) : NULL;
    if (b && fread(b, 1, n, f) != (size_t)n) {
        free(b);
        b = NULL;
    }
    fclose(f);
    *len = b ? (size_t)n : 0;
    return b;
}

/* write_cache stores bytes under a per-worker temporary name first, so a half-written file is
 * never mistaken for a cover. */
static void write_cache(const char *p, const char *bytes, size_t len, int worker) {
    char tmp[256];
    snprintf(tmp, sizeof tmp, "%s.%d.part", p, worker);
    FILE *f = fopen(tmp, "wb");
    if (!f) return;
    int ok = fwrite(bytes, 1, len, f) == len;
    if (fclose(f) != 0) ok = 0;
    if (ok) rename(tmp, p);
    else remove(tmp);
}

static void push_done(long key, SDL_Surface *s) {
    SDL_LockMutex(mu);
    if (ndone < MAX_DONE) done[ndone++] = (Done){key, s};
    else if (s) SDL_FreeSurface(s); /* the UI is far behind; it asks again later */
    SDL_UnlockMutex(mu);
}

static int worker(void *arg) {
    int me = (int)(intptr_t)arg;
    for (;;) {
        SDL_LockMutex(mu);
        while (!nwanted) SDL_CondWait(cond, mu);
        Job j = wanted[--nwanted];
        SDL_UnlockMutex(mu);

        char file[256], path[256];
        cache_path(&j, file, sizeof file);
        size_t len = 0;
        char *bytes = read_file(file, &len);
        if (!bytes) {
            snprintf(path, sizeof path, "/art/%s?w=%d%s", j.id, j.width, j.back ? "&kind=back" : "");
            if ((bytes = gs_fetch(host, port, path, &len)) && len > 0) write_cache(file, bytes, len, me);
        }
        SDL_Surface *s = bytes ? IMG_Load_RW(SDL_RWFromConstMem(bytes, (int)len), 1) : NULL;
        if (bytes && !s) {
            fprintf(stderr, "cover %s: %s\n", j.id, IMG_GetError());
            remove(file); /* damaged: fetch it again next time */
        }
        free(bytes);
        push_done(j.key, s);
    }
    return 0;
}

void covers_init(const char *h, int p) {
    mkdir(APP_DATA, 0777);
    mkdir(CACHE_DIR, 0777);
    snprintf(host, sizeof host, "%s", h);
    port = p;
    mu = SDL_CreateMutex();
    cond = SDL_CreateCond();
    for (int i = 0; i < WORKERS; i++) SDL_DetachThread(SDL_CreateThread(worker, "gs-covers", (void *)(intptr_t)i));
}

void covers_want(long key, const char *item_id, int width, int back) {
    SDL_LockMutex(mu);
    if (nwanted == capwanted) {
        Job *g = realloc(wanted, (capwanted = capwanted * 2 + 64) * sizeof *wanted);
        if (!g) {
            SDL_UnlockMutex(mu);
            return;
        }
        wanted = g;
    }
    Job *j = &wanted[nwanted++];
    j->key = key;
    j->width = width;
    j->back = back;
    snprintf(j->id, sizeof j->id, "%s", item_id);
    SDL_CondSignal(cond);
    SDL_UnlockMutex(mu);
}

void covers_forget(void) {
    SDL_LockMutex(mu);
    nwanted = 0;
    SDL_UnlockMutex(mu);
}

int covers_take(long *key, SDL_Surface **s) {
    SDL_LockMutex(mu);
    int got = ndone > 0;
    if (got) {
        Done d = done[--ndone];
        *key = d.key;
        *s = d.s;
    }
    SDL_UnlockMutex(mu);
    return got;
}

/* Startup for the Game Shop PS5 app (from FLEX's app/start.c, github.com/MrRobotto512/FLEX).
 *
 * An app is started by the system, not injected by a loader like a payload: the loader calls
 * _start with the process parameters (argc, then the argv array) and a teardown function. We set
 * up the C library, run the constructors, call main, and when main returns ask the system to
 * close the app, which takes us back to the home screen. */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>

#include "app.h"

typedef void (*hook)(void);

void _init_env(void *params);
int sceSystemServiceLoadExec(const char *path, char *const argv[]);
int main(int argc, char **argv);

extern hook __preinit_array_start[] __attribute__((weak));
extern hook __preinit_array_end[] __attribute__((weak));
extern hook __init_array_start[] __attribute__((weak));
extern hook __init_array_end[] __attribute__((weak));
extern hook __fini_array_start[] __attribute__((weak));
extern hook __fini_array_end[] __attribute__((weak));

static void run(hook *first, hook *last) {
    if (first && last)
        for (; first != last; first++) (*first)();
}

static void fini(void) {
    if (!__fini_array_start || !__fini_array_end) return;
    for (hook *h = __fini_array_end; h != __fini_array_start;) (*--h)();
}

/* The payload SDK's libc.a (linked for getaddrinfo and friends) routes dlopen and executable
 * mappings through the payload loader in its crt1.o, which an app doesn't have. We load nothing
 * at run time and never map code, so these only need to say no. */
void *__dlopen(const char *path, int mode) {
    (void)path, (void)mode;
    return NULL;
}
void *__dlsym(void *handle, const char *name) {
    (void)handle, (void)name;
    return NULL;
}
int __dlclose(void *handle) {
    (void)handle;
    return -1;
}
char *__dlerror(void) { return "no dynamic loading in the Game Shop app"; }
int __dladdr(const void *addr, void *info) {
    (void)addr, (void)info;
    return 0;
}
int kernel_mprotect(int pid, intptr_t addr, size_t len, int prot) {
    (void)pid, (void)addr, (void)len, (void)prot;
    return -1;
}

/* System functions the app loader may leave unbound: an app gets only some system modules
 * loaded with it, and a call into one that isn't is a call to address 0. SDL's PS5 port sets up
 * the USB keyboard and the system's text-input dialog, which we don't use, so the Makefile
 * routes those calls here (--wrap): they go through when bound and fail politely when not. */
#define GUARDED(X) X(sceKeyboardInit) X(sceKeyboardOpen) X(sceKeyboardClose) X(sceKeyboardReadState) \
    X(sceImeDialogInit) X(sceImeDialogGetStatus) X(sceImeDialogGetResult) X(sceImeDialogTerm)
#define WRAP(name)                                                                                   \
    extern int __real_##name(long, long, long, long, long, long) __attribute__((weak));             \
    int __wrap_##name(long a, long b, long c, long d, long e, long f) {                              \
        return __real_##name ? __real_##name(a, b, c, d, e, f) : (int)0x80020002; /* not loaded */ \
    }
GUARDED(WRAP)

/* SDL's screen: one 64 MB block from sceKernelAllocateMainDirectMemory. When that fails, ask
 * for it the way working apps do (FLEX found the first way can be refused). */
size_t sceKernelGetDirectMemorySize(void);
int sceKernelAllocateDirectMemory(long start, long end, size_t len, size_t align, int type, long *phys);
int __real_sceKernelAllocateMainDirectMemory(size_t len, size_t align, int type, long *phys);

int __wrap_sceKernelAllocateMainDirectMemory(size_t len, size_t align, int type, long *phys) {
    int r = __real_sceKernelAllocateMainDirectMemory(len, align, type, phys);
    if (r == 0) return 0;
    int again = sceKernelAllocateDirectMemory(0, (long)sceKernelGetDirectMemorySize(), len, align, type, phys);
    fprintf(stderr, "Game Shop: screen memory: %zu MB from main direct memory failed (0x%08x); asking directly %s\n",
            len >> 20, (unsigned)r, again == 0 ? "worked" : "failed");
    return again;
}

/* The app's stdout and stderr go nowhere anyone can read; they go to a file instead (the
 * previous run's stays as log.1.txt). In an app the C library's stderr isn't file descriptor 2,
 * so the streams themselves are swapped. */
static void capture_output(void) {
    mkdir(APP_DATA, 0777);
    rename(APP_DATA "/log.txt", APP_DATA "/log.1.txt");
    FILE *f = fopen(APP_DATA "/log.txt", "w");
    if (!f) return;
    setvbuf(f, NULL, _IONBF, 0);
    stdout = f;
    stderr = f;
}

__attribute__((visibility("default"), noreturn)) void _start(void *params, hook teardown) {
    int argc = *(int *)params;
    char **argv = (char **)((uint8_t *)params + sizeof(uint64_t));
    _init_env(params);
    if (teardown) atexit(teardown);
    atexit(fini);
    run(__preinit_array_start, __preinit_array_end);
    run(__init_array_start, __init_array_end);
    capture_output();
    fprintf(stderr, "Game Shop: %s\n", app_heap_desc());
    int status = main(argc, argv);
    fprintf(stderr, "Game Shop: main returned %d, closing the app\n", status);
    sceSystemServiceLoadExec("exit", NULL); /* closes the app; returns only if refused */
    exit(status);
}

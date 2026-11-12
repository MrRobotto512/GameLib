/* Game Shop tile: a tiny PS5 app whose only job is to open the store in the PS5's web browser.
   Shadow Mount+ puts any /data/homebrew/<ID>/ folder holding eboot.bin, sce_module/libc.prx and
   sce_sys/{param.json,icon0.png} on the home screen; the PC uploads this one over FTP, with the
   store's address in url.txt next to the eboot (so the signed eboot is never patched).
   Built with the app kit from Forwarder Manager, the way FLEX builds its app (see ps5/Makefile). */
#include <fcntl.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifndef TITLE_ID
#define TITLE_ID "PPSA87500"
#endif
#define URL_FILE "/data/homebrew/" TITLE_ID "/url.txt"

typedef void (*hook)(void);

void _init_env(void *params);
int sceSystemServiceLoadExec(const char *path, char *const argv[]);
int sceSystemServiceHideSplashScreen(void);
int sceSystemServiceLaunchWebBrowser(const char *url, void *param);
int sceUserServiceInitialize(void *params);
int sceKernelUsleep(unsigned int usec);

typedef struct {
  char pad[45];
  char message[3075];
} notify_t;
int sceKernelSendNotificationRequest(int, notify_t *, size_t, int);

static void notify(const char *text) {
  notify_t n;
  memset(&n, 0, sizeof n);
  strncpy(n.message, text, sizeof n.message - 1);
  sceKernelSendNotificationRequest(0, &n, sizeof n, 0);
}

/* The store's address: the first line of url.txt. */
static int read_url(char *url, size_t size) {
  int fd = open(URL_FILE, O_RDONLY);
  if (fd < 0) return -1;
  ssize_t n = read(fd, url, size - 1);
  close(fd);
  if (n <= 0) return -1;
  url[n] = 0;
  url[strcspn(url, "\r\n")] = 0;
  return url[0] ? 0 : -1;
}

static void run(void) {
  char url[512];
  sceSystemServiceHideSplashScreen();
  if (read_url(url, sizeof url)) {
    notify("Game Shop: no store address here. Press \"Put a Game Shop tile on the PS5\" in Game Shop's Settings.");
    return;
  }
  sceUserServiceInitialize(0);
  int err = sceSystemServiceLaunchWebBrowser(url, 0);
  if (err) {
    notify("Game Shop: the web browser would not open.");
    return;
  }
  sceKernelUsleep(500 * 1000);   /* let the browser take the screen before we go */
}

/* An app is started by the system, not injected: the loader calls _start with the process
   parameters and a teardown function. Set up the C library, open the store, and close the app
   (sceSystemServiceLoadExec("exit")), which leaves the browser in front. */
__attribute__((visibility("default"), noreturn)) void _start(void *params, hook teardown) {
  _init_env(params);
  if (teardown) atexit(teardown);
  run();
  sceSystemServiceLoadExec("exit", NULL);   /* returns only if refused */
  exit(0);
}

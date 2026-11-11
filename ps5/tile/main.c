/* Game Shop tile: register /user/app/<TITLE_ID> (a web shortcut: sce_sys/param.json with a
   deeplinkUri, plus icon0.png, both put there over FTP by the PC) as an app on the PS5 home
   screen. Built with john-tornblom's ps5-payload-sdk and sent to elfldr (port 9021).
   Calling it again for a title that is already installed is harmless: that is how the URL is
   refreshed when the PC's address changes. */
#include <stdio.h>
#include <string.h>

#ifndef TITLE_ID
#define TITLE_ID "GMSH00001"
#endif

int sceAppInstUtilInitialize(void);
int sceAppInstUtilAppInstallTitleDir(const char *, const char *, void *);

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

int main(void) {
  int err;
  if ((err = sceAppInstUtilInitialize())) {
    printf("sceAppInstUtilInitialize: %x\n", err);
    notify("Game Shop: could not start the app installer");
    return 1;
  }
  if ((err = sceAppInstUtilAppInstallTitleDir(TITLE_ID, "/user/app/", 0))) {
    printf("sceAppInstUtilAppInstallTitleDir: %x\n", err);
    notify("Game Shop: the tile could not be added");
    return 1;
  }
  notify("Game Shop is on the home screen");
  return 0;
}

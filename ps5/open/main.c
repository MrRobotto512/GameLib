/* Game Shop open: launch the PS5's web browser at a URL. The URL lives in a fixed buffer that
   the PC overwrites in the ELF before sending it to elfldr (see gameshop/ps5.py), so one build
   serves every PC address. Built with john-tornblom's ps5-payload-sdk. */
#include <stdio.h>
#include <stdlib.h>

int sceUserServiceInitialize(void *);
int sceUserServiceTerminate(void);
int sceSystemServiceLaunchWebBrowser(const char *, void *);

/* 512 bytes, NUL padded; the marker is what the PC looks for. Not const: it must land in .data. */
char gameshop_url[512] = "GAMESHOP_URL_PLACEHOLDER";

__attribute__((constructor)) static void init(void) {
  if (sceUserServiceInitialize(0)) {
    perror("sceUserServiceInitialize");
  }
}

int main(void) {
  if (sceSystemServiceLaunchWebBrowser(gameshop_url, 0)) {
    perror("sceSystemServiceLaunchWebBrowser");
    return EXIT_FAILURE;
  }
  return EXIT_SUCCESS;
}

__attribute__((destructor)) static void fini(void) {
  sceUserServiceTerminate();
}

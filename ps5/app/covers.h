/* Cover loading off the UI thread (from FLEX's ps5-client/src/posters.c). Worker threads fetch
 * covers from the PC (several at a time) and keep them on the console's disk, so each crosses
 * the network once, and decode them; the main thread collects the decoded surfaces with
 * covers_take and makes textures. */
#pragma once
#include <SDL.h>

void covers_init(const char *host, int port);

/* covers_want asks for a game's cover (or, with back set, its back cover), width pixels wide, to
 * show soon. key comes back with the picture. The newest request is served first, so what's on
 * screen wins over what was scrolled past. ver is the PC's number for the cover it has now (art_v):
 * a copy kept on the console under another number is not used, so a better cover replaces it. */
void covers_want(long key, const char *item_id, int width, int back, long ver);

/* covers_forget drops requests not yet started (a new console or page was opened). */
void covers_forget(void);

/* covers_take hands over one decoded cover (NULL surface: the game has none). Returns 0 when none
 * is ready. The caller frees the surface. */
int covers_take(long *key, SDL_Surface **s);

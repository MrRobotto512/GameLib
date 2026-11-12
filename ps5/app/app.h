/* What app/start.c and app/heap.c offer the rest of the Game Shop app. */

#ifndef GAMESHOP_APP_H
#define GAMESHOP_APP_H

#include <stddef.h>

#ifndef TITLE_ID
#define TITLE_ID "PPSA87500"
#endif
#define APP_HOME "/data/homebrew/" TITLE_ID /* the app's own folder: eboot.bin, url.txt */
#define APP_DATA "/data/gameshop"           /* log and cover cache, outside the app's folder */

/* app_heap_desc says where the app's heap came from and how big it is. */
const char *app_heap_desc(void);

/* app_heap_mb: the heap in use, taken from its memory so far, and its whole size, in MB. -1 if
 * there is no such heap. */
int app_heap_mb(long *inuse, long *system, long *cap);

#endif

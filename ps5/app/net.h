/* A minimal HTTP/1.0 client for Game Shop's API (from FLEX's ps5-client/src/net.c). */
#pragma once
#include <stddef.h>

/* gs_http sends one request and returns the malloc'd, NUL-terminated body of a 2xx answer (len
 * set if non-NULL), or NULL. json_body, when given, goes as application/json. */
char *gs_http(const char *host, int port, const char *method, const char *path, const char *json_body, size_t *len);

/* gs_fetch is a GET for other threads (the cover loaders): it leaves gs_error alone. */
char *gs_fetch(const char *host, int port, const char *path, size_t *len);

/* The body of the main thread's last answer that wasn't 2xx (Game Shop says why as
 * {"detail": ...}); "" when the PC didn't answer at all. */
extern char gs_error[512];

#define gs_get(h, p, path) gs_http((h), (p), "GET", (path), NULL, NULL)

#include "net.h"

#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>

/* connect_timeout fails fast when the PC is off, instead of the ~75 s OS default. */
static int connect_timeout(int s, const struct sockaddr *addr, socklen_t len, int ms) {
    int fl = fcntl(s, F_GETFL, 0);
    fcntl(s, F_SETFL, fl | O_NONBLOCK);
    int r = connect(s, addr, len);
    if (r < 0 && errno == EINPROGRESS) {
        fd_set w;
        FD_ZERO(&w);
        FD_SET(s, &w);
        struct timeval tv = {.tv_sec = ms / 1000, .tv_usec = (ms % 1000) * 1000};
        r = select(s + 1, NULL, &w, NULL, &tv) == 1 ? 0 : -1;
        if (r == 0) {
            int err = 0;
            socklen_t l = sizeof err;
            getsockopt(s, SOL_SOCKET, SO_ERROR, &err, &l);
            if (err) r = -1;
        }
    }
    fcntl(s, F_SETFL, fl);
    return r;
}

static int tcp_connect(const char *host, int port) {
    char portstr[8];
    snprintf(portstr, sizeof portstr, "%d", port);
    struct addrinfo hints = {.ai_family = AF_INET, .ai_socktype = SOCK_STREAM}, *res;
    if (getaddrinfo(host, portstr, &hints, &res) != 0) return -1;
    int s = -1;
    for (struct addrinfo *a = res; a; a = a->ai_next) {
        s = socket(a->ai_family, a->ai_socktype, a->ai_protocol);
        if (s < 0) continue;
        if (connect_timeout(s, a->ai_addr, a->ai_addrlen, 3000) == 0) break;
        close(s);
        s = -1;
    }
    freeaddrinfo(res);
    if (s >= 0) {
        /* a game page asks the PC to look at the PS5 over FTP, which can take a few seconds */
        struct timeval tv = {.tv_sec = 30};
        setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    }
    return s;
}

char gs_error[512];

static char *request(const char *host, int port, const char *method, const char *path, const char *json_body,
                     size_t *len, char *err, size_t errlen) {
    if (err) err[0] = 0;
    int s = tcp_connect(host, port);
    if (s < 0) return NULL;

    char req[2048];
    int n = snprintf(req, sizeof req, "%s %s HTTP/1.0\r\nHost: %s:%d\r\nX-GameShop-Client: ps5\r\nConnection: close\r\n",
                     method, path, host, port);
    size_t blen = json_body ? strlen(json_body) : 0;
    if (json_body)
        n += snprintf(req + n, sizeof req - n, "Content-Type: application/json\r\nContent-Length: %zu\r\n", blen);
    n += snprintf(req + n, sizeof req - n, "\r\n");
    if (send(s, req, n, 0) < 0 || (blen && send(s, json_body, blen, 0) < 0)) {
        close(s);
        return NULL;
    }

    size_t cap = 65536, used = 0;
    char *buf = malloc(cap);
    while (buf) {
        if (used + 16384 > cap) {
            char *g = realloc(buf, cap *= 2);
            if (!g) {
                free(buf);
                buf = NULL;
                break;
            }
            buf = g;
        }
        ssize_t r = recv(s, buf + used, cap - used - 1, 0);
        if (r <= 0) break;
        used += r;
    }
    close(s);
    if (!buf) return NULL;
    buf[used] = 0;

    int status = 0;
    sscanf(buf, "HTTP/%*d.%*d %d", &status);
    char *body = strstr(buf, "\r\n\r\n");
    if (!body || status < 200 || status >= 300) {
        fprintf(stderr, "gs_http: %s %s -> HTTP %d\n", method, path, status);
        if (err) snprintf(err, errlen, "%s", body ? body + 4 : "");
        free(buf);
        return NULL;
    }
    body += 4;
    size_t bl = used - (body - buf);
    memmove(buf, body, bl + 1);
    if (len) *len = bl;
    return buf;
}

char *gs_http(const char *host, int port, const char *method, const char *path, const char *json_body, size_t *len) {
    return request(host, port, method, path, json_body, len, gs_error, sizeof gs_error);
}

char *gs_fetch(const char *host, int port, const char *path, size_t *len) {
    return request(host, port, "GET", path, NULL, len, NULL, 0);
}

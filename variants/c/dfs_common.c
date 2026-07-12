/* ==========================================================================
 * dfs_common.c — Implementação da camada comum (sockets, RPC, base64,
 * placement, chunking, utilidades). Portável Windows (Winsock) / POSIX.
 * ========================================================================== */
#include "dfs_common.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <ctype.h>
#include <pthread.h>

#ifdef _WIN32
  #include <ws2tcpip.h>
  #include <direct.h>
  #include <windows.h>
  #define MKDIR(p) _mkdir(p)
#else
  #include <sys/socket.h>
  #include <sys/types.h>
  #include <sys/stat.h>
  #include <netinet/in.h>
  #include <arpa/inet.h>
  #include <netdb.h>
  #include <unistd.h>
  #include <time.h>
  #define MKDIR(p) mkdir(p, 0777)
#endif

/* ---- Config: membership canônica ---------------------------------------- */
int build_nodes(node_info *out, int max) {
    int n = NODE_COUNT < max ? NODE_COUNT : max;
    for (int i = 0; i < n; i++) {
        snprintf(out[i].id, sizeof(out[i].id), "node%d", i + 1);
        snprintf(out[i].host, sizeof(out[i].host), "%s", NODE_HOST);
        out[i].port = BASE_NODE_PORT + i;
        snprintf(out[i].dir, sizeof(out[i].dir), "data/nodes/node%d", i + 1);
    }
    return n;
}

/* ---- Sockets ------------------------------------------------------------ */
void net_init(void) {
#ifdef _WIN32
    WSADATA wsa;
    WSAStartup(MAKEWORD(2, 2), &wsa);
#endif
}

void sock_close(sock_t s) {
#ifdef _WIN32
    closesocket(s);
#else
    close(s);
#endif
}

sock_t tcp_connect(const char *host, int port) {
    struct addrinfo hints, *res = NULL;
    char portstr[16];
    snprintf(portstr, sizeof(portstr), "%d", port);
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(host, portstr, &hints, &res) != 0) return DFS_INVALID_SOCK;

    sock_t s = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (s == DFS_INVALID_SOCK) { freeaddrinfo(res); return DFS_INVALID_SOCK; }
    if (connect(s, res->ai_addr, (int)res->ai_addrlen) != 0) {
        sock_close(s);
        freeaddrinfo(res);
        return DFS_INVALID_SOCK;
    }
    freeaddrinfo(res);
    return s;
}

static int send_all(sock_t s, const char *buf, size_t len) {
    size_t sent = 0;
    while (sent < len) {
        int n = send(s, buf + sent, (int)(len - sent), 0);
        if (n <= 0) return -1;
        sent += (size_t)n;
    }
    return 0;
}

int send_line(sock_t s, const char *line) {
    if (send_all(s, line, strlen(line)) != 0) return -1;
    return send_all(s, "\n", 1);
}

char *recv_line(sock_t s) {
    size_t cap = 4096, len = 0;
    char *buf = malloc(cap);
    char tmp[8192];
    for (;;) {
        int r = recv(s, tmp, sizeof(tmp), 0);
        if (r <= 0) {
            if (len == 0) { free(buf); return NULL; }
            break;
        }
        for (int i = 0; i < r; i++) {
            if (tmp[i] == '\n') { buf[len] = '\0'; return buf; }
            if (len + 1 >= cap) { cap *= 2; buf = realloc(buf, cap); }
            buf[len++] = tmp[i];
        }
    }
    buf[len] = '\0';
    return buf;
}

json_value *rpc_request(const char *host, int port, json_value *payload) {
    sock_t s = tcp_connect(host, port);
    if (s == DFS_INVALID_SOCK) return NULL;
    char *line = json_serialize(payload);
    int ok = send_line(s, line);
    free(line);
    if (ok != 0) { sock_close(s); return NULL; }
    char *resp = recv_line(s);
    sock_close(s);
    if (!resp) return NULL;
    json_value *v = json_parse(resp);
    free(resp);
    return v;
}

/* Servidor: uma thread por conexão. */
typedef struct { sock_t sock; rpc_handler handler; } conn_ctx;

static void *conn_thread(void *arg) {
    conn_ctx *ctx = (conn_ctx *)arg;
    char *line = recv_line(ctx->sock);
    if (line) {
        json_value *req = json_parse(line);
        free(line);
        json_value *resp = NULL;
        if (req) resp = ctx->handler(req);
        if (!resp) {
            resp = json_new_obj();
            json_obj_set(resp, "ok", json_new_bool(0));
            json_obj_set(resp, "error", json_new_str("requisição inválida"));
        }
        char *out = json_serialize(resp);
        send_line(ctx->sock, out);
        free(out);
        json_free(resp);
        json_free(req);
    }
    sock_close(ctx->sock);
    free(ctx);
    return NULL;
}

void serve(const char *host, int port, rpc_handler handler) {
    (void)host;
    sock_t srv = socket(AF_INET, SOCK_STREAM, 0);
    int yes = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, (const char *)&yes, sizeof(yes));
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = inet_addr(host);
    addr.sin_port = htons((unsigned short)port);
    if (bind(srv, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        fprintf(stderr, "erro: bind na porta %d falhou\n", port);
        exit(1);
    }
    listen(srv, 64);
    for (;;) {
        sock_t c = accept(srv, NULL, NULL);
        if (c == DFS_INVALID_SOCK) continue;
        conn_ctx *ctx = malloc(sizeof(conn_ctx));
        ctx->sock = c;
        ctx->handler = handler;
        pthread_t t;
        pthread_create(&t, NULL, conn_thread, ctx);
        pthread_detach(t);
    }
}

/* ---- Base64 ------------------------------------------------------------- */
static const char B64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

char *b64_encode(const unsigned char *data, size_t len) {
    size_t olen = 4 * ((len + 2) / 3);
    char *out = malloc(olen + 1);
    size_t i, o = 0;
    for (i = 0; i + 2 < len; i += 3) {
        unsigned v = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2];
        out[o++] = B64[(v >> 18) & 63];
        out[o++] = B64[(v >> 12) & 63];
        out[o++] = B64[(v >> 6) & 63];
        out[o++] = B64[v & 63];
    }
    if (i < len) {
        unsigned v = data[i] << 16;
        int rem = (int)(len - i);
        if (rem == 2) v |= data[i + 1] << 8;
        out[o++] = B64[(v >> 18) & 63];
        out[o++] = B64[(v >> 12) & 63];
        out[o++] = (rem == 2) ? B64[(v >> 6) & 63] : '=';
        out[o++] = '=';
    }
    out[o] = '\0';
    return out;
}

static int b64_val(char c) {
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= 'a' && c <= 'z') return c - 'a' + 26;
    if (c >= '0' && c <= '9') return c - '0' + 52;
    if (c == '+') return 62;
    if (c == '/') return 63;
    return -1;
}

unsigned char *b64_decode(const char *text, size_t *out_len) {
    size_t tlen = strlen(text);
    unsigned char *out = malloc(tlen / 4 * 3 + 4);
    size_t o = 0;
    int quad[4], qi = 0;
    for (size_t i = 0; i < tlen; i++) {
        char c = text[i];
        if (c == '=' || isspace((unsigned char)c)) {
            if (c == '=') { quad[qi++] = -2; if (qi == 4) goto flush; }
            continue;
        }
        int v = b64_val(c);
        if (v < 0) continue;
        quad[qi++] = v;
        if (qi == 4) {
        flush:
            {
                int b0 = quad[0], b1 = quad[1], b2 = quad[2], b3 = quad[3];
                out[o++] = (unsigned char)((b0 << 2) | (b1 >> 4));
                if (b2 != -2) out[o++] = (unsigned char)((b1 << 4) | (b2 >> 2));
                if (b3 != -2 && b2 != -2) out[o++] = (unsigned char)((b2 << 6) | b3);
                qi = 0;
            }
        }
    }
    *out_len = o;
    return out;
}

/* ---- Placement ---------------------------------------------------------- */
int replicas_for_chunk(int chunk_index, int n, int replication_factor, int *out_idx) {
    if (n <= 0) return 0;
    int r = replication_factor < n ? replication_factor : n;
    for (int off = 0; off < r; off++)
        out_idx[off] = (chunk_index + off) % n;
    return r;
}

int node_id_cmp(const char *a, const char *b) {
    /* Ordena pelo sufixo numérico: "node2" < "node10". */
    const char *pa = a, *pb = b;
    while (*pa && !isdigit((unsigned char)*pa)) pa++;
    while (*pb && !isdigit((unsigned char)*pb)) pb++;
    if (*pa && *pb) {
        long na = strtol(pa, NULL, 10), nb = strtol(pb, NULL, 10);
        if (na != nb) return na < nb ? -1 : 1;
    }
    return strcmp(a, b);
}

/* ---- Chunking ----------------------------------------------------------- */
long choose_chunk_size(long file_size, int cluster_size) {
    if (file_size <= 0) return MIN_CHUNK_SIZE;
    long candidate = file_size / ((long)cluster_size * CHUNK_TARGET_MULTIPLIER);
    if (file_size >= (long)cluster_size * MIN_CHUNK_SIZE) {
        long alt = file_size / cluster_size;
        if (alt < candidate) candidate = alt;
    }
    if (candidate < MIN_CHUNK_SIZE) candidate = MIN_CHUNK_SIZE;
    if (candidate > MAX_CHUNK_SIZE) candidate = MAX_CHUNK_SIZE;
    return candidate;
}

/* ---- Utilidades --------------------------------------------------------- */
double now_seconds(void) {
#ifdef _WIN32
    return (double)GetTickCount64() / 1000.0;
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
#endif
}

void sleep_seconds(int s) {
#ifdef _WIN32
    Sleep((DWORD)s * 1000);
#else
    sleep((unsigned)s);
#endif
}

void ensure_dir(const char *path) {
    char tmp[1024];
    snprintf(tmp, sizeof(tmp), "%s", path);
    for (char *p = tmp + 1; *p; p++) {
        if (*p == '/' || *p == '\\') {
            char c = *p;
            *p = '\0';
            MKDIR(tmp);
            *p = c;
        }
    }
    MKDIR(tmp);
}

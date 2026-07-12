/* ==========================================================================
 * node.c — Nó de armazenamento (plano de dados) da variante C.
 *
 * Espelha storage_node.py + data_service.py + local_storage.py. Papéis:
 * armazenador, réplica, gateway/primary (fan-out com quórum) e emissor de
 * heartbeat com block report. Recebe do coordenador a lista de órfãos (GC).
 *
 * Uso: node <node_id> <port> <storage_dir>
 * ========================================================================== */
#include "dfs_common.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <pthread.h>

#ifdef _WIN32
  #include <windows.h>
#else
  #include <dirent.h>
#endif

/* Identidade do nó (file-scope: um processo = um nó). */
static char g_node_id[32];
static char g_host[64];
static int  g_port;
static char g_chunks_dir[600];

static void node_log(const char *msg) { printf("[%s] %s\n", g_node_id, msg); fflush(stdout); }

/* ---- I/O de arquivos ---------------------------------------------------- */
static unsigned char *read_file(const char *path, size_t *len) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    unsigned char *buf = malloc(sz > 0 ? sz : 1);
    size_t rd = fread(buf, 1, sz, f);
    fclose(f);
    *len = rd;
    return buf;
}

static int write_file(const char *path, const unsigned char *data, size_t len) {
    FILE *f = fopen(path, "wb");
    if (!f) return -1;
    fwrite(data, 1, len, f);
    fclose(f);
    return 0;
}

static void chunk_path(char *out, size_t n, const char *chunk_id) {
    snprintf(out, n, "%s/%s", g_chunks_dir, chunk_id);
}

/* Lista os chunks locais como um array JSON de strings. */
static json_value *local_chunks(void) {
    json_value *arr = json_new_arr();
#ifdef _WIN32
    char pattern[700];
    snprintf(pattern, sizeof(pattern), "%s/*", g_chunks_dir);
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(pattern, &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY))
                json_arr_add(arr, json_new_str(fd.cFileName));
        } while (FindNextFileA(h, &fd));
        FindClose(h);
    }
#else
    DIR *d = opendir(g_chunks_dir);
    if (d) {
        struct dirent *e;
        while ((e = readdir(d))) {
            if (e->d_name[0] == '.') continue;
            json_arr_add(arr, json_new_str(e->d_name));
        }
        closedir(d);
    }
#endif
    return arr;
}

/* ---- Handlers ----------------------------------------------------------- */
static json_value *resp_ok(void) {
    json_value *r = json_new_obj();
    json_obj_set(r, "ok", json_new_bool(1));
    return r;
}
static json_value *resp_err(const char *msg) {
    json_value *r = json_new_obj();
    json_obj_set(r, "ok", json_new_bool(0));
    json_obj_set(r, "error", json_new_str(msg));
    return r;
}

static json_value *handle_store(json_value *req) {
    const char *chunk_id = json_get_str(req, "chunk_id");
    const char *b64 = json_get_str(req, "data");
    size_t len;
    unsigned char *data = b64_decode(b64, &len);
    char path[700];
    chunk_path(path, sizeof(path), chunk_id);
    write_file(path, data, len);
    free(data);

    if (!json_get_bool(req, "primary")) {
        json_value *r = resp_ok();
        json_value *arr = json_new_arr();
        json_arr_add(arr, json_new_str(g_node_id));
        json_obj_set(r, "stored_on", arr);
        return r;
    }

    /* Papel de primary: fan-out às demais réplicas, exigindo quórum. */
    json_value *stored = json_new_arr();
    json_arr_add(stored, json_new_str(g_node_id));
    int count = 1;
    json_value *fanout = json_get(req, "fanout");
    if (fanout && fanout->type == JSON_ARR) {
        for (size_t i = 0; i < fanout->nitems; i++) {
            json_value *rp = fanout->items[i];
            const char *rid = json_get_str(rp, "node_id");
            if (strcmp(rid, g_node_id) == 0) continue;
            json_value *sreq = json_new_obj();
            json_obj_set(sreq, "op", json_new_str("STORE"));
            json_obj_set(sreq, "chunk_id", json_new_str(chunk_id));
            json_obj_set(sreq, "data", json_new_str(b64));
            json_obj_set(sreq, "primary", json_new_bool(0));
            json_value *sresp = rpc_request(json_get_str(rp, "host"), (int)json_get_int(rp, "port"), sreq);
            json_free(sreq);
            if (sresp && json_get_bool(sresp, "ok")) {
                json_arr_add(stored, json_new_str(rid));
                count++;
            } else {
                char m[128];
                snprintf(m, sizeof(m), "fan-out falhou p/ %s", rid);
                node_log(m);
            }
            json_free(sresp);
        }
    }

    json_value *r = json_new_obj();
    json_obj_set(r, "ok", json_new_bool(count >= WRITE_QUORUM));
    if (count < WRITE_QUORUM) {
        char m[96];
        snprintf(m, sizeof(m), "quorum nao atingido (%d/%d)", count, WRITE_QUORUM);
        json_obj_set(r, "error", json_new_str(m));
    }
    json_obj_set(r, "stored_on", stored);
    return r;
}

static json_value *handle_fetch(json_value *req) {
    char path[700];
    chunk_path(path, sizeof(path), json_get_str(req, "chunk_id"));
    size_t len;
    unsigned char *data = read_file(path, &len);
    if (!data) return resp_err("chunk ausente");
    char *b64 = b64_encode(data, len);
    free(data);
    json_value *r = resp_ok();
    json_value *s = json_new_str(b64);
    free(b64);
    json_obj_set(r, "data", s);
    return r;
}

static json_value *handle_delete(json_value *req) {
    char path[700];
    chunk_path(path, sizeof(path), json_get_str(req, "chunk_id"));
    remove(path);
    return resp_ok();
}

static json_value *handle_replicate(json_value *req) {
    const char *chunk_id = json_get_str(req, "chunk_id");
    char path[700];
    chunk_path(path, sizeof(path), chunk_id);
    size_t len;
    unsigned char *data = read_file(path, &len);
    if (!data) return resp_err("fonte nao tem o chunk");
    char *b64 = b64_encode(data, len);
    free(data);

    json_value *target = json_get(req, "target");
    json_value *sreq = json_new_obj();
    json_obj_set(sreq, "op", json_new_str("STORE"));
    json_obj_set(sreq, "chunk_id", json_new_str(chunk_id));
    json_value *ds = json_new_str(b64);
    free(b64);
    json_obj_set(sreq, "data", ds);
    json_obj_set(sreq, "primary", json_new_bool(0));
    json_value *sresp = rpc_request(json_get_str(target, "host"), (int)json_get_int(target, "port"), sreq);
    json_free(sreq);

    int ok = sresp && json_get_bool(sresp, "ok");
    json_free(sresp);
    if (ok) {
        char m[128];
        snprintf(m, sizeof(m), "re-replicou %s -> %s", chunk_id, json_get_str(target, "node_id"));
        node_log(m);
        json_value *r = resp_ok();
        json_obj_set(r, "target", json_new_str(json_get_str(target, "node_id")));
        return r;
    }
    return resp_err("destino recusou");
}

static json_value *node_handler(json_value *req) {
    const char *op = json_get_str(req, "op");
    if (strcmp(op, "PING") == 0) {
        json_value *r = resp_ok();
        json_obj_set(r, "node_id", json_new_str(g_node_id));
        return r;
    }
    if (strcmp(op, "STORE") == 0)     return handle_store(req);
    if (strcmp(op, "FETCH") == 0)     return handle_fetch(req);
    if (strcmp(op, "DELETE") == 0)    return handle_delete(req);
    if (strcmp(op, "REPLICATE") == 0) return handle_replicate(req);
    if (strcmp(op, "LIST") == 0) {
        json_value *r = resp_ok();
        json_obj_set(r, "chunks", local_chunks());
        return r;
    }
    return resp_err("op desconhecida");
}

/* ---- Heartbeat + GC ----------------------------------------------------- */
static void register_with_retry(void) {
    for (int i = 0; i < 10; i++) {
        json_value *req = json_new_obj();
        json_obj_set(req, "op", json_new_str("REGISTER"));
        json_obj_set(req, "node_id", json_new_str(g_node_id));
        json_obj_set(req, "host", json_new_str(g_host));
        json_obj_set(req, "port", json_new_num(g_port));
        json_value *resp = rpc_request(COORD_HOST, COORD_PORT, req);
        json_free(req);
        if (resp) { json_free(resp); node_log("registrado no coordenador"); return; }
        sleep_seconds(1);
    }
    node_log("nao consegui registrar (coordenador fora do ar?)");
}

static void *heartbeat_thread(void *arg) {
    (void)arg;
    for (;;) {
        sleep_seconds(HEARTBEAT_INTERVAL);
        json_value *req = json_new_obj();
        json_obj_set(req, "op", json_new_str("HEARTBEAT"));
        json_obj_set(req, "node_id", json_new_str(g_node_id));
        json_obj_set(req, "chunks", local_chunks());
        json_value *resp = rpc_request(COORD_HOST, COORD_PORT, req);
        json_free(req);
        if (!resp) { node_log("heartbeat falhou"); continue; }
        json_value *del = json_get(resp, "delete");
        if (del && del->type == JSON_ARR) {
            for (size_t i = 0; i < del->nitems; i++) {
                if (del->items[i]->type != JSON_STR) continue;
                char path[700];
                chunk_path(path, sizeof(path), del->items[i]->str);
                if (remove(path) == 0) {
                    char m[128];
                    snprintf(m, sizeof(m), "GC apagou orfao %s", del->items[i]->str);
                    node_log(m);
                }
            }
        }
        json_free(resp);
    }
    return NULL;
}

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr, "uso: node <node_id> <port> <storage_dir>\n");
        return 1;
    }
    net_init();
    snprintf(g_node_id, sizeof(g_node_id), "%s", argv[1]);
    snprintf(g_host, sizeof(g_host), "%s", NODE_HOST);
    g_port = atoi(argv[2]);
    snprintf(g_chunks_dir, sizeof(g_chunks_dir), "%s/chunks", argv[3]);
    ensure_dir(g_chunks_dir);

    register_with_retry();
    pthread_t hb;
    pthread_create(&hb, NULL, heartbeat_thread, NULL);
    pthread_detach(hb);

    char m[700];
    snprintf(m, sizeof(m), "no no ar em %s:%d (dir=%s)", g_host, g_port, g_chunks_dir);
    node_log(m);
    serve(NODE_HOST, g_port, node_handler);
    return 0;
}

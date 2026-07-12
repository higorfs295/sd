/* ==========================================================================
 * coordinator.c — Coordenador (plano de controle) da variante C.
 *
 * Espelha server.py + node_registry.py + metadata_service.py +
 * replication_watcher.py. Mantém metadados (JSON em disco), registro de nós com
 * máquina de estados de vivacidade (ALIVE/SUSPECT/DEAD), placement
 * determinístico, supervisor de re-replicação e garbage collection.
 * Estado global protegido por um mutex (o handler é um ponteiro de função).
 * ========================================================================== */
#include "dfs_common.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <pthread.h>

#define MAXN 64
#define METADATA_FILE "data/metadata/metadata_index.json"

/* ---- Estado global ------------------------------------------------------ */
typedef struct { char id[32]; char host[64]; int port; double last_hb; } noderec;

static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static noderec g_nodes[MAXN];
static int g_nnodes = 0;
static char g_membership[MAXN][32];   /* ordenada por sufixo numérico */
static int g_nmembership = 0;
static char g_prev_state[MAXN][16];   /* estado anterior por posição de membership */
static json_value *g_meta = NULL;     /* { "files": { ... } } */

/* pending deletes e suspected orphans como listas simples */
typedef struct { char node[32]; char chunk[80]; } pair_t;
static pair_t g_pending[4096]; static int g_npending = 0;
typedef struct { char node[32]; char chunk[80]; int count; } suspect_t;
static suspect_t g_suspect[4096]; static int g_nsuspect = 0;

static void coord_log(const char *msg) { printf("[coordenador] %s\n", msg); fflush(stdout); }

/* ---- Registro de nós ---------------------------------------------------- */
static noderec *find_node(const char *id) {
    for (int i = 0; i < g_nnodes; i++)
        if (strcmp(g_nodes[i].id, id) == 0) return &g_nodes[i];
    return NULL;
}
static noderec *find_or_add_node(const char *id, const char *host, int port) {
    noderec *n = find_node(id);
    if (n) return n;
    n = &g_nodes[g_nnodes++];
    snprintf(n->id, sizeof(n->id), "%s", id);
    snprintf(n->host, sizeof(n->host), "%s", host);
    n->port = port;
    n->last_hb = 0;
    return n;
}
static int in_membership(const char *id) {
    for (int i = 0; i < g_nmembership; i++)
        if (strcmp(g_membership[i], id) == 0) return 1;
    return 0;
}
static void sort_membership(void) {
    for (int i = 0; i < g_nmembership; i++)
        for (int j = i + 1; j < g_nmembership; j++)
            if (node_id_cmp(g_membership[i], g_membership[j]) > 0) {
                char t[32]; strcpy(t, g_membership[i]);
                strcpy(g_membership[i], g_membership[j]);
                strcpy(g_membership[j], t);
            }
}

/* Estado de vivacidade (cálculo preguiçoso). Chamar com o lock tomado. */
static const char *state_of(const char *id) {
    noderec *n = find_node(id);
    if (!n || n->last_hb == 0) return "DEAD";
    double silence = now_seconds() - n->last_hb;
    if (silence < HEARTBEAT_SUSPECT) return "ALIVE";
    if (silence < HEARTBEAT_DEAD) return "SUSPECT";
    return "DEAD";
}
static int is_dead(const char *id) { return strcmp(state_of(id), "DEAD") == 0; }

static json_value *addr_json(const char *id) {
    noderec *n = find_node(id);
    json_value *o = json_new_obj();
    json_obj_set(o, "node_id", json_new_str(id));
    json_obj_set(o, "host", json_new_str(n ? n->host : NODE_HOST));
    json_obj_set(o, "port", json_new_num(n ? n->port : 0));
    return o;
}

/* ---- Metadados ---------------------------------------------------------- */
static json_value *meta_files(void) { return json_get(g_meta, "files"); }

static void persist_meta(void) {
    char *s = json_serialize(g_meta);
    FILE *f = fopen(METADATA_FILE, "wb");
    if (f) { fwrite(s, 1, strlen(s), f); fclose(f); }
    free(s);
}
static void load_meta(void) {
    FILE *f = fopen(METADATA_FILE, "rb");
    if (f) {
        fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
        char *buf = malloc(sz + 1); fread(buf, 1, sz, f); buf[sz] = '\0'; fclose(f);
        g_meta = json_parse(buf);
        free(buf);
    }
    if (!g_meta || g_meta->type != JSON_OBJ) { json_free(g_meta); g_meta = NULL; }
    if (!g_meta) { g_meta = json_new_obj(); json_obj_set(g_meta, "files", json_new_obj()); }
    if (!meta_files()) json_obj_set(g_meta, "files", json_new_obj());
}

/* chunk que os metadados esperam do nó (para GC). */
static int node_expects_chunk(const char *node_id, const char *chunk_id) {
    json_value *files = meta_files();
    for (size_t i = 0; i < files->nmembers; i++) {
        json_value *entry = files->members[i].val;
        json_value *chunks = json_get(entry, "chunks");
        if (!chunks) continue;
        for (size_t c = 0; c < chunks->nitems; c++) {
            if (strcmp(json_get_str(chunks->items[c], "chunk_id"), chunk_id) != 0) continue;
            json_value *reps = json_get(chunks->items[c], "replicas");
            for (size_t r = 0; r < reps->nitems; r++)
                if (strcmp(json_get_str(reps->items[r], "node_id"), node_id) == 0) return 1;
        }
    }
    return 0;
}

/* ---- Handlers ----------------------------------------------------------- */
static json_value *resp_err(const char *m) {
    json_value *r = json_new_obj();
    json_obj_set(r, "ok", json_new_bool(0));
    json_obj_set(r, "error", json_new_str(m));
    return r;
}

static json_value *handle_register(json_value *req) {
    const char *id = json_get_str(req, "node_id");
    pthread_mutex_lock(&g_lock);
    noderec *n = find_or_add_node(id, json_get_str(req, "host"), (int)json_get_int(req, "port"));
    snprintf(n->host, sizeof(n->host), "%s", json_get_str(req, "host"));
    n->port = (int)json_get_int(req, "port");
    n->last_hb = now_seconds();
    if (!in_membership(id)) { snprintf(g_membership[g_nmembership++], 32, "%s", id); sort_membership(); }
    int msize = g_nmembership;
    pthread_mutex_unlock(&g_lock);

    char m[96]; snprintf(m, sizeof(m), "no %s registrado (membership=%d)", id, msize);
    coord_log(m);
    json_value *r = json_new_obj();
    json_obj_set(r, "ok", json_new_bool(1));
    json_obj_set(r, "cluster_size", json_new_num(msize));
    return r;
}

static json_value *handle_heartbeat(json_value *req) {
    const char *id = json_get_str(req, "node_id");
    json_value *chunks = json_get(req, "chunks");
    json_value *to_delete = json_new_arr();

    pthread_mutex_lock(&g_lock);
    noderec *n = find_or_add_node(id, NODE_HOST, 0);
    n->last_hb = now_seconds();

    /* Deleções pendentes (DELETE que falhou enquanto o nó estava morto). */
    for (int i = 0; i < g_npending; ) {
        if (strcmp(g_pending[i].node, id) == 0) {
            json_arr_add(to_delete, json_new_str(g_pending[i].chunk));
            g_pending[i] = g_pending[--g_npending];
        } else i++;
    }

    /* Órfãos por block report, confirmados em 2 ciclos consecutivos. */
    if (chunks && chunks->type == JSON_ARR) {
        /* zera suspeitas deste nó que não são mais órfãs */
        for (int i = 0; i < g_nsuspect; ) {
            if (strcmp(g_suspect[i].node, id) != 0) { i++; continue; }
            int still = 0;
            for (size_t c = 0; c < chunks->nitems; c++)
                if (chunks->items[c]->type == JSON_STR &&
                    strcmp(chunks->items[c]->str, g_suspect[i].chunk) == 0 &&
                    !node_expects_chunk(id, g_suspect[i].chunk)) { still = 1; break; }
            if (!still) g_suspect[i] = g_suspect[--g_nsuspect];
            else i++;
        }
        for (size_t c = 0; c < chunks->nitems; c++) {
            if (chunks->items[c]->type != JSON_STR) continue;
            const char *cid = chunks->items[c]->str;
            if (node_expects_chunk(id, cid)) continue; /* legítimo */
            /* órfão: incrementa contagem */
            suspect_t *sp = NULL;
            for (int k = 0; k < g_nsuspect; k++)
                if (strcmp(g_suspect[k].node, id) == 0 && strcmp(g_suspect[k].chunk, cid) == 0) { sp = &g_suspect[k]; break; }
            if (!sp && g_nsuspect < 4096) { sp = &g_suspect[g_nsuspect++]; snprintf(sp->node, 32, "%s", id); snprintf(sp->chunk, 80, "%s", cid); sp->count = 0; }
            if (sp) { sp->count++; if (sp->count >= 2) json_arr_add(to_delete, json_new_str(cid)); }
        }
    }
    pthread_mutex_unlock(&g_lock);

    json_value *r = json_new_obj();
    json_obj_set(r, "ok", json_new_bool(1));
    json_obj_set(r, "delete", to_delete);
    return r;
}

static json_value *handle_request_upload(json_value *req) {
    long size = json_get_int(req, "size");
    char upload_id[32];
    snprintf(upload_id, sizeof(upload_id), "up_%08x%04x", (unsigned)(now_seconds() * 1000) ^ rand(), rand() & 0xffff);

    pthread_mutex_lock(&g_lock);
    int n = g_nmembership;
    long chunk_size = choose_chunk_size(size, n);
    int num_chunks = size <= 0 ? 1 : (int)((size + chunk_size - 1) / chunk_size);
    if (num_chunks == 0) num_chunks = 1;

    json_value *chunks_arr = json_new_arr();
    int insufficient = -1;
    for (int i = 0; i < num_chunks; i++) {
        int idx[REPLICATION_FACTOR];
        int r = replicas_for_chunk(i, n, REPLICATION_FACTOR, idx);
        int live = 0;
        json_value *reps = json_new_arr();
        for (int k = 0; k < r; k++) {
            const char *rid = g_membership[idx[k]];
            if (!is_dead(rid)) live++;
            json_arr_add(reps, addr_json(rid));
        }
        if (live < WRITE_QUORUM) { insufficient = i; json_free(reps); break; }
        json_value *c = json_new_obj();
        json_obj_set(c, "index", json_new_num(i));
        char cid[80]; snprintf(cid, sizeof(cid), "%s_chunk_%d", upload_id, i);
        json_obj_set(c, "chunk_id", json_new_str(cid));
        json_obj_set(c, "replicas", reps);
        json_arr_add(chunks_arr, c);
    }
    pthread_mutex_unlock(&g_lock);

    if (insufficient >= 0) {
        json_free(chunks_arr);
        char m[96]; snprintf(m, sizeof(m), "replicas vivas insuficientes p/ quorum no chunk %d", insufficient);
        return resp_err(m);
    }

    char m[128]; snprintf(m, sizeof(m), "upload %s p/ %s: %d chunk(s) de %ld B", upload_id, json_get_str(req, "path"), num_chunks, chunk_size);
    coord_log(m);
    json_value *r = json_new_obj();
    json_obj_set(r, "ok", json_new_bool(1));
    json_obj_set(r, "upload_id", json_new_str(upload_id));
    json_obj_set(r, "chunk_size", json_new_num(chunk_size));
    json_obj_set(r, "num_chunks", json_new_num(num_chunks));
    json_obj_set(r, "chunks", chunks_arr);
    return r;
}

/* clona um json_value (para copiar replicas do request para os metadados) */
static json_value *json_clone(const json_value *v) {
    if (!v) return json_new_null();
    switch (v->type) {
        case JSON_NULL: return json_new_null();
        case JSON_BOOL: return json_new_bool(v->boolean);
        case JSON_NUM: return json_new_num(v->num);
        case JSON_STR: return json_new_str(v->str);
        case JSON_ARR: {
            json_value *a = json_new_arr();
            for (size_t i = 0; i < v->nitems; i++) json_arr_add(a, json_clone(v->items[i]));
            return a;
        }
        case JSON_OBJ: {
            json_value *o = json_new_obj();
            for (size_t i = 0; i < v->nmembers; i++) json_obj_set(o, v->members[i].key, json_clone(v->members[i].val));
            return o;
        }
    }
    return json_new_null();
}

static json_value *handle_confirm_upload(json_value *req) {
    const char *path = json_get_str(req, "path");
    json_value *in_chunks = json_get(req, "chunks");

    pthread_mutex_lock(&g_lock);
    json_value *entry = json_new_obj();
    json_obj_set(entry, "num_chunks", json_new_num(in_chunks ? (double)in_chunks->nitems : 0));
    json_obj_set(entry, "chunk_size", json_new_num(json_get_int(req, "chunk_size")));
    json_obj_set(entry, "created_at", json_new_num(now_seconds()));
    json_value *chunks = json_new_arr();
    if (in_chunks) for (size_t i = 0; i < in_chunks->nitems; i++) json_arr_add(chunks, json_clone(in_chunks->items[i]));
    json_obj_set(entry, "chunks", chunks);
    json_obj_set(meta_files(), path, entry);
    persist_meta();
    pthread_mutex_unlock(&g_lock);

    char m[128]; snprintf(m, sizeof(m), "arquivo %s confirmado nos metadados", path);
    coord_log(m);
    json_value *r = json_new_obj();
    json_obj_set(r, "ok", json_new_bool(1));
    return r;
}

static json_value *handle_request_download(json_value *req) {
    const char *path = json_get_str(req, "path");
    pthread_mutex_lock(&g_lock);
    json_value *entry = json_get(meta_files(), path);
    if (!entry) { pthread_mutex_unlock(&g_lock); return resp_err("arquivo nao encontrado"); }

    json_value *out_chunks = json_new_arr();
    json_value *chunks = json_get(entry, "chunks");
    for (size_t c = 0; c < chunks->nitems; c++) {
        json_value *ch = chunks->items[c];
        json_value *reps = json_get(ch, "replicas");
        json_value *live = json_new_arr();
        for (size_t r = 0; r < reps->nitems; r++)
            if (!is_dead(json_get_str(reps->items[r], "node_id")))
                json_arr_add(live, json_clone(reps->items[r]));
        json_value *chosen = live;
        if (live->nitems == 0) { json_free(live); chosen = json_new_arr(); for (size_t r = 0; r < reps->nitems; r++) json_arr_add(chosen, json_clone(reps->items[r])); }
        json_value *oc = json_new_obj();
        json_obj_set(oc, "index", json_new_num(json_get_int(ch, "index")));
        json_obj_set(oc, "chunk_id", json_new_str(json_get_str(ch, "chunk_id")));
        json_obj_set(oc, "replicas", chosen);
        json_arr_add(out_chunks, oc);
    }
    long num = json_get_int(entry, "num_chunks");
    pthread_mutex_unlock(&g_lock);

    json_value *r = json_new_obj();
    json_obj_set(r, "ok", json_new_bool(1));
    json_obj_set(r, "num_chunks", json_new_num(num));
    json_obj_set(r, "chunks", out_chunks);
    return r;
}

static json_value *handle_delete_file(json_value *req) {
    const char *path = json_get_str(req, "path");
    /* Coleta as réplicas sob lock; faz as chamadas de rede fora do lock. */
    pthread_mutex_lock(&g_lock);
    json_value *entry = json_get(meta_files(), path);
    if (!entry) { pthread_mutex_unlock(&g_lock); return resp_err("arquivo nao encontrado"); }
    json_value *snapshot = json_clone(json_get(entry, "chunks"));
    pthread_mutex_unlock(&g_lock);

    for (size_t c = 0; c < snapshot->nitems; c++) {
        json_value *reps = json_get(snapshot->items[c], "replicas");
        const char *cid = json_get_str(snapshot->items[c], "chunk_id");
        for (size_t r = 0; r < reps->nitems; r++) {
            const char *rid = json_get_str(reps->items[r], "node_id");
            int dead;
            pthread_mutex_lock(&g_lock); dead = is_dead(rid); pthread_mutex_unlock(&g_lock);
            if (dead) {
                pthread_mutex_lock(&g_lock);
                if (g_npending < 4096) { snprintf(g_pending[g_npending].node, 32, "%s", rid); snprintf(g_pending[g_npending].chunk, 80, "%s", cid); g_npending++; }
                pthread_mutex_unlock(&g_lock);
            } else {
                json_value *dreq = json_new_obj();
                json_obj_set(dreq, "op", json_new_str("DELETE"));
                json_obj_set(dreq, "chunk_id", json_new_str(cid));
                json_value *dr = rpc_request(json_get_str(reps->items[r], "host"), (int)json_get_int(reps->items[r], "port"), dreq);
                json_free(dreq);
                if (!dr) { pthread_mutex_lock(&g_lock); if (g_npending < 4096) { snprintf(g_pending[g_npending].node, 32, "%s", rid); snprintf(g_pending[g_npending].chunk, 80, "%s", cid); g_npending++; } pthread_mutex_unlock(&g_lock); }
                json_free(dr);
            }
        }
    }
    json_free(snapshot);

    pthread_mutex_lock(&g_lock);
    json_obj_del(meta_files(), path);
    persist_meta();
    pthread_mutex_unlock(&g_lock);

    char m[128]; snprintf(m, sizeof(m), "arquivo %s removido", path);
    coord_log(m);
    json_value *r = json_new_obj();
    json_obj_set(r, "ok", json_new_bool(1));
    return r;
}

static json_value *handle_list_files(void) {
    pthread_mutex_lock(&g_lock);
    json_value *files = meta_files();
    json_value *arr = json_new_arr();
    for (size_t i = 0; i < files->nmembers; i++) {
        json_value *entry = files->members[i].val;
        json_value *chunks = json_get(entry, "chunks");
        /* nós distintos */
        json_value *nodes = json_new_arr();
        for (size_t c = 0; c < chunks->nitems; c++) {
            json_value *reps = json_get(chunks->items[c], "replicas");
            for (size_t r = 0; r < reps->nitems; r++) {
                const char *rid = json_get_str(reps->items[r], "node_id");
                int seen = 0;
                for (size_t k = 0; k < nodes->nitems; k++) if (strcmp(nodes->items[k]->str, rid) == 0) { seen = 1; break; }
                if (!seen) json_arr_add(nodes, json_new_str(rid));
            }
        }
        json_value *fo = json_new_obj();
        json_obj_set(fo, "path", json_new_str(files->members[i].key));
        json_obj_set(fo, "num_chunks", json_new_num(json_get_int(entry, "num_chunks")));
        json_obj_set(fo, "nodes", nodes);
        json_arr_add(arr, fo);
    }
    pthread_mutex_unlock(&g_lock);
    json_value *r = json_new_obj();
    json_obj_set(r, "ok", json_new_bool(1));
    json_obj_set(r, "files", arr);
    return r;
}

static json_value *handle_status(void) {
    pthread_mutex_lock(&g_lock);
    json_value *arr = json_new_arr();
    for (int i = 0; i < g_nmembership; i++) {
        json_value *o = json_new_obj();
        json_obj_set(o, "node_id", json_new_str(g_membership[i]));
        json_obj_set(o, "state", json_new_str(state_of(g_membership[i])));
        json_arr_add(arr, o);
    }
    int nfiles = (int)meta_files()->nmembers;
    pthread_mutex_unlock(&g_lock);
    json_value *r = json_new_obj();
    json_obj_set(r, "ok", json_new_bool(1));
    json_obj_set(r, "nodes", arr);
    json_obj_set(r, "files", json_new_num(nfiles));
    return r;
}

static json_value *coord_handler(json_value *req) {
    const char *op = json_get_str(req, "op");
    if (strcmp(op, "REGISTER") == 0)         return handle_register(req);
    if (strcmp(op, "HEARTBEAT") == 0)        return handle_heartbeat(req);
    if (strcmp(op, "REQUEST_UPLOAD") == 0)   return handle_request_upload(req);
    if (strcmp(op, "CONFIRM_UPLOAD") == 0)   return handle_confirm_upload(req);
    if (strcmp(op, "REQUEST_DOWNLOAD") == 0) return handle_request_download(req);
    if (strcmp(op, "DELETE_FILE") == 0)      return handle_delete_file(req);
    if (strcmp(op, "LIST_FILES") == 0)       return handle_list_files();
    if (strcmp(op, "STATUS") == 0)           return handle_status();
    return resp_err("op desconhecida");
}

/* ---- Supervisor de re-replicação ---------------------------------------- */
typedef struct { char path[256]; char chunk_id[80]; char source[32]; char target[32]; } rework_t;

static void update_after_replicate(const char *path, const char *chunk_id, const char *target) {
    pthread_mutex_lock(&g_lock);
    json_value *entry = json_get(meta_files(), path);
    if (entry) {
        json_value *chunks = json_get(entry, "chunks");
        for (size_t c = 0; c < chunks->nitems; c++) {
            if (strcmp(json_get_str(chunks->items[c], "chunk_id"), chunk_id) != 0) continue;
            json_value *reps = json_get(chunks->items[c], "replicas");
            json_value *nr = json_new_arr();
            for (size_t r = 0; r < reps->nitems; r++)
                if (!is_dead(json_get_str(reps->items[r], "node_id")))
                    json_arr_add(nr, json_clone(reps->items[r]));
            /* adiciona o destino se ainda não estiver */
            int has = 0;
            for (size_t r = 0; r < nr->nitems; r++) if (strcmp(json_get_str(nr->items[r], "node_id"), target) == 0) has = 1;
            if (!has) json_arr_add(nr, addr_json(target));
            json_obj_set(chunks->items[c], "replicas", nr);
        }
        persist_meta();
    }
    pthread_mutex_unlock(&g_lock);
}

static void *watcher_thread(void *arg) {
    (void)arg;
    for (;;) {
        sleep_seconds(WATCHER_INTERVAL);
        rework_t work[512]; int nwork = 0;

        pthread_mutex_lock(&g_lock);
        /* transições p/ DEAD */
        int any_transition = 0;
        for (int i = 0; i < g_nmembership; i++) {
            const char *cur = state_of(g_membership[i]);
            if (strcmp(cur, "DEAD") == 0 && strcmp(g_prev_state[i], "DEAD") != 0) {
                any_transition = 1;
                char m[96]; snprintf(m, sizeof(m), "detectada MORTE de %s: iniciando re-replicacao", g_membership[i]);
                coord_log(m);
            }
            snprintf(g_prev_state[i], 16, "%s", cur);
        }
        if (any_transition) {
            json_value *files = meta_files();
            for (size_t fi = 0; fi < files->nmembers && nwork < 512; fi++) {
                json_value *entry = files->members[fi].val;
                json_value *chunks = json_get(entry, "chunks");
                for (size_t c = 0; c < chunks->nitems && nwork < 512; c++) {
                    json_value *reps = json_get(chunks->items[c], "replicas");
                    int dead = 0, live = 0; const char *source = NULL;
                    for (size_t r = 0; r < reps->nitems; r++) {
                        const char *rid = json_get_str(reps->items[r], "node_id");
                        if (is_dead(rid)) dead++; else { live++; if (!source) source = rid; }
                    }
                    if (dead == 0 || live == 0) continue;
                    int need = REPLICATION_FACTOR - live;
                    for (int t = 0; t < g_nmembership && need > 0 && nwork < 512; t++) {
                        const char *cand = g_membership[t];
                        if (is_dead(cand)) continue;
                        int already = 0;
                        for (size_t r = 0; r < reps->nitems; r++) if (strcmp(json_get_str(reps->items[r], "node_id"), cand) == 0) already = 1;
                        if (already) continue;
                        snprintf(work[nwork].path, 256, "%s", files->members[fi].key);
                        snprintf(work[nwork].chunk_id, 80, "%s", json_get_str(chunks->items[c], "chunk_id"));
                        snprintf(work[nwork].source, 32, "%s", source);
                        snprintf(work[nwork].target, 32, "%s", cand);
                        nwork++; need--;
                    }
                }
            }
        }
        pthread_mutex_unlock(&g_lock);

        for (int i = 0; i < nwork; i++) {
            json_value *src = addr_json(work[i].source);   /* addr_json lê g_nodes; ok sem lock p/ leitura simples */
            json_value *tgt = addr_json(work[i].target);
            json_value *rreq = json_new_obj();
            json_obj_set(rreq, "op", json_new_str("REPLICATE"));
            json_obj_set(rreq, "chunk_id", json_new_str(work[i].chunk_id));
            json_obj_set(rreq, "target", json_clone(tgt));
            json_value *rr = rpc_request(json_get_str(src, "host"), (int)json_get_int(src, "port"), rreq);
            json_free(rreq); json_free(src); json_free(tgt);
            if (rr && json_get_bool(rr, "ok")) {
                update_after_replicate(work[i].path, work[i].chunk_id, work[i].target);
                char m[160]; snprintf(m, sizeof(m), "chunk %s re-replicado %s -> %s", work[i].chunk_id, work[i].source, work[i].target);
                coord_log(m);
            }
            json_free(rr);
        }
    }
    return NULL;
}

int main(void) {
    net_init();
    srand((unsigned)(now_seconds() * 1000));
    ensure_dir("data/metadata");

    node_info nodes[NODE_COUNT];
    int n = build_nodes(nodes, NODE_COUNT);
    for (int i = 0; i < n; i++) {
        find_or_add_node(nodes[i].id, nodes[i].host, nodes[i].port);
        snprintf(g_membership[g_nmembership++], 32, "%s", nodes[i].id);
        strcpy(g_prev_state[i], "DEAD");
    }
    sort_membership();
    load_meta();

    pthread_t w;
    pthread_create(&w, NULL, watcher_thread, NULL);
    pthread_detach(w);

    char m[160];
    snprintf(m, sizeof(m), "coordenador no ar em %s:%d", COORD_HOST, COORD_PORT);
    coord_log(m);
    snprintf(m, sizeof(m), "membership canonica: %d nos (RF=%d, quorum=%d)", g_nmembership, REPLICATION_FACTOR, WRITE_QUORUM);
    coord_log(m);
    serve(COORD_HOST, COORD_PORT, coord_handler);
    return 0;
}

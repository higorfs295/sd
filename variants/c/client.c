/* ==========================================================================
 * client.c — Interface de linha de comando (CLI) da variante C.
 *
 * Espelha cli.py + client.py. Cliente "fraco": fala controle com o coordenador
 * e dados com os nós. Comandos: put, get, list, rm, status.
 *
 * PUT: REQUEST_UPLOAD -> envia cada chunk ao nó primary (gateway, fan-out com
 *      quórum) -> CONFIRM_UPLOAD.
 * GET (estilo GFS): pede o mapa de chunks e busca cada pedaço numa réplica viva.
 *
 * Uso: client <put|get|list|rm|status> ...
 * ========================================================================== */
#include "dfs_common.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

static json_value *coord(json_value *payload) {
    json_value *r = rpc_request(COORD_HOST, COORD_PORT, payload);
    json_free(payload);
    if (!r) { fprintf(stderr, "erro: sem resposta do coordenador (esta no ar?)\n"); exit(1); }
    return r;
}

static unsigned char *read_all(const char *path, size_t *len) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    unsigned char *buf = malloc(sz > 0 ? sz : 1);
    *len = fread(buf, 1, sz, f);
    fclose(f);
    return buf;
}

static int cmd_put(const char *local, const char *dfs_path) {
    size_t len;
    unsigned char *data = read_all(local, &len);
    if (!data) { fprintf(stderr, "arquivo local nao existe: %s\n", local); return 1; }

    json_value *req = json_new_obj();
    json_obj_set(req, "op", json_new_str("REQUEST_UPLOAD"));
    json_obj_set(req, "path", json_new_str(dfs_path));
    json_obj_set(req, "size", json_new_num((double)len));
    json_value *plan = coord(req);
    if (!json_get_bool(plan, "ok")) { fprintf(stderr, "coordenador recusou: %s\n", json_get_str(plan, "error")); return 1; }

    long chunk_size = json_get_int(plan, "chunk_size");
    json_value *chunks = json_get(plan, "chunks");
    json_value *confirmed = json_new_arr();

    for (size_t i = 0; i < chunks->nitems; i++) {
        json_value *c = chunks->items[i];
        int idx = (int)json_get_int(c, "index");
        const char *chunk_id = json_get_str(c, "chunk_id");
        json_value *reps = json_get(c, "replicas");
        json_value *primary = reps->items[0];

        size_t off = (size_t)idx * (size_t)chunk_size;
        size_t sl = (off < len) ? ((len - off < (size_t)chunk_size) ? (len - off) : (size_t)chunk_size) : 0;
        char *b64 = b64_encode(data + off, sl);

        json_value *sreq = json_new_obj();
        json_obj_set(sreq, "op", json_new_str("STORE"));
        json_obj_set(sreq, "chunk_id", json_new_str(chunk_id));
        json_value *ds = json_new_str(b64); free(b64);
        json_obj_set(sreq, "data", ds);
        json_obj_set(sreq, "primary", json_new_bool(1));
        /* fanout = clone das réplicas */
        json_value *fo = json_new_arr();
        for (size_t r = 0; r < reps->nitems; r++) {
            json_value *rp = json_new_obj();
            json_obj_set(rp, "node_id", json_new_str(json_get_str(reps->items[r], "node_id")));
            json_obj_set(rp, "host", json_new_str(json_get_str(reps->items[r], "host")));
            json_obj_set(rp, "port", json_new_num(json_get_int(reps->items[r], "port")));
            json_arr_add(fo, rp);
        }
        json_obj_set(sreq, "fanout", fo);

        json_value *sresp = rpc_request(json_get_str(primary, "host"), (int)json_get_int(primary, "port"), sreq);
        json_free(sreq);
        if (!sresp || !json_get_bool(sresp, "ok")) {
            fprintf(stderr, "falha ao gravar chunk %d\n", idx);
            json_free(sresp); return 1;
        }
        json_value *stored = json_get(sresp, "stored_on");

        /* confirma só as réplicas que realmente gravaram */
        json_value *actual = json_new_arr();
        printf("  chunk %d: gravado em ", idx);
        for (size_t r = 0; r < reps->nitems; r++) {
            const char *rid = json_get_str(reps->items[r], "node_id");
            int ok = 0;
            for (size_t s = 0; s < stored->nitems; s++) if (strcmp(stored->items[s]->str, rid) == 0) ok = 1;
            if (ok) {
                json_value *rp = json_new_obj();
                json_obj_set(rp, "node_id", json_new_str(rid));
                json_obj_set(rp, "host", json_new_str(json_get_str(reps->items[r], "host")));
                json_obj_set(rp, "port", json_new_num(json_get_int(reps->items[r], "port")));
                json_arr_add(actual, rp);
                printf("%s ", rid);
            }
        }
        printf("\n");

        json_value *cc = json_new_obj();
        json_obj_set(cc, "index", json_new_num(idx));
        json_obj_set(cc, "chunk_id", json_new_str(chunk_id));
        json_obj_set(cc, "replicas", actual);
        json_arr_add(confirmed, cc);
        json_free(sresp);
    }

    json_value *creq = json_new_obj();
    json_obj_set(creq, "op", json_new_str("CONFIRM_UPLOAD"));
    json_obj_set(creq, "path", json_new_str(dfs_path));
    json_obj_set(creq, "chunk_size", json_new_num(chunk_size));
    json_obj_set(creq, "chunks", confirmed);
    json_value *cresp = coord(creq);
    json_free(cresp);

    printf("OK: %s -> %s (%zu chunk(s))\n", local, dfs_path, chunks->nitems);
    json_free(plan);
    free(data);
    return 0;
}

static int cmd_get(const char *dfs_path, const char *local) {
    json_value *req = json_new_obj();
    json_obj_set(req, "op", json_new_str("REQUEST_DOWNLOAD"));
    json_obj_set(req, "path", json_new_str(dfs_path));
    json_value *plan = coord(req);
    if (!json_get_bool(plan, "ok")) { fprintf(stderr, "coordenador: %s\n", json_get_str(plan, "error")); return 1; }

    FILE *out = fopen(local, "wb");
    if (!out) { fprintf(stderr, "nao consegui criar %s\n", local); return 1; }

    json_value *chunks = json_get(plan, "chunks");
    /* os chunks já vêm em ordem de índice do coordenador */
    for (size_t c = 0; c < chunks->nitems; c++) {
        json_value *ch = chunks->items[c];
        json_value *reps = json_get(ch, "replicas");
        int got = 0;
        for (size_t r = 0; r < reps->nitems && !got; r++) {
            json_value *freq = json_new_obj();
            json_obj_set(freq, "op", json_new_str("FETCH"));
            json_obj_set(freq, "chunk_id", json_new_str(json_get_str(ch, "chunk_id")));
            json_value *fr = rpc_request(json_get_str(reps->items[r], "host"), (int)json_get_int(reps->items[r], "port"), freq);
            json_free(freq);
            if (fr && json_get_bool(fr, "ok")) {
                size_t blen;
                unsigned char *bytes = b64_decode(json_get_str(fr, "data"), &blen);
                fwrite(bytes, 1, blen, out);
                free(bytes);
                got = 1;
            }
            json_free(fr);
        }
        if (!got) { fprintf(stderr, "nao consegui obter o chunk %ld de nenhuma replica viva\n", json_get_int(ch, "index")); fclose(out); return 1; }
    }
    fclose(out);
    printf("OK: %s -> %s\n", dfs_path, local);
    json_free(plan);
    return 0;
}

static int cmd_list(void) {
    json_value *req = json_new_obj();
    json_obj_set(req, "op", json_new_str("LIST_FILES"));
    json_value *resp = coord(req);
    json_value *files = json_get(resp, "files");
    if (!files || files->nitems == 0) { printf("(nenhum arquivo)\n"); json_free(resp); return 0; }
    printf("%-30s %6s  NOS\n", "CAMINHO", "CHUNKS");
    for (size_t i = 0; i < files->nitems; i++) {
        json_value *f = files->items[i];
        json_value *nodes = json_get(f, "nodes");
        printf("%-30s %6ld  ", json_get_str(f, "path"), json_get_int(f, "num_chunks"));
        for (size_t k = 0; k < nodes->nitems; k++) printf("%s%s", k ? "," : "", nodes->items[k]->str);
        printf("\n");
    }
    json_free(resp);
    return 0;
}

static int cmd_rm(const char *dfs_path) {
    json_value *req = json_new_obj();
    json_obj_set(req, "op", json_new_str("DELETE_FILE"));
    json_obj_set(req, "path", json_new_str(dfs_path));
    json_value *resp = coord(req);
    if (!json_get_bool(resp, "ok")) { fprintf(stderr, "erro: %s\n", json_get_str(resp, "error")); json_free(resp); return 1; }
    printf("removido: %s\n", dfs_path);
    json_free(resp);
    return 0;
}

static int cmd_status(void) {
    json_value *req = json_new_obj();
    json_obj_set(req, "op", json_new_str("STATUS"));
    json_value *resp = coord(req);
    printf("arquivos: %ld\n", json_get_int(resp, "files"));
    json_value *nodes = json_get(resp, "nodes");
    for (size_t i = 0; i < nodes->nitems; i++)
        printf("  %-8s %s\n", json_get_str(nodes->items[i], "node_id"), json_get_str(nodes->items[i], "state"));
    json_free(resp);
    return 0;
}

int main(int argc, char **argv) {
    net_init();
    if (argc < 2) { fprintf(stderr, "uso: client <put|get|list|rm|status> ...\n"); return 1; }
    const char *cmd = argv[1];
    if (strcmp(cmd, "put") == 0 && argc >= 4)  return cmd_put(argv[2], argv[3]);
    if (strcmp(cmd, "get") == 0 && argc >= 4)  return cmd_get(argv[2], argv[3]);
    if (strcmp(cmd, "list") == 0)              return cmd_list();
    if (strcmp(cmd, "rm") == 0 && argc >= 3)   return cmd_rm(argv[2]);
    if (strcmp(cmd, "status") == 0)            return cmd_status();
    fprintf(stderr, "comando invalido\n");
    return 1;
}

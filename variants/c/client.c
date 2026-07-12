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

static int g_quiet = 0; /* silencia as mensagens "OK:" (usado no benchmark) */

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

/* Clona uma subárvore JSON (para reenviar o plano ao ingress/egress). */
static json_value *json_clone(const json_value *v) {
    if (!v) return json_new_null();
    switch (v->type) {
        case JSON_NULL: return json_new_null();
        case JSON_BOOL: return json_new_bool(v->boolean);
        case JSON_NUM: return json_new_num(v->num);
        case JSON_STR: return json_new_str(v->str);
        case JSON_ARR: { json_value *a = json_new_arr(); for (size_t i = 0; i < v->nitems; i++) json_arr_add(a, json_clone(v->items[i])); return a; }
        case JSON_OBJ: { json_value *o = json_new_obj(); for (size_t i = 0; i < v->nmembers; i++) json_obj_set(o, v->members[i].key, json_clone(v->members[i].val)); return o; }
    }
    return json_new_null();
}

/* PUT (cliente fraco): entrega o arquivo INTEIRO ao INGRESS, que fatia, replica
 * com quórum e confirma ao coordenador. */
static int cmd_put(const char *local, const char *dfs_path) {
    size_t len;
    unsigned char *data = read_all(local, &len);
    if (!data) { fprintf(stderr, "arquivo local nao existe: %s\n", local); return 1; }

    json_value *req = json_new_obj();
    json_obj_set(req, "op", json_new_str("REQUEST_UPLOAD"));
    json_obj_set(req, "path", json_new_str(dfs_path));
    json_obj_set(req, "size", json_new_num((double)len));
    json_value *plan = coord(req);
    if (!json_get_bool(plan, "ok")) { fprintf(stderr, "coordenador recusou: %s\n", json_get_str(plan, "error")); free(data); return 1; }

    json_value *ingress = json_get(plan, "ingress");
    char *b64 = b64_encode(data, len);
    json_value *ureq = json_new_obj();
    json_obj_set(ureq, "op", json_new_str("UPLOAD_FILE"));
    json_obj_set(ureq, "path", json_new_str(dfs_path));
    json_obj_set(ureq, "upload_id", json_new_str(json_get_str(plan, "upload_id")));
    json_obj_set(ureq, "chunk_size", json_new_num(json_get_int(plan, "chunk_size")));
    json_obj_set(ureq, "chunks", json_clone(json_get(plan, "chunks")));
    json_value *ds = json_new_str(b64); free(b64);
    json_obj_set(ureq, "data", ds);

    json_value *uresp = rpc_request(json_get_str(ingress, "host"), (int)json_get_int(ingress, "port"), ureq);
    json_free(ureq);
    free(data);
    if (!uresp || !json_get_bool(uresp, "ok")) {
        fprintf(stderr, "falha no upload (ingress %s): %s\n", json_get_str(ingress, "node_id"), uresp ? json_get_str(uresp, "error") : "sem resposta");
        json_free(uresp); json_free(plan); return 1;
    }
    if (!g_quiet) printf("OK: %s -> %s via ingress %s (%ld chunk(s), %ld B)\n", local, dfs_path,
           json_get_str(ingress, "node_id"), json_get_int(uresp, "chunks_written"), json_get_int(uresp, "bytes"));
    json_free(uresp);
    json_free(plan);
    return 0;
}

/* GET (cliente fraco): pede o arquivo ao EGRESS, que remonta por localidade. */
static int cmd_get(const char *dfs_path, const char *local) {
    json_value *req = json_new_obj();
    json_obj_set(req, "op", json_new_str("REQUEST_DOWNLOAD"));
    json_obj_set(req, "path", json_new_str(dfs_path));
    json_value *plan = coord(req);
    if (!json_get_bool(plan, "ok")) { fprintf(stderr, "coordenador: %s\n", json_get_str(plan, "error")); return 1; }

    json_value *egress = json_get(plan, "egress");
    json_value *dreq = json_new_obj();
    json_obj_set(dreq, "op", json_new_str("DOWNLOAD_FILE"));
    json_obj_set(dreq, "path", json_new_str(dfs_path));
    json_obj_set(dreq, "chunks", json_clone(json_get(plan, "chunks")));
    json_value *dresp = rpc_request(json_get_str(egress, "host"), (int)json_get_int(egress, "port"), dreq);
    json_free(dreq);
    if (!dresp || !json_get_bool(dresp, "ok")) {
        fprintf(stderr, "falha no download (egress %s): %s\n", json_get_str(egress, "node_id"), dresp ? json_get_str(dresp, "error") : "sem resposta");
        json_free(dresp); json_free(plan); return 1;
    }
    size_t blen; unsigned char *bytes = b64_decode(json_get_str(dresp, "data"), &blen);
    FILE *out = fopen(local, "wb");
    if (!out) { fprintf(stderr, "nao consegui criar %s\n", local); free(bytes); json_free(dresp); json_free(plan); return 1; }
    fwrite(bytes, 1, blen, out);
    fclose(out);
    free(bytes);
    if (!g_quiet) printf("OK: %s -> %s via egress %s (%ld B)\n", dfs_path, local, json_get_str(egress, "node_id"), json_get_int(dresp, "bytes"));
    json_free(dresp);
    json_free(plan);
    return 0;
}

static int cmd_list(void) {
    json_value *req = json_new_obj();
    json_obj_set(req, "op", json_new_str("LIST_FILES"));
    json_value *resp = coord(req);
    json_value *files = json_get(resp, "files");
    if (!files || files->nitems == 0) { printf("(nenhum arquivo)\n"); json_free(resp); return 0; }
    printf("%-28s %6s %10s  NOS\n", "CAMINHO", "CHUNKS", "BYTES");
    for (size_t i = 0; i < files->nitems; i++) {
        json_value *f = files->items[i];
        json_value *nodes = json_get(f, "nodes");
        printf("%-28s %6ld %10ld  ", json_get_str(f, "path"), json_get_int(f, "num_chunks"), json_get_int(f, "size"));
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
    if (!g_quiet) printf("removido: %s\n", dfs_path);
    json_free(resp);
    return 0;
}

static int cmd_status(void) {
    json_value *req = json_new_obj();
    json_obj_set(req, "op", json_new_str("STATUS"));
    json_value *resp = coord(req);
    printf("arquivos: %ld | re-replicacoes: %ld | GC: %ld\n",
           json_get_int(resp, "files"), json_get_int(resp, "rereplications"), json_get_int(resp, "gc_deletes"));
    json_value *nodes = json_get(resp, "nodes");
    for (size_t i = 0; i < nodes->nitems; i++)
        printf("  %-8s %s\n", json_get_str(nodes->items[i], "node_id"), json_get_str(nodes->items[i], "state"));
    json_free(resp);
    return 0;
}

static void print_metrics(json_value *resp) {
    printf("arquivos: %ld | re-replicacoes: %ld | GC apagou: %ld\n",
           json_get_int(resp, "files"), json_get_int(resp, "rereplications"), json_get_int(resp, "gc_deletes"));
    json_value *ops = json_get(resp, "ops");
    if (!ops || ops->nmembers == 0) { printf("(sem metricas de operacao ainda)\n"); return; }
    printf("%-10s %6s %10s %10s %10s %12s\n", "OP", "N", "AVG(ms)", "MIN(ms)", "MAX(ms)", "BYTES");
    for (size_t i = 0; i < ops->nmembers; i++) {
        json_value *m = ops->members[i].val;
        printf("%-10s %6ld %10.2f %10.2f %10.2f %12ld\n", ops->members[i].key,
               json_get_int(m, "count"), json_get(m, "avg_ms")->num, json_get(m, "min_ms")->num,
               json_get(m, "max_ms")->num, json_get_int(m, "bytes"));
    }
}

static int cmd_metrics(void) {
    json_value *req = json_new_obj();
    json_obj_set(req, "op", json_new_str("METRICS"));
    json_value *resp = coord(req);
    print_metrics(resp);
    json_free(resp);
    return 0;
}

/* Hub de telemetria ao vivo (espelha telemetry_hub.py): consulta a cada 1s. */
static int cmd_telemetry(void) {
    printf("Hub de telemetria (Ctrl+C para sair). Consultando o coordenador a cada 1s...\n");
    for (;;) {
        json_value *req = json_new_obj();
        json_obj_set(req, "op", json_new_str("METRICS"));
        json_value *resp = rpc_request(COORD_HOST, COORD_PORT, req);
        json_free(req);
        if (resp) { print_metrics(resp); json_free(resp); printf("----\n"); }
        else printf("[telemetria] coordenador indisponivel\n");
        sleep_seconds(1);
    }
    return 0;
}

/* Benchmark: PUT/GET de vários tamanhos; grava benchmark/resultados.csv. */
static int cmd_benchmark(int argc, char **argv) {
    int sizes[16], nsizes = 0, iter = 3;
    for (int i = 2; i < argc; i++) {
        if (strcmp(argv[i], "--iter") == 0 && i + 1 < argc) iter = atoi(argv[++i]);
        else if (strcmp(argv[i], "--sizes") == 0) { while (i + 1 < argc && argv[i + 1][0] >= '0' && argv[i + 1][0] <= '9' && nsizes < 16) sizes[nsizes++] = atoi(argv[++i]); }
    }
    if (nsizes == 0) { sizes[0] = 1; sizes[1] = 2; sizes[2] = 5; nsizes = 3; }

    ensure_dir("benchmark");
    FILE *csv = fopen("benchmark/resultados.csv", "w");
    if (csv) fprintf(csv, "op,size_mb,iter,latency_ms,throughput_mbps\n");
    printf("%-8s %-6s %-4s %12s %12s\n", "OP", "MB", "IT", "LATENCIA_ms", "THRPUT_MBps");

    for (int s = 0; s < nsizes; s++) {
        int mb = sizes[s];
        size_t sz = (size_t)mb * 1024 * 1024;
        unsigned char *buf = malloc(sz);
        for (size_t k = 0; k < sz; k++) buf[k] = (unsigned char)(rand() & 0xff);
        char src[64], dst[64], dpath[64];
        snprintf(src, sizeof(src), "bench_src_%d.bin", mb);
        snprintf(dst, sizeof(dst), "bench_dst_%d.bin", mb);
        snprintf(dpath, sizeof(dpath), "/bench/f%d.bin", mb);
        FILE *f = fopen(src, "wb"); fwrite(buf, 1, sz, f); fclose(f); free(buf);

        g_quiet = 1;
        for (int it = 1; it <= iter; it++) {
            double t0 = now_seconds();
            cmd_put(src, dpath);
            double put_ms = (now_seconds() - t0) * 1000; double put_mbps = mb / (put_ms / 1000.0);
            printf("%-8s %-6d %-4d %12.2f %12.2f\n", "put", mb, it, put_ms, put_mbps);
            if (csv) fprintf(csv, "put,%d,%d,%.2f,%.2f\n", mb, it, put_ms, put_mbps);

            t0 = now_seconds();
            cmd_get(dpath, dst);
            double get_ms = (now_seconds() - t0) * 1000; double get_mbps = mb / (get_ms / 1000.0);
            printf("%-8s %-6d %-4d %12.2f %12.2f\n", "get", mb, it, get_ms, get_mbps);
            if (csv) fprintf(csv, "get,%d,%d,%.2f,%.2f\n", mb, it, get_ms, get_mbps);
        }
        cmd_rm(dpath);
        g_quiet = 0;
        remove(src); remove(dst);
    }
    if (csv) fclose(csv);
    printf("\nCSV gravado em benchmark/resultados.csv\n");
    return 0;
}

/* Integridade ponta a ponta: PUT, GET e compara byte a byte. */
static int cmd_test_integrity(int mb) {
    size_t sz = (size_t)mb * 1024 * 1024;
    unsigned char *buf = malloc(sz);
    for (size_t k = 0; k < sz; k++) buf[k] = (unsigned char)(rand() & 0xff);
    FILE *f = fopen("integ_src.bin", "wb"); fwrite(buf, 1, sz, f); fclose(f);

    printf("arquivo de %d MB\n", mb);
    if (cmd_put("integ_src.bin", "/teste/integridade.bin")) { free(buf); return 1; }
    if (cmd_get("/teste/integridade.bin", "integ_dst.bin")) { free(buf); return 1; }
    cmd_rm("/teste/integridade.bin");

    size_t dlen; unsigned char *got = read_all("integ_dst.bin", &dlen);
    int ok = got && dlen == sz && memcmp(buf, got, sz) == 0;
    free(buf); free(got);
    remove("integ_src.bin"); remove("integ_dst.bin");
    if (ok) { printf("INTEGRIDADE OK: o arquivo baixado e identico ao enviado (byte a byte).\n"); return 0; }
    printf("FALHA DE INTEGRIDADE\n");
    return 1;
}

int main(int argc, char **argv) {
    net_init();
    srand((unsigned)(now_seconds() * 1000));
    if (argc < 2) { fprintf(stderr, "uso: client <put|get|list|rm|status|metrics|telemetry|benchmark|test-integrity> ...\n"); return 1; }
    const char *cmd = argv[1];
    if (strcmp(cmd, "put") == 0 && argc >= 4)  return cmd_put(argv[2], argv[3]);
    if (strcmp(cmd, "get") == 0 && argc >= 4)  return cmd_get(argv[2], argv[3]);
    if (strcmp(cmd, "list") == 0)              return cmd_list();
    if (strcmp(cmd, "rm") == 0 && argc >= 3)   return cmd_rm(argv[2]);
    if (strcmp(cmd, "status") == 0)            return cmd_status();
    if (strcmp(cmd, "metrics") == 0)           return cmd_metrics();
    if (strcmp(cmd, "telemetry") == 0)         return cmd_telemetry();
    if (strcmp(cmd, "benchmark") == 0)         return cmd_benchmark(argc, argv);
    if (strcmp(cmd, "test-integrity") == 0)    return cmd_test_integrity(argc >= 3 ? atoi(argv[2]) : 4);
    fprintf(stderr, "comando invalido\n");
    return 1;
}

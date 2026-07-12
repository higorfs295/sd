/* ==========================================================================
 * dfs_common.h — Camada comum da variante C: configuração, sockets portáveis
 * (Winsock/BSD), transporte JSON-por-linha, base64, placement e chunking.
 *
 * Equivale, no conjunto, a config.py + a camada gRPC + placement.py + chunking.py
 * do projeto original, adaptado para C sem dependências externas.
 * ========================================================================== */
#ifndef DFS_COMMON_H
#define DFS_COMMON_H

#include "json.h"
#include <stddef.h>

/* ---- Parâmetros centrais (espelham config.py) --------------------------- */
#define COORD_HOST "127.0.0.1"
#define COORD_PORT 9100
#define NODE_COUNT 5
#define BASE_NODE_PORT 9101
#define NODE_HOST "127.0.0.1"

#define REPLICATION_FACTOR 3
#define WRITE_QUORUM 2

#define MIN_CHUNK_SIZE (1L * 1024 * 1024)   /* 1 MB  */
#define MAX_CHUNK_SIZE (16L * 1024 * 1024)  /* 16 MB */
#define CHUNK_TARGET_MULTIPLIER 3

#define HEARTBEAT_INTERVAL 2
#define HEARTBEAT_SUSPECT 5
#define HEARTBEAT_DEAD 12
#define WATCHER_INTERVAL 2

/* ---- Descrição de um nó ------------------------------------------------- */
typedef struct {
    char id[32];
    char host[64];
    int  port;
    char dir[512];
} node_info;

/* Preenche `out` (capacidade >= NODE_COUNT) com a membership canônica. */
int build_nodes(node_info *out, int max);

/* ---- Sockets / transporte ---------------------------------------------- */
#ifdef _WIN32
  #include <winsock2.h>
  typedef SOCKET sock_t;
  #define DFS_INVALID_SOCK INVALID_SOCKET
#else
  typedef int sock_t;
  #define DFS_INVALID_SOCK (-1)
#endif

void net_init(void);                              /* WSAStartup no Windows */
sock_t tcp_connect(const char *host, int port);
void   sock_close(sock_t s);

/* Envia uma linha (json + '\n'). Devolve 0 em sucesso. */
int  send_line(sock_t s, const char *line);
/* Lê uma linha terminada por '\n'. Devolve string malloc'd (sem '\n') ou NULL. */
char *recv_line(sock_t s);

/* RPC: conecta, envia payload (não toma posse), lê e parseia a resposta.
 * Devolve json_value* (o chamador dá json_free) ou NULL em erro. */
json_value *rpc_request(const char *host, int port, json_value *payload);

/* Servidor TCP: para cada conexão lê UMA requisição, chama handler e responde.
 * handler recebe o json_value* da requisição (posse do servidor) e devolve um
 * json_value* de resposta (o servidor serializa e dá free). */
typedef json_value *(*rpc_handler)(json_value *req);
void serve(const char *host, int port, rpc_handler handler);

/* ---- Base64 ------------------------------------------------------------- */
char          *b64_encode(const unsigned char *data, size_t len);        /* malloc'd */
unsigned char *b64_decode(const char *text, size_t *out_len);            /* malloc'd */

/* ---- Placement determinístico (round-robin) ----------------------------- */
/* Preenche out_idx[] com os índices (em node_ids ordenado) das R réplicas do
 * chunk. node_ids é a MEMBERSHIP CANÔNICA. Devolve o número de réplicas. */
int replicas_for_chunk(int chunk_index, int n, int replication_factor, int *out_idx);

/* Compara node ids por sufixo numérico ("node2" < "node10"). */
int node_id_cmp(const char *a, const char *b);

/* ---- Chunking adaptável (espelha chunking.py) --------------------------- */
long choose_chunk_size(long file_size, int cluster_size);

/* ---- Utilidades --------------------------------------------------------- */
double now_seconds(void);
void   sleep_seconds(int s);
void   ensure_dir(const char *path);

#endif

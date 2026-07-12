/* ==========================================================================
 * json.h — Mini-parser/serializador JSON (variante C).
 *
 * O projeto original usa gRPC/Protobuf. Nesta variante em C, as mensagens
 * trafegam como JSON por linha sobre TCP, então precisamos de uma camada JSON
 * mínima porém correta: objetos, arrays, strings, números e booleanos, com
 * suporte a aninhamento (necessário para as listas de réplicas e de chunks).
 * ========================================================================== */
#ifndef DFS_JSON_H
#define DFS_JSON_H

#include <stddef.h>

typedef enum { JSON_NULL, JSON_BOOL, JSON_NUM, JSON_STR, JSON_ARR, JSON_OBJ } json_type;

typedef struct json_value json_value;
typedef struct { char *key; json_value *val; } json_member;

struct json_value {
    json_type type;
    int boolean;               /* JSON_BOOL */
    double num;                /* JSON_NUM  */
    char *str;                 /* JSON_STR (owned) */
    json_value **items;        /* JSON_ARR */
    size_t nitems;
    json_member *members;      /* JSON_OBJ */
    size_t nmembers;
};

/* Parsing */
json_value *json_parse(const char *text);      /* NULL em erro */
void json_free(json_value *v);

/* Serialização (string malloc'd; o chamador dá free) */
char *json_serialize(const json_value *v);

/* Construtores */
json_value *json_new_obj(void);
json_value *json_new_arr(void);
json_value *json_new_str(const char *s);
json_value *json_new_num(double n);
json_value *json_new_bool(int b);
json_value *json_new_null(void);

/* Mutação */
void json_obj_set(json_value *obj, const char *key, json_value *val); /* substitui se a chave existir; assume posse de val */
void json_obj_del(json_value *obj, const char *key);                  /* remove a chave se existir */
void json_arr_add(json_value *arr, json_value *val);                  /* assume posse de val */

/* Acesso */
json_value *json_get(const json_value *obj, const char *key);        /* ou NULL */
const char *json_get_str(const json_value *obj, const char *key);    /* ou "" */
long        json_get_int(const json_value *obj, const char *key);    /* ou 0  */
int         json_get_bool(const json_value *obj, const char *key);   /* ou 0  */

#endif

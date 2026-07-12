/* ==========================================================================
 * json.c — Implementação do mini-JSON. Recursivo-descendente, sem dependências.
 * ========================================================================== */
#include "json.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <ctype.h>

/* ---- Construtores ------------------------------------------------------- */
static json_value *jv_new(json_type t) {
    json_value *v = calloc(1, sizeof(json_value));
    v->type = t;
    return v;
}
json_value *json_new_obj(void)  { return jv_new(JSON_OBJ); }
json_value *json_new_arr(void)  { return jv_new(JSON_ARR); }
json_value *json_new_num(double n) { json_value *v = jv_new(JSON_NUM); v->num = n; return v; }
json_value *json_new_bool(int b)   { json_value *v = jv_new(JSON_BOOL); v->boolean = b ? 1 : 0; return v; }
json_value *json_new_null(void) { return jv_new(JSON_NULL); }
json_value *json_new_str(const char *s) {
    json_value *v = jv_new(JSON_STR);
    v->str = strdup(s ? s : "");
    return v;
}

void json_obj_set(json_value *obj, const char *key, json_value *val) {
    /* Substitui o valor se a chave já existir (evita duplicatas em metadados). */
    for (size_t i = 0; i < obj->nmembers; i++) {
        if (strcmp(obj->members[i].key, key) == 0) {
            json_free(obj->members[i].val);
            obj->members[i].val = val;
            return;
        }
    }
    obj->members = realloc(obj->members, (obj->nmembers + 1) * sizeof(json_member));
    obj->members[obj->nmembers].key = strdup(key);
    obj->members[obj->nmembers].val = val;
    obj->nmembers++;
}

void json_obj_del(json_value *obj, const char *key) {
    for (size_t i = 0; i < obj->nmembers; i++) {
        if (strcmp(obj->members[i].key, key) == 0) {
            free(obj->members[i].key);
            json_free(obj->members[i].val);
            for (size_t j = i + 1; j < obj->nmembers; j++)
                obj->members[j - 1] = obj->members[j];
            obj->nmembers--;
            return;
        }
    }
}
void json_arr_add(json_value *arr, json_value *val) {
    arr->items = realloc(arr->items, (arr->nitems + 1) * sizeof(json_value *));
    arr->items[arr->nitems++] = val;
}

/* ---- Liberação ---------------------------------------------------------- */
void json_free(json_value *v) {
    if (!v) return;
    switch (v->type) {
        case JSON_STR: free(v->str); break;
        case JSON_ARR:
            for (size_t i = 0; i < v->nitems; i++) json_free(v->items[i]);
            free(v->items);
            break;
        case JSON_OBJ:
            for (size_t i = 0; i < v->nmembers; i++) {
                free(v->members[i].key);
                json_free(v->members[i].val);
            }
            free(v->members);
            break;
        default: break;
    }
    free(v);
}

/* ---- Acesso ------------------------------------------------------------- */
json_value *json_get(const json_value *obj, const char *key) {
    if (!obj || obj->type != JSON_OBJ) return NULL;
    for (size_t i = 0; i < obj->nmembers; i++)
        if (strcmp(obj->members[i].key, key) == 0) return obj->members[i].val;
    return NULL;
}
const char *json_get_str(const json_value *obj, const char *key) {
    json_value *v = json_get(obj, key);
    return (v && v->type == JSON_STR) ? v->str : "";
}
long json_get_int(const json_value *obj, const char *key) {
    json_value *v = json_get(obj, key);
    return (v && v->type == JSON_NUM) ? (long)v->num : 0;
}
int json_get_bool(const json_value *obj, const char *key) {
    json_value *v = json_get(obj, key);
    return (v && v->type == JSON_BOOL) ? v->boolean : 0;
}

/* ---- Parser ------------------------------------------------------------- */
typedef struct { const char *p; } parser;

static void skip_ws(parser *ps) {
    while (*ps->p && (*ps->p == ' ' || *ps->p == '\t' || *ps->p == '\n' || *ps->p == '\r')) ps->p++;
}

static json_value *parse_value(parser *ps);

static char *parse_string_raw(parser *ps) {
    if (*ps->p != '"') return NULL;
    ps->p++;
    size_t cap = 16, len = 0;
    char *out = malloc(cap);
    while (*ps->p && *ps->p != '"') {
        char c = *ps->p++;
        if (c == '\\') {
            char e = *ps->p++;
            switch (e) {
                case 'n': c = '\n'; break;
                case 't': c = '\t'; break;
                case 'r': c = '\r'; break;
                case 'b': c = '\b'; break;
                case 'f': c = '\f'; break;
                case '/': c = '/'; break;
                case '\\': c = '\\'; break;
                case '"': c = '"'; break;
                case 'u': {
                    /* Suporte básico: interpreta \uXXXX apenas para ASCII (< 0x80). */
                    char hex[5] = {0};
                    for (int i = 0; i < 4 && *ps->p; i++) hex[i] = *ps->p++;
                    long cp = strtol(hex, NULL, 16);
                    c = (char)(cp & 0x7f);
                    break;
                }
                default: c = e; break;
            }
        }
        if (len + 1 >= cap) { cap *= 2; out = realloc(out, cap); }
        out[len++] = c;
    }
    if (*ps->p == '"') ps->p++;
    out[len] = '\0';
    return out;
}

static json_value *parse_string(parser *ps) {
    char *s = parse_string_raw(ps);
    if (!s) return NULL;
    json_value *v = jv_new(JSON_STR);
    v->str = s;
    return v;
}

static json_value *parse_number(parser *ps) {
    char *end;
    double d = strtod(ps->p, &end);
    if (end == ps->p) return NULL;
    ps->p = end;
    return json_new_num(d);
}

static json_value *parse_array(parser *ps) {
    ps->p++; /* [ */
    json_value *arr = json_new_arr();
    skip_ws(ps);
    if (*ps->p == ']') { ps->p++; return arr; }
    while (*ps->p) {
        json_value *item = parse_value(ps);
        if (!item) { json_free(arr); return NULL; }
        json_arr_add(arr, item);
        skip_ws(ps);
        if (*ps->p == ',') { ps->p++; skip_ws(ps); continue; }
        if (*ps->p == ']') { ps->p++; return arr; }
        json_free(arr); return NULL;
    }
    json_free(arr); return NULL;
}

static json_value *parse_object(parser *ps) {
    ps->p++; /* { */
    json_value *obj = json_new_obj();
    skip_ws(ps);
    if (*ps->p == '}') { ps->p++; return obj; }
    while (*ps->p) {
        skip_ws(ps);
        char *key = parse_string_raw(ps);
        if (!key) { json_free(obj); return NULL; }
        skip_ws(ps);
        if (*ps->p != ':') { free(key); json_free(obj); return NULL; }
        ps->p++;
        skip_ws(ps);
        json_value *val = parse_value(ps);
        if (!val) { free(key); json_free(obj); return NULL; }
        obj->members = realloc(obj->members, (obj->nmembers + 1) * sizeof(json_member));
        obj->members[obj->nmembers].key = key;
        obj->members[obj->nmembers].val = val;
        obj->nmembers++;
        skip_ws(ps);
        if (*ps->p == ',') { ps->p++; continue; }
        if (*ps->p == '}') { ps->p++; return obj; }
        json_free(obj); return NULL;
    }
    json_free(obj); return NULL;
}

static json_value *parse_value(parser *ps) {
    skip_ws(ps);
    char c = *ps->p;
    if (c == '"') return parse_string(ps);
    if (c == '{') return parse_object(ps);
    if (c == '[') return parse_array(ps);
    if (c == '-' || (c >= '0' && c <= '9')) return parse_number(ps);
    if (strncmp(ps->p, "true", 4) == 0)  { ps->p += 4; return json_new_bool(1); }
    if (strncmp(ps->p, "false", 5) == 0) { ps->p += 5; return json_new_bool(0); }
    if (strncmp(ps->p, "null", 4) == 0)  { ps->p += 4; return json_new_null(); }
    return NULL;
}

json_value *json_parse(const char *text) {
    parser ps = { text };
    json_value *v = parse_value(&ps);
    return v;
}

/* ---- Serialização ------------------------------------------------------- */
typedef struct { char *buf; size_t len, cap; } sbuf;

static void sb_ensure(sbuf *s, size_t extra) {
    if (s->len + extra + 1 > s->cap) {
        while (s->len + extra + 1 > s->cap) s->cap = s->cap ? s->cap * 2 : 256;
        s->buf = realloc(s->buf, s->cap);
    }
}
static void sb_putc(sbuf *s, char c) { sb_ensure(s, 1); s->buf[s->len++] = c; }
static void sb_puts(sbuf *s, const char *str) {
    size_t n = strlen(str);
    sb_ensure(s, n);
    memcpy(s->buf + s->len, str, n);
    s->len += n;
}
static void sb_put_escaped(sbuf *s, const char *str) {
    sb_putc(s, '"');
    for (const char *p = str; *p; p++) {
        unsigned char c = (unsigned char)*p;
        switch (c) {
            case '"':  sb_puts(s, "\\\""); break;
            case '\\': sb_puts(s, "\\\\"); break;
            case '\n': sb_puts(s, "\\n"); break;
            case '\t': sb_puts(s, "\\t"); break;
            case '\r': sb_puts(s, "\\r"); break;
            case '\b': sb_puts(s, "\\b"); break;
            case '\f': sb_puts(s, "\\f"); break;
            default:
                if (c < 0x20) { char tmp[8]; snprintf(tmp, sizeof(tmp), "\\u%04x", c); sb_puts(s, tmp); }
                else sb_putc(s, (char)c);
        }
    }
    sb_putc(s, '"');
}

static void serialize_into(sbuf *s, const json_value *v) {
    if (!v) { sb_puts(s, "null"); return; }
    switch (v->type) {
        case JSON_NULL: sb_puts(s, "null"); break;
        case JSON_BOOL: sb_puts(s, v->boolean ? "true" : "false"); break;
        case JSON_NUM: {
            char tmp[64];
            if (v->num == (double)(long long)v->num)
                snprintf(tmp, sizeof(tmp), "%lld", (long long)v->num);
            else
                snprintf(tmp, sizeof(tmp), "%.10g", v->num);
            sb_puts(s, tmp);
            break;
        }
        case JSON_STR: sb_put_escaped(s, v->str); break;
        case JSON_ARR:
            sb_putc(s, '[');
            for (size_t i = 0; i < v->nitems; i++) {
                if (i) sb_putc(s, ',');
                serialize_into(s, v->items[i]);
            }
            sb_putc(s, ']');
            break;
        case JSON_OBJ:
            sb_putc(s, '{');
            for (size_t i = 0; i < v->nmembers; i++) {
                if (i) sb_putc(s, ',');
                sb_put_escaped(s, v->members[i].key);
                sb_putc(s, ':');
                serialize_into(s, v->members[i].val);
            }
            sb_putc(s, '}');
            break;
    }
}

char *json_serialize(const json_value *v) {
    sbuf s = {0};
    serialize_into(&s, v);
    sb_ensure(&s, 1);
    s.buf[s.len] = '\0';
    return s.buf;
}

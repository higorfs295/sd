#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <time.h>
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <process.h>

#pragma comment(lib, "Ws2_32.lib")

#define BUFFER_SIZE 512
#define MAX_NAME 64
#define MAX_CLIENTS 64

typedef struct {
    SOCKET sock;
    char name[MAX_NAME];
} ClientInfo;

ClientInfo g_clients[MAX_CLIENTS];
int g_client_count = 0;
CRITICAL_SECTION g_clients_lock;

static void trim_crlf(char *s) {
    size_t len = strlen(s);
    while (len > 0 && (s[len - 1] == '\n' || s[len - 1] == '\r')) {
        s[len - 1] = '\0';
        len--;
    }
}

static void timestamp(char *out, size_t out_size) {
    time_t now = time(NULL);
    struct tm tm_now;
    localtime_s(&tm_now, &now);
    strftime(out, out_size, "%Y-%m-%d %H:%M:%S", &tm_now);
}

static int send_all(SOCKET sock, const char *data, int len) {
    int total = 0;

    while (total < len) {
        int sent = send(sock, data + total, len - total, 0);
        if (sent == SOCKET_ERROR || sent == 0) {
            return SOCKET_ERROR;
        }
        total += sent;
    }

    return total;
}

static int send_text(SOCKET sock, const char *text) {
    return send_all(sock, text, (int)strlen(text));
}

static int recv_line(SOCKET sock, char *buffer, int size) {
    int total = 0;
    char c;

    while (total < size - 1) {
        int r = recv(sock, &c, 1, 0);
        if (r <= 0) {
            if (total == 0) return -1;
            break;
        }

        if (c == '\n') break;
        if (c != '\r') buffer[total++] = c;
    }

    buffer[total] = '\0';
    return total;
}

static int name_in_use_locked(const char *name) {
    for (int i = 0; i < g_client_count; i++) {
        if (strcmp(g_clients[i].name, name) == 0) {
            return 1;
        }
    }
    return 0;
}

static int add_client(SOCKET sock, const char *name) {
    int ok = 0;

    EnterCriticalSection(&g_clients_lock);

    if (g_client_count < MAX_CLIENTS && !name_in_use_locked(name)) {
        g_clients[g_client_count].sock = sock;
        strncpy(g_clients[g_client_count].name, name, MAX_NAME - 1);
        g_clients[g_client_count].name[MAX_NAME - 1] = '\0';
        g_client_count++;
        ok = 1;
    }

    LeaveCriticalSection(&g_clients_lock);
    return ok;
}

static void remove_client(SOCKET sock) {
    EnterCriticalSection(&g_clients_lock);

    for (int i = 0; i < g_client_count; i++) {
        if (g_clients[i].sock == sock) {
            g_clients[i] = g_clients[g_client_count - 1];
            g_client_count--;
            break;
        }
    }

    LeaveCriticalSection(&g_clients_lock);
}

static void get_online_list(char *out, size_t out_size) {
    out[0] = '\0';

    EnterCriticalSection(&g_clients_lock);
    for (int i = 0; i < g_client_count; i++) {
        if (i > 0) {
            strncat(out, " ", out_size - strlen(out) - 1);
        }
        strncat(out, g_clients[i].name, out_size - strlen(out) - 1);
    }
    LeaveCriticalSection(&g_clients_lock);
}

static void broadcast(const char *message) {
    SOCKET sockets[MAX_CLIENTS];
    int count = 0;

    EnterCriticalSection(&g_clients_lock);
    for (int i = 0; i < g_client_count; i++) {
        sockets[count++] = g_clients[i].sock;
    }
    LeaveCriticalSection(&g_clients_lock);

    for (int i = 0; i < count; i++) {
        send_text(sockets[i], message);
        send_text(sockets[i], "\n");
    }
}

static void server_log(const char *fmt, ...) {
    va_list args;
    va_start(args, fmt);
    vprintf(fmt, args);
    printf("\n");
    fflush(stdout);
    va_end(args);
}

void init_chat_state(void) {
    InitializeCriticalSection(&g_clients_lock);
}

void cleanup_chat_state(void) {
    DeleteCriticalSection(&g_clients_lock);
}

unsigned __stdcall client_thread(void *arg) {
    SOCKET client_sock = *(SOCKET *)arg;
    free(arg);

    char username[MAX_NAME];
    char buffer[BUFFER_SIZE];
    char message[BUFFER_SIZE + MAX_NAME + 64];
    char online[BUFFER_SIZE];
    char timebuf[32];

    int n = recv_line(client_sock, username, sizeof(username));
    if (n <= 0) {
        closesocket(client_sock);
        return 0;
    }

    trim_crlf(username);

    if (username[0] == '\0') {
        send_text(client_sock, "Servidor: nome de usuario vazio.\n");
        closesocket(client_sock);
        return 0;
    }

    if (!add_client(client_sock, username)) {
        send_text(client_sock, "Servidor: nome ja em uso ou sala cheia.\n");
        closesocket(client_sock);
        return 0;
    }

    get_online_list(online, sizeof(online));

    snprintf(message, sizeof(message), "Servidor: bem-vindo, %s!\n", username);
    send_text(client_sock, message);

    snprintf(message, sizeof(message), "Servidor: usuarios online agora -> %s\n", online[0] ? online : "(ninguem)");
    send_text(client_sock, message);

    timestamp(timebuf, sizeof(timebuf));
    server_log("[%s] %s entrou no chat.", timebuf, username);

    snprintf(message, sizeof(message), "Servidor: [%s] entrou no chat.", username);
    broadcast(message);

    while (1) {
        n = recv_line(client_sock, buffer, sizeof(buffer));
        if (n <= 0) {
            break;
        }

        trim_crlf(buffer);

        if (buffer[0] == '\0') {
            continue;
        }

        if (strcmp(buffer, "/quit") == 0) {
            break;
        }

        timestamp(timebuf, sizeof(timebuf));
        snprintf(message, sizeof(message), "[%s] %s: %s", timebuf, username, buffer);

        server_log("%s", message);
        broadcast(message);
    }

    remove_client(client_sock);

    timestamp(timebuf, sizeof(timebuf));
    server_log("[%s] %s saiu do chat.", timebuf, username);

    snprintf(message, sizeof(message), "Servidor: [%s] saiu do chat.", username);
    broadcast(message);

    closesocket(client_sock);
    return 0;
}
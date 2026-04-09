#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <process.h>
#include <winsock2.h>
#include <ws2tcpip.h>

#pragma comment(lib, "Ws2_32.lib")

#define HOST "127.0.0.1"
#define PORT 9090
#define BUFFER_SIZE 512

static SOCKET g_sock = INVALID_SOCKET;

unsigned __stdcall receiver_thread(void *arg) {
    SOCKET sock = *(SOCKET *)arg;
    char buffer[BUFFER_SIZE];

    while (1) {
        int received = recv(sock, buffer, BUFFER_SIZE - 1, 0);
        if (received > 0) {
            buffer[received] = '\0';
            printf("%s", buffer);
            fflush(stdout);
        } else {
            break;
        }
    }

    return 0;
}

int main(void) {
    WSADATA wsa;
    SOCKET sock = INVALID_SOCKET;
    struct sockaddr_in server_addr;
    unsigned thread_id;
    HANDLE thread_handle;
    char line[BUFFER_SIZE];

    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        fprintf(stderr, "WSAStartup falhou.\n");
        return 1;
    }

    sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (sock == INVALID_SOCKET) {
        fprintf(stderr, "Erro ao criar socket.\n");
        WSACleanup();
        return 1;
    }

    memset(&server_addr, 0, sizeof(server_addr));
    server_addr.sin_family = AF_INET;
    server_addr.sin_port = htons(PORT);
    server_addr.sin_addr.s_addr = inet_addr(HOST);

    printf("Conectando ao servidor em %s:%d...\n", HOST, PORT);

    if (connect(sock, (struct sockaddr *)&server_addr, sizeof(server_addr)) == SOCKET_ERROR) {
        fprintf(stderr, "Erro ao conectar.\n");
        closesocket(sock);
        WSACleanup();
        return 1;
    }

    g_sock = sock;

    thread_handle = (HANDLE)_beginthreadex(NULL, 0, receiver_thread, &g_sock, 0, &thread_id);
    if (thread_handle == NULL) {
        fprintf(stderr, "Erro ao criar thread de recepção.\n");
        closesocket(sock);
        WSACleanup();
        return 1;
    }

    printf("Digite mensagens. Ctrl+C para sair.\n");

    while (fgets(line, sizeof(line), stdin)) {
        send(sock, line, (int)strlen(line), 0);
    }

    shutdown(sock, SD_BOTH);
    closesocket(sock);

    WaitForSingleObject(thread_handle, INFINITE);
    CloseHandle(thread_handle);

    WSACleanup();
    printf("Conexão encerrada.\n");
    return 0;
}
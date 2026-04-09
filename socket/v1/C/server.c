#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <winsock2.h>
#include <ws2tcpip.h>

#pragma comment(lib, "Ws2_32.lib")

#define PORT 9090
#define BUFFER_SIZE 512

int main(void) {
    WSADATA wsa;
    SOCKET server_sock = INVALID_SOCKET;
    SOCKET client_sock = INVALID_SOCKET;
    struct sockaddr_in server_addr, client_addr;
    int client_len = sizeof(client_addr);
    char buffer[BUFFER_SIZE];

    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        fprintf(stderr, "WSAStartup falhou.\n");
        return 1;
    }

    server_sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (server_sock == INVALID_SOCKET) {
        fprintf(stderr, "Erro ao criar socket.\n");
        WSACleanup();
        return 1;
    }

    memset(&server_addr, 0, sizeof(server_addr));
    server_addr.sin_family = AF_INET;
    server_addr.sin_addr.s_addr = INADDR_ANY;
    server_addr.sin_port = htons(PORT);

    if (bind(server_sock, (struct sockaddr *)&server_addr, sizeof(server_addr)) == SOCKET_ERROR) {
        fprintf(stderr, "Erro no bind.\n");
        closesocket(server_sock);
        WSACleanup();
        return 1;
    }

    if (listen(server_sock, SOMAXCONN) == SOCKET_ERROR) {
        fprintf(stderr, "Erro no listen.\n");
        closesocket(server_sock);
        WSACleanup();
        return 1;
    }

    printf("Escutando a porta %d...\n", PORT);

    while (1) {
        client_sock = accept(server_sock, (struct sockaddr *)&client_addr, &client_len);
        if (client_sock == INVALID_SOCKET) {
            fprintf(stderr, "Erro no accept.\n");
            continue;
        }

        printf("Cliente conectado.\n");

        while (1) {
            int received = recv(client_sock, buffer, BUFFER_SIZE - 1, 0);
            if (received > 0) {
                buffer[received] = '\0';
                printf("Recebido: %s", buffer);

                send(client_sock, buffer, received, 0);
            } else if (received == 0) {
                printf("Cliente desconectou.\n");
                break;
            } else {
                printf("Erro no recv.\n");
                break;
            }
        }

        closesocket(client_sock);
    }

    closesocket(server_sock);
    WSACleanup();
    return 0;
}
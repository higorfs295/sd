#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <winsock2.h>
#include <ws2tcpip.h>
#include <process.h> // Necessário para _beginthreadex

#pragma comment(lib, "Ws2_32.lib")

#define PORT 9090
#define BUFFER_SIZE 512

// Função que cada Thread irá executar
unsigned __stdcall client_thread(void *arg) {
    SOCKET client_sock = *(SOCKET *)arg;
    free(arg); // Libera o ponteiro alocado no accept
    char buffer[BUFFER_SIZE];

    printf("[+] Cliente conectado.\n");

    while (1) {
        int received = recv(client_sock, buffer, BUFFER_SIZE - 1, 0);
        if (received > 0) {
            buffer[received] = '\0';
            printf("Recebido: %s", buffer);
            send(client_sock, buffer, received, 0); // Echo
        } else if (received == 0) {
            printf("[-] Conexão com o cliente caiu.\n");
            break;
        } else {
            printf("[-] Erro no recv ou cliente forçou a queda.\n");
            break;
        }
    }

    closesocket(client_sock);
    return 0;
}

int main(void) {
    WSADATA wsa;
    SOCKET server_sock = INVALID_SOCKET;
    struct sockaddr_in server_addr, client_addr;
    int client_len = sizeof(client_addr);

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

    printf("Escutando a porta %d (Modo Concorrente)...\n", PORT);

    while (1) {
        // Aloca espaço na memória para o socket de cada cliente
        SOCKET *new_sock = malloc(sizeof(SOCKET)); 
        *new_sock = accept(server_sock, (struct sockaddr *)&client_addr, &client_len);
        
        if (*new_sock == INVALID_SOCKET) {
            fprintf(stderr, "Erro no accept.\n");
            free(new_sock);
            continue;
        }

        // Cria a thread passando o ponteiro do socket
        unsigned thread_id;
        HANDLE thread_handle = (HANDLE)_beginthreadex(NULL, 0, client_thread, new_sock, 0, &thread_id);
        
        if (thread_handle == NULL) {
            fprintf(stderr, "Erro ao criar thread para cliente.\n");
            closesocket(*new_sock);
            free(new_sock);
        } else {
            CloseHandle(thread_handle); // A thread rodará livremente
        }
    }

    closesocket(server_sock);
    WSACleanup();
    return 0;
}
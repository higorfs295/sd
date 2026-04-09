import socket
import threading

BUFFER_SIZE = 512
message_counter = 0
counter_lock = threading.Lock()

def handle_client(server_socket, data, addr):
    global message_counter
    with counter_lock:
        message_counter += 1
        current_count = message_counter

    msg_recebida = data.decode('utf-8', errors='replace')
    print(f"[{addr[0]}:{addr[1]}] Mensagem recebida: {msg_recebida}")

    resposta = f"Mensagem {current_count} recebida com sucesso!"
    server_socket.sendto(resposta.encode('utf-8'), addr)

def main():
    # Pede a porta dinamicamente pelo terminal DENTRO do programa
    entrada = input("Digite a porta em que o servidor vai atender (ex: 9095): ")
    
    # Se você der Enter sem digitar nada, ele usa a 9095 como salvaguarda
    if entrada.strip() == "":
        porta = 9095
    else:
        porta = int(entrada)

    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    try:
        server.bind(("localhost", porta))
        print(f"Servidor escutando na porta {porta}...")
    except OSError:
        print(f"Erro: A porta {porta} já está em uso por outro programa. Tente outra.")
        return

    try:
        while True:
            data, addr = server.recvfrom(BUFFER_SIZE)
            client_thread = threading.Thread(
                target=handle_client, args=(server, data, addr), daemon=True
            )
            client_thread.start()
    except KeyboardInterrupt:
        print("\nEncerrando o servidor...")
    finally:
        server.close()

if __name__ == "__main__":
    main()
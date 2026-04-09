import socket
import threading

HOST = "0.0.0.0"
PORT = 9090
BUFFER_SIZE = 512

def handle_client(client_socket, addr):
    """Função executada em uma thread separada para cada cliente."""
    print(f"[+] Cliente conectado: {addr}")
    with client_socket:
        file = client_socket.makefile("rwb")
        try:
            while True:
                line = file.readline()
                if not line:
                    break # Cliente fechou a conexão

                print(f"[{addr[0]}:{addr[1]}] Recebido: ", line.decode("utf-8", errors="replace"), end="")
                file.write(line) # Echo de volta para o cliente
                file.flush()
        except Exception as e:
            print(f"[-] Erro na conexão com {addr}: {e}")
        finally:
            print(f"[-] Cliente {addr} desconectou.")

def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen() # Fica ouvindo novas conexões

    print(f"Escutando a porta {PORT} (Modo Concorrente)...")

    try:
        while True:
            client, addr = server.accept()
            # Inicia uma thread para o cliente recém-conectado
            thread = threading.Thread(target=handle_client, args=(client, addr), daemon=True)
            thread.start()
    except KeyboardInterrupt:
        print("\nEncerrando o servidor...")
    finally:
        server.close()

if __name__ == "__main__":
    main()
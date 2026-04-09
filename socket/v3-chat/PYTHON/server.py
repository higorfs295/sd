import socket
import threading
import chat_handler

HOST = "0.0.0.0"
PORT = 9090

def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen()

    print(f"Servidor escutando na porta {PORT}...")
    print("Abrindo chat multiusuario...")

    try:
        while True:
            client, addr = server.accept()
            thread = threading.Thread(
                target=chat_handler.handle_client,
                args=(client, addr),
                daemon=True
            )
            thread.start()
    except KeyboardInterrupt:
        print("\nEncerrando o servidor...")
    finally:
        server.close()

if __name__ == "__main__":
    main()
import socket
import threading
import sys

HOST = "127.0.0.1"
PORT = 9090
BUFFER_SIZE = 512

def receive_loop(sock):
    """Thread que escuta o servidor constantemente."""
    try:
        while True:
            data = sock.recv(BUFFER_SIZE)
            if not data:
                break
            sys.stdout.write(data.decode("utf-8", errors="replace"))
            sys.stdout.flush()
    except:
        pass # Ignora erros ao fechar o socket

def main():
    print(f"Conectando ao servidor em {HOST}:{PORT}...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    try:
        sock.connect((HOST, PORT))
    except ConnectionRefusedError:
        print("Erro: Não foi possível conectar ao servidor.")
        return

    print("Digite mensagens. Ctrl+C para sair.")

    t = threading.Thread(target=receive_loop, args=(sock,), daemon=True)
    t.start()

    try:
        for line in sys.stdin:
            sock.sendall(line.encode("utf-8"))
    except KeyboardInterrupt:
        print("\nSaindo...")
    finally:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except:
            pass
        sock.close()
        print("Conexão encerrada.")

if __name__ == "__main__":
    main()
import socket

HOST = "0.0.0.0"
PORT = 9090
BUFFER_SIZE = 512

def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(1)

    print(f"Escutando a porta {PORT}...")

    while True:
        client, addr = server.accept()
        print(f"Cliente conectado: {addr}")

        with client:
            file = client.makefile("rwb")
            try:
                while True:
                    line = file.readline()
                    if not line:
                        break

                    print("Recebido:", line.decode("utf-8", errors="replace"), end="")
                    file.write(line)
                    file.flush()
            finally:
                print("Cliente desconectou. Voltando a escutar...")

if __name__ == "__main__":
    main()
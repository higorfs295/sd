import threading
import time

MAX_CLIENTS = 64

clients = []
clients_lock = threading.Lock()

def timestamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def send_line(sock, text):
    sock.sendall((text + "\n").encode("utf-8", errors="replace"))

def broadcast(message):
    with clients_lock:
        snapshot = [c["socket"] for c in clients]

    for sock in snapshot:
        try:
            send_line(sock, message)
        except Exception:
            pass

def online_list():
    with clients_lock:
        return " ".join(c["name"] for c in clients)

def register_client(sock, name):
    with clients_lock:
        if len(clients) >= MAX_CLIENTS:
            return False
        if any(c["name"] == name for c in clients):
            return False
        clients.append({"socket": sock, "name": name})
        return True

def remove_client(sock):
    with clients_lock:
        for i, c in enumerate(clients):
            if c["socket"] == sock:
                removed = c["name"]
                clients.pop(i)
                return removed
    return None

def handle_client(client_socket, addr):
    print(f"[+] Cliente conectado: {addr}")
    name = None

    try:
        with client_socket:
            reader = client_socket.makefile("r", encoding="utf-8", newline="\n")

            first_line = reader.readline()
            if not first_line:
                return

            name = first_line.strip()

            if not name:
                send_line(client_socket, "Servidor: nome de usuario vazio.")
                return

            if not register_client(client_socket, name):
                send_line(client_socket, "Servidor: nome ja em uso ou sala cheia.")
                return

            send_line(client_socket, f"Servidor: bem-vindo, {name}!")
            current_online = online_list()
            send_line(
                client_socket,
                f"Servidor: usuarios online agora -> {current_online if current_online else '(ninguem)'}"
            )

            print(f"[{timestamp()}] {name} entrou no chat.")
            broadcast(f"Servidor: [{name}] entrou no chat.")

            while True:
                line = reader.readline()
                if not line:
                    break

                msg = line.rstrip("\r\n")
                if not msg:
                    continue

                if msg == "/quit":
                    break

                message = f"[{timestamp()}] {name}: {msg}"
                print(message)
                broadcast(message)

            reader.close()

    except Exception as e:
        print(f"[-] Erro na conexao com {addr}: {e}")

    finally:
        removed_name = remove_client(client_socket)
        if removed_name:
            print(f"[{timestamp()}] {removed_name} saiu do chat.")
            broadcast(f"Servidor: [{removed_name}] saiu do chat.")
        print(f"[-] Cliente {addr} desconectou.")
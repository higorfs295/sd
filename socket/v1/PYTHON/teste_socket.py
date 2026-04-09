import socket

HOST = "localhost"
PORT = 9090

print(f"Conectando ao servidor em {HOST}:{PORT}...")
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect((HOST, PORT))
print("Conectado com sucesso.")
sock.close()
print("Conexão encerrada.")
#!/bin/sh

HOST="127.0.0.1"
PORT="9090"

echo "Conectando ao servidor em $HOST:$PORT..."
echo "Digite mensagens. Ctrl+C para sair."

# O Netcat (nc/ncat) no cliente já lida bem com STDIN para Socket
ncat "$HOST" "$PORT"
echo "Conexão encerrada."
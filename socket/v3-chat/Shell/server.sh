#!/bin/sh

PORT="9090"
CLIENT_DIR="./clients"

mkdir -p "$CLIENT_DIR"

echo "Servidor escutando na porta $PORT..."
echo "Abrindo chat multiusuário..."

# Cada conexão recebida será tratada por chat_handler.sh
socat -T 300 TCP-LISTEN:"$PORT",reuseaddr,fork EXEC:"sh ./chat_handler.sh"
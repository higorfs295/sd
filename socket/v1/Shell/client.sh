#!/bin/sh

HOST="127.0.0.1"
PORT="9090"

echo "Conectando ao servidor em $HOST:$PORT..."
echo "Digite mensagens. Ctrl+C para sair."

while IFS= read -r MSG; do
    printf '%s\n' "$MSG"
done | ncat "$HOST" "$PORT"

echo "Conexão encerrada."
#!/bin/sh

HOST="127.0.0.1"
PORT="9090"

printf "Digite seu nome de usuário: "
read -r USERNAME
USERNAME=$(printf '%s' "$USERNAME" | tr -d '\r')

if [ -z "$USERNAME" ]; then
    echo "Nome vazio. Encerrando."
    exit 1
fi

echo "Conectando ao servidor em $HOST:$PORT..."
echo "Digite mensagens. Use /quit para sair."

{
    printf '%s\n' "$USERNAME"
    cat
} | ncat "$HOST" "$PORT"

echo "Conexão encerrada."
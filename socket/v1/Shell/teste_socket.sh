#!/bin/sh

HOST="localhost"
PORT="9090"

printf "Conectando ao servidor em %s:%s...\n" "$HOST" "$PORT"
nc "$HOST" "$PORT"

echo "Conexão encerrada."
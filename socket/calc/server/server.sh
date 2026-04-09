#!/bin/sh

PORT=9094

echo "Servidor rodando em http://localhost:$PORT"
echo "Pressione CTRL+C para parar"

# Cada conexão é tratada em um processo filho (timeout de 60s)
socat -T 60 TCP-LISTEN:$PORT,reuseaddr,fork EXEC:"./handler.sh"
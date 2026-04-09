#!/bin/sh

PORT="9090"

echo "Escutando a porta $PORT (Modo Concorrente com Socat)..."

# TCP-LISTEN cria o socket. 
# O parâmetro 'fork' faz o equivalente a criar threads (cria um processo filho por conexão)
# 'EXEC:cat' faz com que o servidor repita as mensagens de volta para o cliente (Echo puro)
socat TCP-LISTEN:"$PORT",reuseaddr,fork EXEC:"tee /dev/stderr"

echo "Servidor encerrado."
#!/bin/sh

PORT="9090"

echo "Escutando a porta $PORT..."

while true; do
    ncat -lvk -p "$PORT"
    echo "Cliente desconectou. Voltando a escutar..."
done
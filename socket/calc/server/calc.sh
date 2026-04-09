#!/bin/sh

A=$1
B=$2
OP=$3

case "$OP" in
    add)
        awk "BEGIN {print $A + $B}"
        ;;
    sub)
        awk "BEGIN {print $A - $B}"
        ;;
    mul)
        awk "BEGIN {print $A * $B}"
        ;;
    div)
        if [ "$B" = "0" ]; then
            echo "Erro: divisão por zero"
        else
            awk "BEGIN {printf \"%.2f\n\", $A / $B}"
        fi
        ;;
    pow)
        awk "BEGIN {print $A ^ $B}"
        ;;
    mod)
        if [ "$B" = "0" ]; then
            echo "Erro: divisão por zero"
        else
            awk "BEGIN {print $A % $B}"
        fi
        ;;
    sqrt)
        if awk "BEGIN {exit ($A < 0 ? 0 : 1)}"; then
            echo "Erro: raiz de número negativo"
        else
            awk "BEGIN {printf \"%.2f\n\", sqrt($A)}"
        fi
        ;;
    *)
        echo "Operação inválida"
        ;;
esac
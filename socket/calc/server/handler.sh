#!/bin/sh

read REQUEST
REQUEST=$(echo "$REQUEST" | tr -d '\r')
[ -z "$REQUEST" ] && exit 0

while read HEADER; do
    HEADER=$(echo "$HEADER" | tr -d '\r')
    [ -z "$HEADER" ] && break
done

ROUTE=$(echo "$REQUEST" | cut -d' ' -f2)

# Página inicial
if [ "$ROUTE" = "/" ]; then
    printf "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n"
    cat ../web/index.html
    exit 0
fi

# Arquivo CSS (Garante que a aparência carregue)
if [ "$ROUTE" = "/style.css" ]; then
    printf "HTTP/1.1 200 OK\r\nContent-Type: text/css\r\n\r\n"
    cat ../web/style.css
    exit 0
fi

# Arquivo JS (Garante que a calculadora funcione)
if [ "$ROUTE" = "/script.js" ]; then
    printf "HTTP/1.1 200 OK\r\nContent-Type: application/javascript\r\n\r\n"
    cat ../web/script.js
    exit 0
fi

# Rota de cálculo
if echo "$ROUTE" | grep -q "/calc"; then
    QUERY=$(echo "$ROUTE" | cut -d'?' -f2)

    A=$(echo "$QUERY" | sed -n 's/.*a=\([^&]*\).*/\1/p')
    B=$(echo "$QUERY" | sed -n 's/.*b=\([^&]*\).*/\1/p')
    OP=$(echo "$QUERY" | sed -n 's/.*op=\([^&]*\).*/\1/p')

    RESULT=$(./calc.sh "$A" "$B" "$OP")

    printf "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n"
    
    cat <<EOF
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Resultado</title>
    <link rel="stylesheet" href="/style.css">
</head>
<body>
    <div class="container">
        <h1>Resultado</h1>
        <div class="result">$RESULT</div>
        <a href="/" class="btn">Nova operação</a>
    </div>
</body>
</html>
EOF
    exit 0
fi

printf "HTTP/1.1 404 Not Found\r\n\r\n"]
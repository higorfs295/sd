#!/bin/sh

CLIENT_DIR="./clients"

timestamp() {
    date '+%Y-%m-%d %H:%M:%S'
}

server_log() {
    # Tudo que for para stderr aparece no terminal do servidor
    printf '%s\n' "$*" >&2
}

broadcast() {
    msg=$1

    # Envia a mensagem para todos os FIFOs ativos
    for fifo in "$CLIENT_DIR"/*.out; do
        [ -p "$fifo" ] || continue
        printf '%s\n' "$msg" > "$fifo" 2>/dev/null || true
    done
}

online_list() {
    users=""

    for file in "$CLIENT_DIR"/*.name; do
        [ -f "$file" ] || continue
        user=$(cat "$file" 2>/dev/null)
        [ -n "$user" ] && users="$users $user"
    done

    printf '%s\n' "${users# }"
}

cleanup() {
    # Encerra o leitor do FIFO e remove os arquivos temporários
    [ -n "${CAT_PID:-}" ] && kill "$CAT_PID" 2>/dev/null
    [ -n "${OUT_FIFO:-}" ] && rm -f "$OUT_FIFO"
    [ -n "${NAME_FILE:-}" ] && rm -f "$NAME_FILE"
    exec 3>&- 3<&- 2>/dev/null
}

trap cleanup INT TERM EXIT

# Lê o nome do usuário como primeira linha
IFS= read -r USERNAME || exit 0
USERNAME=$(printf '%s' "$USERNAME" | tr -d '\r')

if [ -z "$USERNAME" ]; then
    printf 'Servidor: nome de usuário vazio.\n'
    exit 0
fi

SESSION_ID="$$"
OUT_FIFO="$CLIENT_DIR/$SESSION_ID.out"
NAME_FILE="$CLIENT_DIR/$SESSION_ID.name"

# Cria o FIFO exclusivo deste cliente
mkfifo "$OUT_FIFO" || exit 1

# Mantém o FIFO aberto para evitar bloqueio quando outros clientes escreverem nele
exec 3<>"$OUT_FIFO"

# Salva o nome do usuário para a lista de online
printf '%s\n' "$USERNAME" > "$NAME_FILE"

# Este cat pega tudo que for escrito no FIFO e manda para o cliente
cat "$OUT_FIFO" &
CAT_PID=$!

printf 'Servidor: bem-vindo, %s!\n' "$USERNAME"
printf 'Servidor: usuários online agora -> %s\n' "$(online_list)"

server_log "[$(timestamp)] $USERNAME entrou no chat."
broadcast "Servidor: [$USERNAME] entrou no chat."

while IFS= read -r MSG; do
    MSG=$(printf '%s' "$MSG" | tr -d '\r')
    [ -z "$MSG" ] && continue

    if [ "$MSG" = "/quit" ]; then
        break
    fi

    server_log "[$(timestamp)] $USERNAME: $MSG"
    broadcast "[$(timestamp)] $USERNAME: $MSG"
done

server_log "[$(timestamp)] $USERNAME saiu do chat."
broadcast "Servidor: [$USERNAME] saiu do chat."
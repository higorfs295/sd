#!/usr/bin/env bash
# build.sh — compila a variante C sem depender do make.
# Detecta o gcc do MSYS2/MinGW se não estiver no PATH.
set -e

CC="${CC:-gcc}"
if ! command -v "$CC" >/dev/null 2>&1; then
  for p in /c/msys64/mingw64/bin/gcc.exe /c/msys64/ucrt64/bin/gcc.exe /c/MinGW/bin/gcc.exe; do
    [ -x "$p" ] && CC="$p" && break
  done
fi

CFLAGS="-O2 -Wall -Wextra -std=c11"
COMMON="json.c dfs_common.c"

case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*) LIBS="-lws2_32 -lpthread"; EXT=".exe" ;;
  *)                    LIBS="-lpthread";          EXT="" ;;
esac

echo "compilando com $CC"
"$CC" $CFLAGS -o "coordinator$EXT" coordinator.c $COMMON $LIBS
"$CC" $CFLAGS -o "node$EXT"        node.c        $COMMON $LIBS
"$CC" $CFLAGS -o "client$EXT"      client.c      $COMMON $LIBS
echo "ok: coordinator$EXT, node$EXT, client$EXT"

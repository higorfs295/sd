# Makefile — variante C do DFS.
#
# No Windows (MSYS2/MinGW) linka contra Winsock (ws2_32); em POSIX usa apenas
# pthread. Gera três executáveis: coordinator, node e client.

CC      ?= gcc
CFLAGS  ?= -O2 -Wall -Wextra -std=c11
COMMON   = json.c dfs_common.c

ifeq ($(OS),Windows_NT)
  LIBS   = -lws2_32 -lpthread
  EXT    = .exe
else
  LIBS   = -lpthread
  EXT    =
endif

BINS = coordinator$(EXT) node$(EXT) client$(EXT)

all: $(BINS)

coordinator$(EXT): coordinator.c $(COMMON)
	$(CC) $(CFLAGS) -o $@ coordinator.c $(COMMON) $(LIBS)

node$(EXT): node.c $(COMMON)
	$(CC) $(CFLAGS) -o $@ node.c $(COMMON) $(LIBS)

client$(EXT): client.c $(COMMON)
	$(CC) $(CFLAGS) -o $@ client.c $(COMMON) $(LIBS)

clean:
	rm -f $(BINS)

.PHONY: all clean

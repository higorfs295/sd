"""
TESTE DE TOLERÂNCIA A FALHAS — re-replicação + integridade (Marco 4/5).
=======================================================================

O que este teste prova (e o anterior NÃO provava):
    O teste antigo só conferia o hash MD5 após matar um nó. Mas um download
    bem-sucedido prova apenas o FAILOVER de leitura (basta 1 réplica viva) —
    o hash bate mesmo que ZERO re-replicação tenha ocorrido. Ou seja, dava um
    falso "passou" para o objetivo real, que é a RÉPLICA NOVA aparecer nos
    metadados.

    Aqui a validação correta é feita lendo o metadata_index.json:
        1. sobe um arquivo via CLI real (run_cli.py put);
        2. lê os metadados e escolhe para matar um nó que REALMENTE guarda
           chunk(s) deste arquivo (senão a re-replicação nem é disparada);
        3. mata o processo desse nó;
        4. faz POLLING do metadata_index.json até ver, nos chunks afetados,
           o nó morto ser SUBSTITUÍDO por um nó novo (re-replicação concluída);
        5. só então baixa e confere o MD5 (round-trip íntegro).

Comandos da CLI (confirmados em dfs/interface/cli.py):
    put <origem_local> <caminho_logico>
    get <caminho_logico> [saida_local]

Pré-requisito: o cluster precisa estar NO AR (python run_cluster.py) em outra
janela. Rode este teste a partir da pasta Final/.

LIMITAÇÃO QUE NÃO CONSEGUI TESTAR: o comando de matar o processo é específico
do Windows (PowerShell Get-CimInstance + Stop-Process). Não consegui validá-lo
em Linux; confirme no seu Windows que ele realmente derruba o nó (veja o passo
[3/6] imprimir o PID encontrado). Se preferir, mate o nó na mão e comente o
bloco do kill.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# ==============================================================================
# CONFIGURAÇÃO
# ==============================================================================
ROOT_DIR = Path(__file__).resolve().parent                      # .../Final
METADATA_PATH = ROOT_DIR / "DFS" / "data" / "metadata" / "metadata_index.json"

ORIGINAL = ROOT_DIR / "original.txt"
BAIXADO = ROOT_DIR / "baixado.txt"
CAMINHO_LOGICO = "testes/falha.bin"     # caminho lógico dentro do DFS
FILE_SIZE_MB = 5

# Comandos da CLI REAL (run_cli.py). Rodados com cwd=ROOT_DIR.
PUT_CMD = [sys.executable, "run_cli.py", "put", str(ORIGINAL), CAMINHO_LOGICO]
GET_CMD = [sys.executable, "run_cli.py", "get", CAMINHO_LOGICO, str(BAIXADO)]

# Quanto esperar pela re-replicação. Detecção da morte ~ HEARTBEAT_DEAD(8s) +
# WATCHER_INTERVAL(2s) ≈ 10s; somamos o round-trip Kafka + cópia do chunk.
TIMEOUT_REREPLICACAO = 30
POLL_INTERVALO = 1.0
# ==============================================================================


def md5(filepath: Path) -> str:
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for bloco in iter(lambda: f.read(65536), b""):
            h.update(bloco)
    return h.hexdigest()


def ler_metadados() -> dict:
    """Lê o metadata_index.json (a fonte de verdade do coordenador)."""
    with open(METADATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def entrada_do_arquivo(meta: dict, caminho_logico: str) -> dict | None:
    """Retorna a entrada {chunks:[...]} do arquivo, ou None se ainda não existe."""
    return meta.get("files", {}).get(caminho_logico)


def mapa_chunk_replicas(entrada: dict) -> dict[str, list[str]]:
    """{chunk_id: [node_id, ...]} a partir da entrada de metadados do arquivo."""
    return {c["chunk_id"]: list(c.get("replicas", [])) for c in entrada.get("chunks", [])}


def _powershell_exe() -> str:
    """
    Caminho ABSOLUTO do powershell.exe.

    Por que não chamar só 'powershell': rodando pelo Git Bash (MINGW64), o PATH
    fica em formato POSIX (/c/Windows/System32/...), e o CreateProcess do Windows
    não consegue achar 'powershell' pelo nome cru — daí o 'FileNotFoundError:
    [WinError 2]'. Usar o caminho completo dispensa a busca no PATH e resolve.
    """
    system_root = os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT") or r"C:\Windows"
    candidato = os.path.join(
        system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"
    )
    return candidato if os.path.exists(candidato) else "powershell"


def matar_no_windows(node_id: str) -> None:
    """
    Mata o processo do nó no Windows SEM usar wmic (deprecado no Win11 recente).
    Procura o python cujo CommandLine contém '--node-id <node_id>' e o derruba.

    Se o kill automático não funcionar (powershell não encontrado, ou nenhum PID
    casado), cai para o MODO MANUAL: você mata o nó na mão e aperta ENTER. Assim
    o teste nunca quebra no passo do kill — no pior caso, vira semiautomático.
    """
    ps = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.CommandLine -like '*--node-id {node_id}*' }} | "
        "ForEach-Object { Write-Output $_.ProcessId; Stop-Process -Id $_.ProcessId -Force }"
    )

    derrubado = False
    try:
        resultado = subprocess.run(
            [_powershell_exe(), "-NoProfile", "-Command", ps],
            capture_output=True, text=True,
        )
        pids = resultado.stdout.strip()
        if pids:
            print(f"      -> PID(s) derrubado(s) para {node_id}: {pids}")
            derrubado = True
        else:
            print(f"      ⚠️ Nenhum PID casou com '--node-id {node_id}'. "
                  f"stderr={resultado.stderr.strip()}")
    except FileNotFoundError:
        print("      ⚠️ Não encontrei o powershell.exe para o kill automático.")

    if not derrubado:
        input(f"      >> MODO MANUAL: mate o '{node_id}' agora (feche a janela dele "
              f"ou use o Gerenciador de Tarefas / Stop-Process) e aperte ENTER... ")


def main() -> None:
    print(f"\n{'='*60}")
    print("🚀 TESTE DE TOLERÂNCIA A FALHAS (re-replicação + integridade)")
    print(f"{'='*60}\n")

    if not METADATA_PATH.exists():
        print(f"❌ metadata_index.json não encontrado em {METADATA_PATH}. "
              f"O cluster está no ar (python run_cluster.py)?")
        return

    # 1. Gera o arquivo
    print(f"[1/6] Gerando arquivo de teste ({FILE_SIZE_MB}MB)...")
    ORIGINAL.write_bytes(os.urandom(FILE_SIZE_MB * 1024 * 1024))
    hash_original = md5(ORIGINAL)
    print(f"      -> MD5 original: {hash_original}")

    # 2. Upload via CLI real
    print(f"\n[2/6] Upload via CLI: {' '.join(PUT_CMD)}")
    subprocess.run(PUT_CMD, cwd=str(ROOT_DIR), check=True)

    # Lê os metadados e confirma que o arquivo realmente entrou
    meta = ler_metadados()
    entrada = entrada_do_arquivo(meta, CAMINHO_LOGICO)
    if entrada is None:
        print(f"❌ '{CAMINHO_LOGICO}' não apareceu nos metadados após o put. Abortando.")
        return

    replicas_antes = mapa_chunk_replicas(entrada)
    print(f"      -> {len(replicas_antes)} chunk(s) gravado(s). Réplicas (antes):")
    for cid, reps in replicas_antes.items():
        print(f"         {cid}: {reps}")

    # 3. Escolhe um nó que REALMENTE guarda chunk deste arquivo e o mata
    nos_com_chunk: dict[str, int] = {}
    for reps in replicas_antes.values():
        for nid in reps:
            nos_com_chunk[nid] = nos_com_chunk.get(nid, 0) + 1
    if not nos_com_chunk:
        print("❌ Nenhuma réplica registrada para o arquivo. Abortando.")
        return

    no_alvo = max(nos_com_chunk, key=lambda n: nos_com_chunk[n])
    chunks_afetados = [cid for cid, reps in replicas_antes.items() if no_alvo in reps]
    print(f"\n[3/6] Matando '{no_alvo}' (guarda {nos_com_chunk[no_alvo]} chunk(s) deste arquivo)...")
    matar_no_windows(no_alvo)

    # 4. Polling dos metadados até a re-replicação concluir
    print(f"\n[4/6] Aguardando re-replicação (até {TIMEOUT_REREPLICACAO}s)...")
    print("      Critério: em cada chunk afetado, o nó morto sai e um nó novo entra.")
    inicio = time.time()
    rereplicado = False
    while time.time() - inicio < TIMEOUT_REREPLICACAO:
        time.sleep(POLL_INTERVALO)
        try:
            entrada_agora = entrada_do_arquivo(ler_metadados(), CAMINHO_LOGICO)
        except (json.JSONDecodeError, FileNotFoundError):
            continue  # coordenador pode estar reescrevendo o índice agora
        if entrada_agora is None:
            continue
        replicas_agora = mapa_chunk_replicas(entrada_agora)

        # Re-replicado = para TODO chunk afetado, o nó morto saiu E há uma réplica
        # nova (que não estava antes naquele chunk).
        ok = True
        for cid in chunks_afetados:
            antes = set(replicas_antes.get(cid, []))
            agora = set(replicas_agora.get(cid, []))
            saiu_o_morto = no_alvo not in agora
            entrou_alguem_novo = bool(agora - (antes - {no_alvo}))
            if not (saiu_o_morto and entrou_alguem_novo):
                ok = False
                break
        if ok:
            rereplicado = True
            decorrido = time.time() - inicio
            print(f"      ✅ Re-replicação detectada em ~{decorrido:.1f}s. Réplicas (depois):")
            for cid in chunks_afetados:
                print(f"         {cid}: {replicas_agora.get(cid)}")
            break

    if not rereplicado:
        print(f"      ❌ Em {TIMEOUT_REREPLICACAO}s os metadados NÃO mostraram a réplica nova "
              f"nos chunks afetados. (Confira os logs do coordenador/nó: '[watcher] DEAD', "
              f"'[Kafka] Replicando ... concluída', '[replication] metadados atualizados'.)")

    # 5. Download e 6. integridade (failover garante leitura mesmo sem re-replicação)
    print(f"\n[5/6] Download via CLI: {' '.join(GET_CMD)}")
    if BAIXADO.exists():
        BAIXADO.unlink()
    subprocess.run(GET_CMD, cwd=str(ROOT_DIR), check=True)

    print("\n[6/6] Verificando integridade (MD5)...")
    hash_baixado = md5(BAIXADO)
    print(f"      original: {hash_original}")
    print(f"      baixado : {hash_baixado}")
    integro = hash_original == hash_baixado

    print(f"\n{'='*60}")
    print(f"  Integridade (round-trip): {'✅ OK' if integro else '❌ FALHOU'}")
    print(f"  Re-replicação nos metadados: {'✅ OK' if rereplicado else '❌ NÃO CONFIRMADA'}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
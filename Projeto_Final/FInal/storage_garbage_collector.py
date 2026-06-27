"""
SISTEMA DE ARQUIVOS DISTRIBUÍDO (DFS) - GARBAGE COLLECTOR DE DISCO
===================================================================
Descrição Geral:
    Serviço autônomo que monitora a saúde das partições de dados dos Storage Nodes.
    Previne e limpa anomalias geradas por quebras de conexão ou interrupções.

ESCOPO (importante para não confundir com o GC do heartbeat):
    Este serviço é APENAS um "limpador de escrita morta". Ele remove:
        - arquivos .tmp (escritas interrompidas / chunks não finalizados);
        - chunks zumbis de 0 bytes;
      e SOMENTE se já estiverem parados há mais de FILE_AGE_THRESHOLD segundos.

    Ele NÃO apaga chunks órfãos válidos (chunks íntegros que não constam mais
    nos metadados). Essa responsabilidade é do GC do heartbeat (no coordenador
    + HeartbeatWorker do nó), que compara o block report com os metadados e
    devolve chunks_to_delete. Detectar órfão exige consultar o catálogo do
    coordenador — coisa que este script standalone, por design, não faz.

Refatoração Sênior:
    - Trava de Tempo (Time-Lock): só deleta arquivos modificados há mais de 60
      segundos, garantindo que não corromperá streams de upload em andamento.

CORREÇÃO DE CAMINHO (bug encontrado na integração):
    Os chunks ficam fisicamente em <DATA_DIR>/nodes/<node_id>/chunks/, conforme
    config.py (storage_dir = DATA_DIR/"nodes"/node_id) e local_storage.py
    (grava em <root>/chunks/<chunk_id>). A versão anterior varria
    <DATA_DIR>/<node_id> (faltava o nível "nodes/" e o subdiretório "chunks/"),
    então não enxergava arquivo nenhum e nunca limpava nada.
"""

from __future__ import annotations

import time
from pathlib import Path

# --- CONFIGURAÇÃO ---
ROOT_DIR = Path(__file__).resolve().parent
STORAGE_BASE_DIR = ROOT_DIR / "DFS" / "data"
NODES = ["node1", "node2", "node3", "node4", "node5"]

CLEAN_INTERVAL = 10.0      # Tempo entre as varreduras do disco (segundos)
FILE_AGE_THRESHOLD = 60.0  # Tempo mínimo sem modificação para ser "morto" (segundos)
# --------------------


def chunks_dir_de(node_id: str) -> Path:
    """
    Caminho REAL onde os chunks de um nó vivem: <data>/nodes/<node_id>/chunks/.
    Casa exatamente com config.py + local_storage.py (CHUNKS_SUBDIR = "chunks").
    """
    return STORAGE_BASE_DIR / "nodes" / node_id / "chunks"


def is_arquivo_morto(arquivo: Path) -> bool:
    """Verifica se o arquivo cumpre os critérios para ser deletado (.tmp ou 0 bytes + idade)."""
    if not arquivo.is_file():
        return False

    idade_segundos = time.time() - arquivo.stat().st_mtime

    # Se o arquivo foi modificado recentemente, IGNORA (pode ser um chunk transferindo).
    if idade_segundos < FILE_AGE_THRESHOLD:
        return False

    # Critério 1: arquivos temporários não finalizados (.tmp).
    if arquivo.suffix == ".tmp":
        return True

    # Critério 2: chunks zumbis com 0 bytes de tamanho.
    if arquivo.stat().st_size == 0:
        return True

    return False


def otimizar_discos() -> None:
    """Itera sobre a pasta chunks/ de cada nó buscando escritas mortas (.tmp / 0 bytes)."""
    if not STORAGE_BASE_DIR.exists():
        return

    for node_id in NODES:
        chunks_dir = chunks_dir_de(node_id)
        if chunks_dir.exists() and chunks_dir.is_dir():
            for arquivo in chunks_dir.iterdir():
                if is_arquivo_morto(arquivo):
                    try:
                        tamanho_kb = arquivo.stat().st_size / 1024
                        arquivo.unlink()
                        print(
                            f"🧹 [GC] Anomalia resolvida no {node_id}: "
                            f"{arquivo.name} ({tamanho_kb:.1f} KB limpos)"
                        )
                    except Exception as e:
                        print(f"⚠️ [GC] Conflito ao acessar {arquivo.name}: {e}")


def main() -> None:
    print(f"\n{'='*75}")
    print("⚙️  STORAGE GARBAGE COLLECTOR: Serviço Assíncrono de I/O")
    print("   -> Monitorando .tmp e chunks de 0 bytes em background...")
    print(f"{'='*75}\n")

    try:
        while True:
            otimizar_discos()
            time.sleep(CLEAN_INTERVAL)
    except KeyboardInterrupt:
        print("\n🛑 Garbage Collector desligado.")


if __name__ == "__main__":
    main()

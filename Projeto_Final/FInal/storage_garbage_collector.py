"""
SISTEMA DE ARQUIVOS DISTRIBUÍDO (DFS) - GARBAGE COLLECTOR DE DISCO
===================================================================
Descrição Geral:
    Serviço autônomo que monitora a saúde das partições de dados dos Storage Nodes.
    Preve e limpa anomalias geradas por quebras de conexão ou interrupções.

Refatoração Sênior:
    - Trava de Tempo (Time-Lock): Só deleta arquivos modificados há mais de 60 segundos,
      garantindo que não corromperá streams de upload ainda em andamento.
"""

from __future__ import annotations

import time
from pathlib import Path

# --- CONFIGURAÇÃO ---
ROOT_DIR = Path(__file__).resolve().parent
STORAGE_BASE_DIR = ROOT_DIR / "DFS" / "data"
NODES = ["node1", "node2", "node3", "node4", "node5"]

CLEAN_INTERVAL = 10.0      # Tempo entre as varreduras do disco (segundos)
FILE_AGE_THRESHOLD = 60.0  # Tempo mínimo sem modificação para ser considerado "morto" (segundos)
# --------------------

def is_arquivo_morto(arquivo: Path) -> bool:
    """Verifica se o arquivo cumpre os critérios para ser deletado (Tamanho ou Extensão + Idade)."""
    if not arquivo.is_file():
        return False
        
    idade_segundos = time.time() - arquivo.stat().st_mtime
    
    # Se o arquivo foi modificado recentemente, IGNORA (pode ser um chunk transferindo)
    if idade_segundos < FILE_AGE_THRESHOLD:
        return False
        
    # Critério 1: Arquivos temporários não finalizados (.tmp)
    if arquivo.suffix == ".tmp":
        return True
        
    # Critério 2: Chunks zumbis com 0 bytes de tamanho
    if arquivo.stat().st_size == 0:
        return True
        
    return False

def otimizar_discos() -> None:
    """Itera sobre a base de dados dos nós buscando falhas nas transações de I/O."""
    if not STORAGE_BASE_DIR.exists():
        return

    for node_id in NODES:
        node_dir = STORAGE_BASE_DIR / node_id
        if node_dir.exists() and node_dir.is_dir():
            for arquivo in node_dir.iterdir():
                if is_arquivo_morto(arquivo):
                    try:
                        tamanho_kb = arquivo.stat().st_size / 1024
                        arquivo.unlink()
                        print(f"🧹 [GC] Anomalia resolvida no {node_id}: {arquivo.name} ({tamanho_kb:.1f} KB limpos)")
                    except Exception as e:
                        print(f"⚠️ [GC] Conflito ao acessar {arquivo.name}: {e}")

def main() -> None:
    print(f"\n{'='*75}")
    print("⚙️  STORAGE GARBAGE COLLECTOR: Serviço Assíncrono de I/O")
    print("   -> Monitorando anomalias e arquivos mortos em background...")
    print(f"{'='*75}\n")
    
    try:
        while True:
            otimizar_discos()
            time.sleep(CLEAN_INTERVAL)
    except KeyboardInterrupt:
        print("\n🛑 Garbage Collector desligado.")

if __name__ == "__main__":
    main()
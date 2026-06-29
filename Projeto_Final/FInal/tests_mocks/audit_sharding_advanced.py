import os
import statistics
from pathlib import Path

# Sobe até a raiz do seu projeto DFS
ROOT_DIR = Path(__file__).resolve().parent.parent

def audit_sharding_statistics():
    
    print("\n=======================================================")
    print("⚖️ AUDITORIA DE BALANCEAMENTO DE DADOS (SHARDING) ⚖️")
    print("=======================================================\n")
    
    # Dicionário: Caminho_do_Nó -> Total de Bytes
    node_storage = {}
    
    print("🔎 Varrendo o projeto em busca dos diretórios de armazenamento dos nós...")
    
    # Caminha por todas as pastas dentro da raiz do projeto DFS
    for dirpath, dirnames, filenames in os.walk(ROOT_DIR):
        # Ignora pastas de cache, git, venv, etc para ser rápido
        if any(ignore in dirpath for ignore in ['.git', '__pycache__', 'venv', '.venv']):
            continue
            
        # Uma heurística: Se a pasta tem arquivos com '_chunk_' no nome, é uma pasta de um nó.
        chunk_files = [f for f in filenames if "_chunk_" in f or f.endswith(".chunk")]
        if chunk_files:
            total_size = sum(os.path.getsize(os.path.join(dirpath, f)) for f in filenames if not f.startswith('.'))
            node_storage[dirpath] = total_size / (1024 * 1024) # Salva em MB

    if not node_storage:
        print("❌ Nenhum diretório contendo chunks de arquivo foi encontrado.")
        print("Certifique-se de que o cluster está rodando e você já fez alguns uploads.")
        return

    # Cálculos Estatísticos
    tamanhos = list(node_storage.values())
    media_mb = statistics.mean(tamanhos)
    desvio_mb = statistics.stdev(tamanhos) if len(tamanhos) > 1 else 0
    cv = (desvio_mb / media_mb) * 100 if media_mb > 0 else 0
    
    print(f"Encontrados {len(node_storage)} nós armazenando dados:\n")
    for d, size in node_storage.items():
        # Exibe apenas a parte final do caminho para ficar limpo
        nome_pasta = Path(d).name
        print(f"📦 {nome_pasta}: \t{size:.2f} MB")
        
    print(f"\n📊 Média por Nó: \t\t{media_mb:.2f} MB")
    print(f"📈 Desvio Padrão: \t\t{desvio_mb:.2f} MB")
    print(f"🎯 Coeficiente de Variação: \t{cv:.2f}%")
    
    print("\n-------------------------------------------------------")
    if cv <= 15.0:
        print("✅ [EXCELENTE] O Hash Constistente/Round-Robin está distribuindo perfeitamente a carga.")
    elif cv <= 30.0:
        print("⚠️ [ACEITÁVEL] Distribuição razoável, mas pode sofrer hot-spots no futuro.")
    else:
        print("❌ [ALERTA] Os dados estão mal distribuídos entre os nós.")

if __name__ == "__main__":
    audit_sharding_statistics()
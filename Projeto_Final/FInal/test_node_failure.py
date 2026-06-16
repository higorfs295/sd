import os
import subprocess
import time
import hashlib

# ==============================================================================
# CONFIGURAÇÃO: Ajuste estes comandos de acordo com a CLI que vocês usam
# Substitua o comando abaixo pela forma correta que vocês usam no terminal
# ==============================================================================
CLI_UPLOAD_CMD = ["python", "-m", "dfs.interface.client", "upload", "original.txt"]
CLI_DOWNLOAD_CMD = ["python", "-m", "dfs.interface.client", "download", "original.txt", "baixado.txt"]

NODE_TO_KILL = "node3"  # Vamos assassinar este nó durante o teste
FILE_SIZE_MB = 5        # Tamanho do arquivo gerado
# ==============================================================================

def get_file_hash(filepath: str) -> str:
    """Gera o Hash MD5 do arquivo para garantir que não houve corrupção de 1 bit sequer."""
    hash_md5 = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def main():
    print(f"\n{'='*50}")
    print("🚀 INICIANDO TESTE DE RESILIÊNCIA E INTEGRIDADE DFS")
    print(f"{'='*50}\n")

    # 1. Geração de Dados
    print(f"[1/6] Gerando arquivo de teste randômico ({FILE_SIZE_MB}MB)...")
    with open("original.txt", "wb") as f:
        f.write(os.urandom(FILE_SIZE_MB * 1024 * 1024))
    
    original_hash = get_file_hash("original.txt")
    print(f"      -> Hash MD5 Original: {original_hash}")

    # 2. Upload
    print("\n[2/6] Realizando Upload para o DFS...")
    subprocess.run(CLI_UPLOAD_CMD, check=True)
    print("      -> Upload concluído com sucesso.")

    # 3. Assassinato do Nó (Chaos Engineering)
    print(f"\n[3/6] Simulando falha catastrófica: Matando processo do '{NODE_TO_KILL}'...")
    # Comando nativo do Windows (WMIC) para encontrar o processo Python específico do node3 e matá-lo
    kill_cmd = f'wmic process where "name=\'python.exe\' and commandline like \'%--node-id {NODE_TO_KILL}%\'" call terminate'
    subprocess.run(kill_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"      -> Nó {NODE_TO_KILL} abatido! Olhe o terminal do Cluster, o Coordenador deve notar a falta do Heartbeat.")

    # 4. Tempo para Re-replicação (Marco 4)
    espera_segundos = 25
    print(f"\n[4/6] Aguardando {espera_segundos}s para o Coordenador detectar a falha e o Kafka ordenar a re-replicação...")
    for i in range(espera_segundos, 0, -1):
        print(f"      Aguardando... {i}s", end="\r")
        time.sleep(1)
    print("      -> Tempo de recuperação esgotado. Iniciando resgate dos dados...")

    # 5. Download e Resgate
    print("\n[5/6] Fazendo Download do arquivo reconstruído...")
    if os.path.exists("baixado.txt"):
        os.remove("baixado.txt")
    
    subprocess.run(CLI_DOWNLOAD_CMD, check=True)
    print("      -> Download concluído.")

    # 6. Prova de Integridade
    print("\n[6/6] Verificando integridade matemática dos dados...")
    downloaded_hash = get_file_hash("baixado.txt")
    print(f"      -> Hash MD5 Baixado:  {downloaded_hash}")

    print(f"\n{'='*50}")
    if original_hash == downloaded_hash:
        print("✅ RESULTADO FINAL: SUCESSO ABSOLUTO! 🎉")
        print("   O sistema perdeu um nó abruptamente, mas o Kafka e o ReplicationManager")
        print("   salvaram os chunks. A integridade do arquivo é de 100%.")
    else:
        print("🚨 RESULTADO FINAL: FALHA DE INTEGRIDADE!")
        print("   Os hashes não batem. O arquivo corrompeu ou a re-replicação falhou.")
    print(f"{'='*50}\n")

if __name__ == "__main__":
    main()
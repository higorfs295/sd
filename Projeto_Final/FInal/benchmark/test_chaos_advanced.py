import os
import sys
import time
import hashlib
from pathlib import Path
import docker  # <--- Integração com o Docker Engine

# --- Injeção do Path real do seu projeto ---
_DFS_DIR = Path(__file__).resolve().parent.parent / "DFS"
if str(_DFS_DIR) not in sys.path:
    sys.path.insert(0, str(_DFS_DIR))

# Importando o seu cliente real
from dfs.client import DataClient as DFSClient

# Configuração Base (Como roda DENTRO do coordenador, apontamos para o localhost/127.0.0.1)
COORD_HOST = "127.0.0.1"
COORD_PORT = 9100  
TEST_FILE = "dados_vitais_chaos.dat"
REMOTE_PATH = "/chaos_test/dados_vitais.dat"

# Nome do contêiner alvo definido no seu docker-compose.yml
TARGET_CONTAINER = "dfs-node2" 

def get_hash(filepath):
    """Calcula o MD5 do arquivo para verificar corrupção."""
    hasher = hashlib.md5()
    with open(filepath, 'rb') as f:
        buf = f.read(65536)
        while len(buf) > 0:
            hasher.update(buf)
            buf = f.read(65536)
    return hasher.hexdigest()

def main():
    print("\n=======================================================")
    print("🔥 INICIANDO TESTE DE CAOS INTERNO (TOLERÂNCIA A FALHAS) 🔥")
    print("=======================================================\n")
    
    # Conecta ao socket do Docker injetado no contêiner
    try:
        docker_client = docker.from_env()
        container_alvo = docker_client.containers.get(TARGET_CONTAINER)
    except Exception as e:
        print(f"❌ Erro ao conectar ao Docker: {e}")
        print("Verifique se o volume do docker.sock foi mapeado e se rodou 'pip install docker'.")
        return

    # 1. Preparação
    print(f"-> Gerando arquivo de teste de 5MB...")
    with open(TEST_FILE, "wb") as f:
        f.write(os.urandom(5 * 1024 * 1024))
    hash_original = get_hash(TEST_FILE)

    client = DFSClient(COORD_HOST, COORD_PORT)
    
    # 2. Upload
    print(f"-> Fazendo upload para {REMOTE_PATH}...")
    client.upload_file(TEST_FILE, REMOTE_PATH)
    print("✅ Upload inicial concluído com sucesso.")

    # 3. Injeta a Falha (Substitui o os.kill/SIGKILL original por container.kill)
    print(f"\n☠️  INJETANDO FALHA: Forçando a parada imediata do contêiner ({TARGET_CONTAINER})...")
    try:
        container_alvo.kill()  # O método .kill() envia um SIGKILL direto ao contêiner, simulando queda abrupta
        print(f"⚠️  O contêiner '{TARGET_CONTAINER}' foi derrubado com sucesso.")
    except Exception as e:
        print(f"❌ Falha ao derrubar o contêiner: {e}")
        return
    
    print("⏳ Aguardando 15 segundos para o Coordenador detectar a falha (Heartbeat Timeout)...")
    time.sleep(15)

    # 4. Tenta baixar o arquivo com o nó morto
    print(f"\n-> Tentando realizar o download do arquivo recuperado...")
    down_file = "download_recuperado_chaos.dat"
    
    try:
        client.download_file(REMOTE_PATH, down_file)
        hash_recuperado = get_hash(down_file)
        
        if hash_recuperado == hash_original:
            print("\n✅ [SUCESSO ABSOLUTO] O sistema se recuperou!")
            print("O arquivo foi baixado íntegro mesmo com um nó do cluster morto.")
        else:
            print("\n❌ [FALHA] O arquivo foi baixado, mas está CORROMPIDO.")
    except Exception as e:
        print(f"\n❌ [FALHA CRÍTICA] O cluster falhou em entregar o arquivo. Erro: {e}")
    finally:
        # 5. Recuperação automática do ambiente de testes
        print(f"\n🚀 RECUPERAÇÃO: Reiniciando o contêiner {TARGET_CONTAINER} para normalizar o cluster...")
        try:
            container_alvo.start()
            print(f"✅ Contêiner {TARGET_CONTAINER} está de volta online.")
        except Exception as e:
            print(f"⚠️  Não foi possível reiniciar o contêiner automaticamente: {e}")

        # Limpeza de arquivos temporários locais
        if os.path.exists(TEST_FILE): os.remove(TEST_FILE)
        if os.path.exists(down_file): os.remove(down_file)

if __name__ == "__main__":
    main()
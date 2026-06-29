import os
import sys
import time
import uuid
import threading
import hashlib
from pathlib import Path

# --- Injeção do Path ---
_DFS_DIR = Path(__file__).resolve().parent.parent / "DFS"
if str(_DFS_DIR) not in sys.path:
    sys.path.insert(0, str(_DFS_DIR))

from dfs.client import DataClient as DFSClient

COORD_HOST = "localhost"
COORD_PORT = 9100  # Porta do seu coordenador

def gerar_conteudo_local(versao):
    filename = f"local_v{versao}.dat"
    with open(filename, "wb") as f:
        # Cria arq levemente diferentes
        conteudo = (f"CONTEUDO VERSAO {versao} ".encode() * (1024 * 50)) # ~1MB
        f.write(conteudo)
    return filename, hashlib.md5(conteudo).hexdigest()

def main():
    print("\n=======================================================")
    print("🔒 INICIANDO TESTE DE CONSISTÊNCIA FORTE (RACE CONDITION) 🔒")
    print("=======================================================\n")
    
    remote_path = f"/consistencia/teste_{uuid.uuid4().hex[:6]}.dat"
    resultados = []
    
    file_v1, hash_v1 = gerar_conteudo_local(1)
    file_v2, hash_v2 = gerar_conteudo_local(2)
    
    client_writer = DFSClient(COORD_HOST, COORD_PORT)
    
    print(f"📝 [Escrita] Fazendo upload da Versão 1...")
    client_writer.upload_file(file_v1, remote_path)
    
    print(f"📝 [Escrita] Sobrescrevendo o mesmo arquivo com a Versão 2...")
    client_writer.upload_file(file_v2, remote_path)
    
    print(f"🚀 [Leitura] Disparando 5 threads simultâneas de leitura IMEDIATAMENTE após escrita...\n")
    
    def reader_task(reader_id):
        client_reader = DFSClient(COORD_HOST, COORD_PORT)
        local_down = f"download_r{reader_id}.dat"
        try:
            client_reader.download_file(remote_path, local_down)
            with open(local_down, "rb") as f:
                hash_lido = hashlib.md5(f.read()).hexdigest()
                
            if hash_lido == hash_v2:
                print(f"✅ [Reader {reader_id}] Leu a Versão 2 corretamente.")
                resultados.append(True)
            elif hash_lido == hash_v1:
                print(f"❌ [Reader {reader_id}] INCONSISTÊNCIA: Leu a Versão 1 (Leitura suja!).")
                resultados.append(False)
            else:
                print(f"❌ [Reader {reader_id}] CORRUPÇÃO MISTA detectada.")
                resultados.append(False)
        except Exception as e:
            print(f"❌ [Reader {reader_id}] Falha na leitura: {e}")
            resultados.append(False)
        finally:
            if os.path.exists(local_down): os.remove(local_down)

    threads = [threading.Thread(target=reader_task, args=(i,)) for i in range(1, 6)]
    for t in threads: t.start()
    for t in threads: t.join()
        
    if os.path.exists(file_v1): os.remove(file_v1)
    if os.path.exists(file_v2): os.remove(file_v2)
    
    print("\n=======================================================")
    if all(resultados) and len(resultados) == 5:
        print("🏆 RESULTADO: Aprovado. O cluster garantiu a consistência dos dados.")
    else:
        print("⚠️ RESULTADO: Reprovado. Ocorreram quebras de consistência.")

if __name__ == "__main__":
    main()
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# 1. A TUA FUNÇÃO (É esta que vais colocar depois no código real do teu nó)
def process_heartbeat_response(response, node_storage_dir):
    # Verifica se a resposta tem a ordem de eliminação e se a lista não está vazia
    if hasattr(response, 'chunks_to_delete') and response.chunks_to_delete:
        logger.info(f"Ordem recebida: eliminar {len(response.chunks_to_delete)} chunks órfãos.")
        
        for chunk_id in response.chunks_to_delete:
            chunk_path = os.path.join(node_storage_dir, chunk_id)
            
            try:
                if os.path.exists(chunk_path):
                    os.remove(chunk_path)
                    logger.info(f"SUCESSO: Chunk '{chunk_id}' foi eliminado fisicamente do disco.")
                else:
                    logger.warning(f"AVISO: Chunk '{chunk_id}' não encontrado em {chunk_path}.")
            except Exception as e:
                logger.error(f"ERRO ao tentar eliminar o chunk {chunk_id}: {str(e)}")

# 2. O SIMULADOR DA RESPOSTA DA VITÓRIA (Coordenador Mock)
class MockHeartbeatResponse:
    def __init__(self):
        # Aqui simulamos que a Vitória mandou apagar o nosso ficheiro falso
        self.chunks_to_delete = ["chunk_teste_123"]

# 3. EXECUTAR O TESTE
if __name__ == "__main__":
    print("--- A INICIAR TESTE DE GARBAGE COLLECTION ---")
    
    # Define o caminho para a pasta do node1 (ajusta se o teu caminho for diferente)
    pasta_do_no = "data/nodes/node1"
    
    # Criamos a resposta falsa
    resposta_falsa_do_coordenador = MockHeartbeatResponse()
    
    # Chamamos a tua função
    process_heartbeat_response(resposta_falsa_do_coordenador, pasta_do_no)
    
    # Verificação final
    if not os.path.exists(os.path.join(pasta_do_no, "chunk_teste_123")):
        print("--- RESULTADO: TESTE PASSOU! O ficheiro desapareceu da pasta. ---")
    else:
        print("--- RESULTADO: TESTE FALHOU! O ficheiro ainda lá está. ---")
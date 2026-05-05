import Pyro5.api
from protocolo import ModuloComunicacao

class ModuloReferenciaRemota:
    """
    Módulo de Referência Remota: Responsável por criar e resolver 
    referências remotas (Proxies) para o cliente. 
    O cliente usa o proxy gerado aqui como se fosse um objeto local.
    """
    def __init__(self):
        ModuloComunicacao.iniciar()

    def obter_referencia(self):
        try:
            print(f"[REFERÊNCIA] Buscando '{ModuloComunicacao.NOME_OBJETO}' no Name Server...")
            
            # Cria a representação local (Proxy) do objeto remoto
            proxy = Pyro5.api.Proxy(f"PYRONAME:{ModuloComunicacao.NOME_OBJETO}")
            
            # Força o 'bind' na rede para testar a conexão imediatamente e garantir que o Server está de pé
            proxy._pyroBind() 
            
            print("[REFERÊNCIA] Proxy criado com sucesso. Referência resolvida.")
            return proxy
        except Exception as e:
            raise ConnectionError(f"Falha ao resolver a referência remota. O Name Server e o Servidor estão rodando?\nDetalhes: {e}")
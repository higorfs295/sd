# Importação corrigida para evitar ModuleNotFoundError
from Pyro5 import config 

class ModuloComunicacao:
    """
    Módulo de Comunicação: Coopera para executar o protocolo de 
    requisição/resposta entre o cliente e o servidor.
    Define a semântica de invocação e a serialização (Marshaling).
    """
    # Configurações do protocolo de comunicação e rede
    SERIALIZADOR = "json"
    NOME_OBJETO = "RMI.Calculadora.Service"
    HOST = "127.0.0.1"  # Forçando IPv4 local
    PORTA = 9099        # Porta específica solicitada

    @staticmethod
    def iniciar():
        # Princípio: Marshaling via JSON configurado diretamente
        config.SERIALIZER = ModuloComunicacao.SERIALIZADOR
        print(f"[COMUNICAÇÃO] Protocolo {ModuloComunicacao.SERIALIZADOR.upper()} ativo.")
        print(f"[COMUNICAÇÃO] Rede configurada para {ModuloComunicacao.HOST}:{ModuloComunicacao.PORTA}.")
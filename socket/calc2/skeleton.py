import Pyro5.api
from servant import CalculadoraServant
from protocolo import ModuloComunicacao

class SkeletonDispatcher:
    """
    Skeleton & Dispatcher: No servidor, é responsável por escutar a rede,
    receber as requisições, desempacotar os dados (Unmarshaling) e
    despachar a chamada para a classe (Servant) correta.
    """
    def __init__(self):
        ModuloComunicacao.iniciar()
        
        # Configura o Daemon para escutar especificamente no IP e Porta desejados (127.0.0.1:9099)
        self.daemon = Pyro5.api.Daemon(
            host=ModuloComunicacao.HOST, 
            port=ModuloComunicacao.PORTA
        )
        
        # Localiza o serviço de nomes (Name Server / Registry)
        self.ns = Pyro5.api.locate_ns()

    def registrar_e_ouvir(self):
        # Registra a implementação (Servant) no dispatcher
        uri = self.daemon.register(CalculadoraServant)
        
        # Publica a referência do objeto no Naming Service para que os clientes o encontrem
        self.ns.register(ModuloComunicacao.NOME_OBJETO, uri)
        
        print(f"[SKELETON] Escutando e despachando em {ModuloComunicacao.HOST}:{ModuloComunicacao.PORTA}")
        print(f"[SKELETON] URI gerada: {uri}")
        
        # Inicia o loop para escutar requisições de rede indefinidamente
        self.daemon.requestLoop()
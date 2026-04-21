"""
DESCRIÇÃO GERAL:
Este é o módulo principal (entry point) do Servidor.
Ele é um Servidor TCP bloqueante que fica ouvindo conexões eternamente.
Em uma arquitetura cliente-servidor, ele é a "Porta de Entrada" do servidor: 
recebe requisições de rede, envia para o serviço processar, e retorna o resultado à rede.
"""

# Importa biblioteca de rede.
import socket

# Importa as configurações do servidor.
from dfs.config import HOST, PORT

# Importa as funções para conversar através da rede com segurança (framing).
from dfs.frame import recv_frame, send_frame

# Importa as dependências: armazenamento concreto e a lógica do serviço.
from dfs.storage.local_storage import LocalStorage
from dfs.application.file_service import FileService


def main() -> None:
    """
    Inicializa o servidor TCP do DFS.
    """
    # "Monta" a aplicação. Instancia o armazenamento e injeta ele no serviço de negócio.
    storage = LocalStorage()
    service = FileService(storage)

    # Cria o socket do servidor.
    # Novamente usamos o bloco `with` para garantir liberação da porta caso a aplicação falhe.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        # bind() liga o servidor de fato à interface de rede e porta definidos em config.py.
        # Sem isso o Sistema Operacional não repassa os dados da porta para o programa.
        server.bind((HOST, PORT))
        
        # listen() coloca o socket no modo escuta. 
        # O número "5" é o "backlog", o tamanho da fila do SO para conexões esperando 
        # para serem aceitas enquanto o servidor atende um cliente.
        server.listen(5)

        print(f"Servidor DFS ouvindo em {HOST}:{PORT}")

        # Laço try/except externo serve apenas para pegar interrupção manual do teclado (Ctrl+C).
        try:
            # Um servidor típico roda num laço infinito (while True).
            while True:
                # accept() bloqueia a execução esperando um cliente.
                # Quando conecta, devolve um "socket novo" (conn) dedicado apenas a este cliente 
                # e o IP/Porta (addr) do cliente.
                conn, addr = server.accept()
                print(f"Conexão recebida de {addr}")

                # Gerenciador de contexto do socket do cliente para garantir que ele seja fechado ao final.
                with conn:
                    # 1. Lê os bytes (requisição) que vieram da rede do cliente usando framing.
                    raw_request = recv_frame(conn)
                    
                    # 2. Envia para o Dispatcher processar a lógica no armazenamento 
                    # e pegar os bytes da resposta.
                    raw_response = service.dispatch(raw_request)
                    
                    # 3. Manda os bytes processados de volta pelo socket para o cliente.
                    send_frame(conn, raw_response)

        except KeyboardInterrupt:
            # Exibe uma mensagem bonita no console ao matar o servidor com Ctrl+C.
            print("\nServidor encerrado pelo usuário.")


# Permite executar com: python -m dfs.interface.server
if __name__ == "__main__":
    main()
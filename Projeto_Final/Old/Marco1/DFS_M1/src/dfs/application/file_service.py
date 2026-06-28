"""
DESCRIÇÃO GERAL:
Esta é a camada de Serviço (Regras de Negócio).
Ela recebe comandos genéricos e os traduz em ações concretas na camada de armazenamento.
A grande vantagem desta camada é a centralização da tratativa de erros e das regras lógicas,
isolando a rede (server.py) do sistema de arquivos (local_storage.py).
"""

# Importa os desserializadores de requisições e construtores de respostas.
from dfs.protocol import parse_request, make_response

# Importa a classe de armazenamento para usar como dependência.
from dfs.storage.local_storage import LocalStorage


class FileService:
    """
    Camada de aplicação do DFS.

    Ela recebe a requisição serializada, interpreta a operação solicitada
    e decide qual ação executar no armazenamento local.
    """

    def __init__(self, storage: LocalStorage):
        # O serviço recebe a instância do Storage ("Injeção de Dependência").
        # Isso facilita o isolamento e testes desta classe.
        self.storage = storage

    def dispatch(self, raw_request: bytes) -> bytes:
        """
        Processa uma requisição Protobuf e devolve uma resposta Protobuf.
        (Padrão Dispatcher/Router)
        """
        # Converte a requisição bruta que veio da rede em um objeto Python acessível.
        request = parse_request(raw_request)

        # Um bloco try/except em volta de toda a lógica para garantir que, caso falhe (ex: o arquivo não existe),
        # o servidor não caia, e sim devolva uma mensagem de erro controlada ao cliente.
        try:
            # Roteamento baseado no atributo 'op' (Operação) enviado pelo cliente.
            
            if request.op == "PUT":
                # Executa operação de gravação e devolve uma resposta de sucesso (True).
                self.storage.put(request.path, request.data)
                return make_response(True, "Arquivo salvo com sucesso")

            if request.op == "GET":
                # Executa leitura; observe que aqui passamos o `data` (conteúdo) de volta na resposta.
                data = self.storage.get(request.path)
                return make_response(True, "Arquivo encontrado", data=data)

            if request.op == "DELETE":
                # Chama a deleção no storage.
                self.storage.delete(request.path)
                return make_response(True, "Arquivo removido com sucesso")

            if request.op == "LIST":
                # Chama listagem e anexa a lista na propriedade `entries` da resposta.
                entries = self.storage.list_files()
                return make_response(True, "Listagem concluída", entries=entries)

            # Fallback caso uma operação não suportada seja enviada.
            return make_response(False, "Operação inválida")

        # Captura qualquer erro disparado pelas funções do LocalStorage 
        # (ex: permissão negada, FileNotFoundError, etc.).
        except Exception as exc:
            # Retorna uma mensagem formatada amigável para o cliente com ok=False.
            return make_response(False, f"Erro: {exc}")
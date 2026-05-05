from abc import ABC, abstractmethod

class ICalculadora(ABC):
    """
    Interface Remota: Descreve o contrato da aplicação.
    Define quais métodos do objeto remoto podem ser invocados
    por outros processos (clientes) através da rede.
    Garante o polimorfismo entre o Proxy (cliente) e o Servant (servidor).
    """
    @abstractmethod
    def executar_calculo(self, a: float, b: float, operacao: str) -> float:
        """Contrato obrigatório para o objeto remoto."""
        pass
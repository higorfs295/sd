import Pyro5.api
from interface import ICalculadora

@Pyro5.api.expose
class CalculadoraServant(ICalculadora):
    """
    Servant (Objeto Remoto): A implementação real que reside no servidor.
    Contém a lógica de negócio que será executada quando o cliente 
    fizer a chamada pela rede.
    """
    def executar_calculo(self, a, b, operacao):
        print(f"[SERVANT] Recebida requisição remota para calcular: {a} {operacao} {b}")
        try:
            a, b = float(a), float(b)
            ops = {
                '+': lambda: a + b, 
                '-': lambda: a - b,
                '*': lambda: a * b, 
                '/': lambda: a / b if b != 0 else "Erro: Divisão por zero",
                '^': lambda: a ** b, 
                'r': lambda: a ** 0.5
            }
            return ops.get(operacao, lambda: "Operação Inválida")()
        except Exception as e:
            return f"Erro no processamento remoto: {str(e)}"
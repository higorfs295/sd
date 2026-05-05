from proxy_resolver import ModuloReferenciaRemota

def exibir_menu():
    print("\n" + "-" * 40)
    print("  CLIENTE RMI - Calculadora Distribuída")
    print("-" * 40)
    print(" [+] Soma      [-] Subtração")
    print(" [*] Multiplicação [/] Divisão")
    print(" [^] Potência      [r] Raiz Quadrada")
    print(" Digite 'sair' para encerrar.")
    print("-" * 40)

def main():
    """
    Ponto de entrada do Cliente.
    Interage com o usuário no terminal e faz as chamadas ao Proxy de 
    forma totalmente transparente em relação à rede.
    """
    print("=" * 40)
    print("   INICIANDO PROCESSO CLIENTE RMI")
    print("=" * 40)
    
    try:
        # O Cliente aciona o módulo de referência para obter o Proxy
        resolver = ModuloReferenciaRemota()
        calc_remota = resolver.obter_referencia()
    except Exception as e:
        print(e)
        return

    print("\nConectado! (Interface Remota pronta para uso)")
    exibir_menu()

    while True:
        op = input("\nOperação (+, -, *, /, ^, r) ou 'sair': ").lower()
        if op == 'sair': 
            print("Encerrando o cliente...")
            break
        
        if op not in ['+', '-', '*', '/', '^', 'r']:
            print("Operação inválida. Tente novamente.")
            continue
        
        try:
            val_a = input("Primeiro valor: ")
            val_b = "0" if op == 'r' else input("Segundo valor: ")
            
            # A Mágica do RMI: Chamada via Proxy (Transparência de Localização)
            # O cliente chama uma função em Python, não sabe que isso é convertido 
            # em JSON e enviado via TCP para 127.0.0.1:9099
            res = calc_remota.executar_calculo(val_a, val_b, op)
            
            print(f">>> Resultado Remoto: {res}")
        except Exception as e:
            print(f"Erro na invocação RMI ou entrada inválida: {e}")

if __name__ == "__main__":
    main()
from skeleton import SkeletonDispatcher

def run_server():
    """
    Ponto de entrada do Servidor.
    Inicializa toda a infraestrutura RMI do lado servidor.
    """
    print("=" * 40)
    print("   INICIANDO PROCESSO SERVIDOR RMI")
    print("=" * 40)
    try:
        servidor = SkeletonDispatcher()
        servidor.registrar_e_ouvir()
    except Exception as e:
        print(f"Erro fatal no servidor: {e}")

if __name__ == "__main__":
    run_server()
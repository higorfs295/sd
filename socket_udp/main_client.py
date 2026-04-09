import socket

BUFFER_SIZE = 512

def main():
    # Pede a porta dinamicamente DENTRO do programa
    entrada = input("Digite a porta do servidor para conectar (ex: 9095): ")
    
    if entrada.strip() == "":
        porta = 9095
    else:
        porta = int(entrada)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5.0) # Proteção caso o servidor esteja desligado
    
    server_address = ("localhost", porta)
    mensagem = "SD é muito interessante!"
    
    print(f"Conectando ao servidor em localhost:{porta}...")

    try:
        sock.sendto(mensagem.encode('utf-8'), server_address)
        print("Mensagem enviada. Aguardando resposta...")

        data, server = sock.recvfrom(BUFFER_SIZE)
        print(f"Resposta do Servidor: {data.decode('utf-8')}")
        
    except socket.timeout:
        print("\nErro: Tempo limite excedido. O servidor não está rodando nesta porta.")
    except Exception as e:
        print(f"\nErro inesperado: {e}")
    finally:
        sock.close()

if __name__ == "__main__":
    main()
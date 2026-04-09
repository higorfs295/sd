import socket
import json

print("=== Configuração da Rede ===")
# Pede para o usuário digitar o IP da Máquina 2 descoberto no passo 1
HOST = input("Digite o IP da Máquina 2 (Servidor) na rede Wi-Fi: ").strip()
PORT = 65432

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    try:
        print(f"Tentando conectar a {HOST}:{PORT}...")
        s.connect((HOST, PORT))
        print("[+] Conectado com SUCESSO pela rede Wi-Fi!\n")
        
        while True:
            print("=== Calculadora Distribuída Multi-Máquinas ===")
            print("1. Soma (+) | 2. Subtração (-) | 3. Multiplicação (*) | 4. Divisão (/) | 0. Sair")
            
            escolha = input("Escolha a operação (0-4): ")
            if escolha == '0':
                break
                
            operacoes = {'1': '+', '2': '-', '3': '*', '4': '/'}
            if escolha not in operacoes: continue
            op = operacoes[escolha]
            
            try:
                qtd = int(input("Quantos números (2 a 10): "))
                if qtd < 2 or qtd > 10: continue
            except ValueError: continue
            
            numeros = []
            for i in range(qtd):
                while True:
                    try:
                        n = float(input(f"Número {i+1}: "))
                        numeros.append(n)
                        break
                    except ValueError: pass
                        
            requisicao = json.dumps({'op': op, 'numeros': numeros})
            s.sendall(requisicao.encode('utf-8'))
            
            data = s.recv(2048)
            resposta = json.loads(data.decode('utf-8'))
            
            print(f"\n[ SERVIDOR DIZ ] => Resultado: {resposta['resultado']}")
            print("-" * 50 + "\n")
            
    except ConnectionRefusedError:
        print("\n(!) ERRO: Conexão recusada. O servidor está rodando? O IP está correto?")
    except TimeoutError:
        print("\n(!) ERRO: Tempo esgotado. Verifique o IP e se estão no mesmo Wi-Fi.")
    except Exception as e:
        print(f"\n(!) ERRO de rede: {e}")
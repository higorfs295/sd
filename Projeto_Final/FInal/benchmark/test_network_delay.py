import os
import subprocess
import time
import sys 


# ==============================================================================
# CONFIGURAÇÃO

# SE FOR SIMULAR DELAY NA REDE RODE OS COMANDOS ABAIXO EM ORDEM
# 
# export NETWORK_DELAY = 1.5
# python run_cluster.py
# python test_network_delay.py

# ==============================================================================
CLI_UPLOAD_CMD = [sys.executable, "run_cli.py", "put", "teste_rede.txt", "testes/rede.bin"]
FILE_SIZE_MB = 2
# ==============================================================================

def medir_tempo_upload(nome_cenario: str) -> float:
    print(f"\n[Cenário: {nome_cenario}]")
    print(f"-> Iniciando cronômetro...")
    start_time = time.time()
    
    # Roda a sua CLI para subir o arquivo
    subprocess.run(CLI_UPLOAD_CMD, check=True, stdout=subprocess.DEVNULL)
    
    end_time = time.time()
    duracao = end_time - start_time
    print(f"-> ⏱️ Tempo final: {duracao:.2f} segundos")
    return duracao

def main():
    print(f"\n{'='*50}")
    print("📶 INICIANDO BENCHMARK DE LATÊNCIA DE REDE (NETWORK DELAY)")
    print(f"{'='*50}\n")

    print(f"Gerando arquivo de teste ({FILE_SIZE_MB}MB)...")
    with open("teste_rede.txt", "wb") as f:
        f.write(os.urandom(FILE_SIZE_MB * 1024 * 1024))

    # AVISO IMPORTANTE SOBRE A EXECUÇÃO
    print("\n⚠️  ATENÇÃO: Este script mede o tempo que o cliente gasta.")
    print("Para simular o atraso, você deve parar o 'run_cluster.py' atual")
    print("e rodar o cluster novamente passando a variável de ambiente, assim:")
    print("   export NETWORK_DELAY=2.0")
    print("   python run_cluster.py\n")

    input("Pressione ENTER quando o cluster estiver rodando com ou sem atraso para começar...")

    # Realiza múltiplas medições para gerar média (útil para gráficos do relatório)
    num_testes = 3
    tempos = []

    for i in range(1, num_testes + 1):
        tempo = medir_tempo_upload(f"Upload {i}/{num_testes}")
        tempos.append(tempo)

    media = sum(tempos) / len(tempos)
    
    print(f"\n{'='*50}")
    print("📊 RESULTADOS DO BENCHMARK:")
    print(f"   Tempos registrados: {[round(t, 2) for t in tempos]} segundos")
    print(f"   Tempo Médio: {media:.2f} segundos")
    print(f"{'='*50}\n")
    print("Dica para o relatório: Rode este teste com NETWORK_DELAY=0 (rede ideal),")
    print("depois com NETWORK_DELAY=1.0 e NETWORK_DELAY=3.0, e monte um gráfico de barras!")

if __name__ == "__main__":
    main()
import os
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

# Caminhos ancorados na pasta do SCRIPT (Final/benchmark/), não no cwd:
#   - lê o CSV de benchmark/csv/
#   - salva os gráficos em benchmark/graficos/
# Assim o plot roda de qualquer diretório, igual ao harness.
_HERE = Path(__file__).resolve().parent
CSV_FILE = _HERE / "csv" / "resultados_benchmark.csv"
GRAFICOS_DIR = _HERE / "graficos"

# Configuração visual profissional
sns.set_theme(style="whitegrid", palette="muted")

def main():
    if not CSV_FILE.exists():
        print(f"Erro: Arquivo '{CSV_FILE}' não encontrado.")
        print("Rode o benchmark_harness.py primeiro (ele grava em benchmark/csv/).")
        return

    df = pd.read_csv(CSV_FILE)

    # Cria diretório para salvar gráficos
    GRAFICOS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Gráfico de Throughput (Vazão) vs Tamanho do Arquivo
    plt.figure(figsize=(10, 6))
    # NOME DA COLUNA CORRIGIDO: throughput_mbs
    sns.lineplot(data=df, x='tamanho_mb', y='throughput_mbs', hue='operacao', marker='o', linewidth=2.5)
    plt.title('Vazão (Throughput) de Dados por Tamanho de Arquivo', fontsize=14)
    plt.xlabel('Tamanho do Arquivo (MB)', fontsize=12)
    plt.ylabel('Throughput (MB/s)', fontsize=12)
    plt.legend(title='Operação')
    plt.tight_layout()
    plt.savefig(GRAFICOS_DIR / '01_throughput_vs_tamanho.png', dpi=300)
    plt.close()

    # 2. Gráfico de Latência (Tempo) vs Tamanho do Arquivo
    plt.figure(figsize=(10, 6))
    sns.barplot(data=df, x='tamanho_mb', y='tempo_segundos', hue='operacao', errorbar='sd')
    plt.title('Tempo de Execução por Tamanho de Arquivo', fontsize=14)
    plt.xlabel('Tamanho do Arquivo (MB)', fontsize=12)
    plt.ylabel('Tempo (Segundos)', fontsize=12)
    plt.legend(title='Operação')
    plt.tight_layout()
    plt.savefig(GRAFICOS_DIR / '02_latencia_vs_tamanho.png', dpi=300)
    plt.close()

    # 3. Gráfico de Elasticidade: Latência vs Nº de Nós
    # NOME DA COLUNA CORRIGIDO: nos_ativos
    if df['nos_ativos'].nunique() > 1:
        plt.figure(figsize=(10, 6))
        sns.boxplot(data=df[df['operacao'] == 'download'], x='nos_ativos', y='tempo_segundos')
        plt.title('Impacto da Elasticidade na Latência de Leitura (Download)', fontsize=14)
        plt.xlabel('Número de Nós Ativos', fontsize=12)
        plt.ylabel('Tempo (Segundos)', fontsize=12)
        plt.tight_layout()
        plt.savefig(GRAFICOS_DIR / '03_elasticidade_latencia.png', dpi=300)
        plt.close()

    print(f"✅ Gráficos gerados com sucesso em '{GRAFICOS_DIR}'!")

if __name__ == "__main__":
    main()
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import os

# Configuração visual profissional
sns.set_theme(style="whitegrid", palette="muted")

def main():
    csv_file = 'resultados_benchmark.csv'
    if not os.path.exists(csv_file):
        print(f"Erro: Arquivo '{csv_file}' não encontrado.")
        return

    df = pd.read_csv(csv_file)
    
    # Cria diretório para salvar gráficos
    os.makedirs('graficos', exist_ok=True)

    # 1. Gráfico de Throughput (Vazão) vs Tamanho do Arquivo
    plt.figure(figsize=(10, 6))
    # NOME DA COLUNA CORRIGIDO: throughput_mbs
    sns.lineplot(data=df, x='tamanho_mb', y='throughput_mbs', hue='operacao', marker='o', linewidth=2.5)
    plt.title('Vazão (Throughput) de Dados por Tamanho de Arquivo', fontsize=14)
    plt.xlabel('Tamanho do Arquivo (MB)', fontsize=12)
    plt.ylabel('Throughput (MB/s)', fontsize=12)
    plt.legend(title='Operação')
    plt.tight_layout()
    plt.savefig('graficos/01_throughput_vs_tamanho.png', dpi=300)
    plt.close()

    # 2. Gráfico de Latência (Tempo) vs Tamanho do Arquivo
    plt.figure(figsize=(10, 6))
    sns.barplot(data=df, x='tamanho_mb', y='tempo_segundos', hue='operacao', errorbar='sd')
    plt.title('Tempo de Execução por Tamanho de Arquivo', fontsize=14)
    plt.xlabel('Tamanho do Arquivo (MB)', fontsize=12)
    plt.ylabel('Tempo (Segundos)', fontsize=12)
    plt.legend(title='Operação')
    plt.tight_layout()
    plt.savefig('graficos/02_latencia_vs_tamanho.png', dpi=300)
    plt.close()

    # 3. Gráfico de Elasticidade: Latência vs Número de Nós
    # NOME DA COLUNA CORRIGIDO: nos_ativos
    if df['nos_ativos'].nunique() > 1:
        plt.figure(figsize=(10, 6))
        sns.boxplot(data=df[df['operacao'] == 'download'], x='nos_ativos', y='tempo_segundos')
        plt.title('Impacto da Elasticidade na Latência de Leitura (Download)', fontsize=14)
        plt.xlabel('Número de Nós Ativos', fontsize=12)
        plt.ylabel('Tempo (Segundos)', fontsize=12)
        plt.tight_layout()
        plt.savefig('graficos/03_elasticidade_latencia.png', dpi=300)
        plt.close()

    print("✅ Gráficos gerados com sucesso na pasta 'graficos/'!")

if __name__ == "__main__":
    main()
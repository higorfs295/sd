import os
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

# Caminhos ancorados na pasta do script (Final/benchmark/):
#   - lê os CSVs de benchmark/csv/
#   - salva os gráficos em benchmark/graficos/
# Assim o plot roda de qualquer diretório, igual aos benchmarks.
_HERE = Path(__file__).resolve().parent
CSV_VOLUME = _HERE / "csv" / "resultados_volume.csv"
CSV_CONCORRENCIA = _HERE / "csv" / "resultados_concorrencia.csv"
GRAFICOS_DIR = _HERE / "graficos"

# Configuração visual profissional
sns.set_theme(style="whitegrid", palette="muted")


def _rotular_pontos(ax, x, y, dados, fmt="{:.1f}", desloc=(0, 8)):
    """
    Escreve o valor numérico de cada ponto em cima dele, num gráfico de linhas.
    Percorre cada linha do DataFrame e usa annotate para posicionar o texto.
    'desloc' empurra o rótulo alguns pixels para cima, para não cobrir o marcador.
    """
    for _, linha in dados.iterrows():
        valor = linha[y]
        ax.annotate(
            fmt.format(valor),
            xy=(linha[x], valor),
            xytext=desloc,
            textcoords="offset points",
            ha="center",
            fontsize=8,
            fontweight="bold",
        )


def _rotular_barras(ax, fmt="{:.2f}"):
    """
    Escreve o valor numérico em cima de cada barra de um gráfico de barras.
    Usa ax.containers (cada container é um grupo de barras, ex.: upload e download) e o bar_label do matplotlib, que posiciona o rótulo automaticamente.
    """
    for container in ax.containers:
        ax.bar_label(container, fmt=fmt, fontsize=8, fontweight="bold", padding=2)


def plotar_volume(df):
    """Gráficos do benchmark de volume (tamanho de arquivo, em série)."""

    # 1. Throughput (vazão) vs tamanho do arquivo
    plt.figure(figsize=(10, 6))
    ax = sns.lineplot(
        data=df,
        x="tamanho_mb",
        y="throughput_mbs",
        hue="operacao",
        marker="o",
        linewidth=2.5,
    )
    # Rótulos: percorremos a média por (operação, tamanho), para um rótulo por ponto.
    medias = df.groupby(["operacao", "tamanho_mb"], as_index=False)[
        "throughput_mbs"
    ].mean()
    for operacao in medias["operacao"].unique():
        _rotular_pontos(
            ax, "tamanho_mb", "throughput_mbs", medias[medias["operacao"] == operacao]
        )
    plt.title("Vazão (Throughput) de Dados por Tamanho de Arquivo", fontsize=14)
    plt.xlabel("Tamanho do Arquivo (MB)", fontsize=12)
    plt.ylabel("Throughput (MB/s)", fontsize=12)
    plt.legend(title="Operação")
    plt.tight_layout()
    plt.savefig(GRAFICOS_DIR / "01_throughput_vs_tamanho.png", dpi=300)
    plt.close()

    # 2. Tempo (latência) vs tamanho do arquivo
    plt.figure(figsize=(10, 6))
    ax = sns.barplot(
        data=df,
        x="tamanho_mb",
        y="tempo_segundos",
        hue="operacao",
        errorbar="sd",
    )
    _rotular_barras(ax, fmt="{:.2f}")
    plt.title("Tempo de Execução por Tamanho de Arquivo", fontsize=14)
    plt.xlabel("Tamanho do Arquivo (MB)", fontsize=12)
    plt.ylabel("Tempo (Segundos)", fontsize=12)
    plt.legend(title="Operação")
    plt.tight_layout()
    plt.savefig(GRAFICOS_DIR / "02_latencia_vs_tamanho.png", dpi=300)
    plt.close()

    # 3. Elasticidade: latência de leitura vs número de nós
    # Só faz sentido se o CSV tiver mais de um valor de nos_ativos.
    if df["nos_ativos"].nunique() > 1:
        plt.figure(figsize=(10, 6))
        ax = sns.boxplot(
            data=df[df["operacao"] == "download"],
            x="nos_ativos",
            y="tempo_segundos",
        )
        plt.title(
            "Impacto da Elasticidade na Latência de Leitura (Download)", fontsize=14
        )
        plt.xlabel("Número de Nós Ativos", fontsize=12)
        plt.ylabel("Tempo (Segundos)", fontsize=12)
        plt.tight_layout()
        plt.savefig(GRAFICOS_DIR / "03_elasticidade_latencia.png", dpi=300)
        plt.close()


def plotar_concorrencia(df):
    """Gráficos do benchmark de concorrência (taxa de requisições, em paralelo)."""

    # 4. Throughput agregado vs concorrência (linha, um ponto por nível de C)
    plt.figure(figsize=(10, 6))
    ax = sns.lineplot(
        data=df,
        x="concorrencia",
        y="throughput_agregado_mbs",
        hue="operacao",
        marker="o",
        linewidth=2.5,
    )
    for operacao in df["operacao"].unique():
        _rotular_pontos(
            ax,
            "concorrencia",
            "throughput_agregado_mbs",
            df[df["operacao"] == operacao],
        )
    plt.title("Throughput Agregado por Nível de Concorrência", fontsize=14)
    plt.xlabel("Concorrência (clientes simultâneos)", fontsize=12)
    plt.ylabel("Throughput Agregado (MB/s)", fontsize=12)
    plt.legend(title="Operação")
    plt.tight_layout()
    plt.savefig(GRAFICOS_DIR / "04_throughput_vs_concorrencia.png", dpi=300)
    plt.close()

    # 5. Latência média e p95 vs concorrência (mostra a degradação sob carga)
    # Focamos no upload, que é o caminho que sofre com a contenção (replicação síncrona).
    df_up = df[df["operacao"] == "upload"]
    plt.figure(figsize=(10, 6))
    ax = sns.lineplot(
        data=df_up,
        x="concorrencia",
        y="latencia_media_s",
        marker="o",
        linewidth=2.5,
        label="latência média",
    )
    sns.lineplot(
        data=df_up,
        x="concorrencia",
        y="latencia_p95_s",
        marker="s",
        linewidth=2.5,
        label="latência p95",
        ax=ax,
    )
    _rotular_pontos(ax, "concorrencia", "latencia_media_s", df_up)
    _rotular_pontos(ax, "concorrencia", "latencia_p95_s", df_up, desloc=(0, -14))
    plt.title("Latência de Upload por Nível de Concorrência", fontsize=14)
    plt.xlabel("Concorrência (clientes simultâneos)", fontsize=12)
    plt.ylabel("Latência (Segundos)", fontsize=12)
    plt.legend(title="Métrica")
    plt.tight_layout()
    plt.savefig(GRAFICOS_DIR / "05_latencia_vs_concorrencia.png", dpi=300)
    plt.close()

    # 6. Vazão de requisições (req/s) vs concorrência
    plt.figure(figsize=(10, 6))
    ax = sns.lineplot(
        data=df,
        x="concorrencia",
        y="req_por_s",
        hue="operacao",
        marker="o",
        linewidth=2.5,
    )
    for operacao in df["operacao"].unique():
        _rotular_pontos(ax, "concorrencia", "req_por_s", df[df["operacao"] == operacao])
    plt.title("Vazão de Requisições por Nível de Concorrência", fontsize=14)
    plt.xlabel("Concorrência (clientes simultâneos)", fontsize=12)
    plt.ylabel("Requisições por Segundo", fontsize=12)
    plt.legend(title="Operação")
    plt.tight_layout()
    plt.savefig(GRAFICOS_DIR / "06_requisicoes_vs_concorrencia.png", dpi=300)
    plt.close()


def main():
    GRAFICOS_DIR.mkdir(parents=True, exist_ok=True)
    algo_gerado = False

    # Benchmark de volume (resultados_volume.csv)
    if CSV_VOLUME.exists():
        plotar_volume(pd.read_csv(CSV_VOLUME))
        algo_gerado = True
    else:
        print(
            f"Aviso: '{CSV_VOLUME}' não encontrado. Rode o benchmark de volume primeiro."
        )

    # Benchmark de concorrência (resultados_concorrencia.csv)
    if CSV_CONCORRENCIA.exists():
        plotar_concorrencia(pd.read_csv(CSV_CONCORRENCIA))
        algo_gerado = True
    else:
        print(
            f"Aviso: '{CSV_CONCORRENCIA}' não encontrado. Rode o benchmark de concorrência primeiro."
        )

    if algo_gerado:
        print(f"✅ Gráficos gerados com sucesso em '{GRAFICOS_DIR}'!")
    else:
        print("Nenhum CSV encontrado. Rode os benchmarks antes de gerar os gráficos.")


if __name__ == "__main__":
    main()

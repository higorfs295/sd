"""
DESCRIÇÃO GERAL:
Este módulo atua como o ponto central de configurações estáticas do sistema.
Centralizar configurações (como portas, IPs e caminhos de diretórios) facilita 
a manutenção e garante que tanto o cliente quanto o servidor operem sob as 
mesmas premissas sem *hardcoding* (valores fixos espalhados pelo código).
"""

# Importamos a classe Path do módulo pathlib.
# pathlib é a forma moderna e orientada a objetos do Python (desde a versão 3.4) 
# para lidar com caminhos de arquivos e diretórios, substituindo o antigo os.path.
from pathlib import Path

# Endereço IP em que o servidor irá rodar.
# "127.0.0.1" é o endereço de "loopback" (localhost), o que significa que o servidor
# só aceitará conexões vindas da própria máquina. Ideal para testes no Marco 1.
HOST = "127.0.0.1"

# Porta de rede escolhida para o serviço TCP.
# O valor 9099 foi escolhido arbitrariamente, mas está bem acima das portas 
# reservadas pelo sistema operacional (0-1023), evitando conflitos de permissão.
PORT = 9099

# Calcula a raiz do projeto de forma dinâmica.
# __file__ é uma variável mágica do Python que contém o caminho deste arquivo (config.py).
# .resolve() transforma isso em um caminho absoluto (ex: C:\...\DFS_M1\src\dfs\config.py).
# .parents[2] sobe duas pastas na hierarquia:
# 1. sobe de 'dfs' para 'src'
# 2. sobe de 'src' para 'DFS_M1' (raiz do projeto)
BASE_DIR = Path(__file__).resolve().parents[2]

# Define o diretório onde os arquivos serão fisicamente armazenados pelo servidor.
# O operador '/' é sobrecarregado pela classe Path, permitindo concatenar caminhos
# de forma limpa, criando "DFS_M1/data/storage" de forma independente de SO (Windows/Linux).
STORAGE_DIR = BASE_DIR / "data" / "storage"
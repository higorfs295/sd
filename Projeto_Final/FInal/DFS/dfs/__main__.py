"""
DESCRIÇÃO GERAL:
Este é o "Ponto de Entrada" (Entry Point) quando o pacote dfs é chamado como um módulo.
No Python, se um diretório possuir um arquivo `__main__.py` dentro dele,
é possível executar a pasta toda como um comando do Python usando a flag `-m`.
Neste projeto, isso viabiliza comandos limpos como: `python -m dfs get /docs /docs`
"""

# Importa a função main (que inicia o parse da linha de comando) do módulo CLI.
from dfs.interface.cli import main

# Idioma comum em Python: verifica se o arquivo está sendo executado diretamente 
# (e não importado como um módulo em outro arquivo).
if __name__ == "__main__":
    # Inicia e transfere o controle para a interface da linha de comando.
    main()
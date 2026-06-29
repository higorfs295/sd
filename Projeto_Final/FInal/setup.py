from setuptools import setup, find_packages

setup(
    name="dfs",
    version="0.1.0",
    # Diz ao Python: "Procure os pacotes a partir da pasta DFS"
    package_dir={"": "DFS"},
    packages=find_packages(where="DFS"),
)
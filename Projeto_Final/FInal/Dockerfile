FROM python:3.12-slim
WORKDIR /app

# Copia e instala as dependências
COPY DFS/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia todo o projeto para dentro do /app
COPY . .

# =========================================================
# A MÁGICA AQUI: Diz ao Python para considerar a pasta /app/DFS 
# como uma pasta raiz de código. Assim ele encontra o módulo 'dfs'!
# =========================================================
ENV PYTHONPATH="/app/DFS:/app"

# (O CMD do docker-compose vai sobrescrever este na hora de rodar os nós)
CMD ["python", "-m", "dfs.interface.cli"]
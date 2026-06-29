#!/usr/bin/env python
import pika
import socket
import time
from datetime import datetime

# --- Infos que vão na mensagem ---
nome = "higor"
computador = "inspiron 15"
hostname = socket.gethostname()  # nome real da sua máquina na rede

# --- Conexão com o servidor remoto ---
# ATENCAO: o usuario 'guest' (padrao) NAO conecta remotamente.
# Se o dono do servidor te passou um usuario/senha, descomente as
# duas linhas de 'credentials' abaixo e use no ConnectionParameters.
#
# credentials = pika.PlainCredentials('SEU_USUARIO', 'SUA_SENHA')
connection = pika.BlockingConnection(
    pika.ConnectionParameters(
        host='172.16.29.8',
        # credentials=credentials,   # <- descomente quando tiver usuario
    ))
channel = connection.channel()

channel.exchange_declare(exchange='logs', exchange_type='fanout')

try:
    while True:
        timestamp = datetime.now().strftime('%H:%M:%S')
        message = (
            f"are you alive? | nome: {nome} | "
            f"computador: {computador} | hostname: {hostname} | "
            f"hora: {timestamp}"
        )
        channel.basic_publish(exchange='logs', routing_key='', body=message)
        print(f" [x] Sent {message}")
        time.sleep(10)
except KeyboardInterrupt:
    print(" [*] Heartbeat interrompido pelo usuario")
finally:
    connection.close()

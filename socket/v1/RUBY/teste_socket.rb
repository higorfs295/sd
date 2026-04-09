require 'socket'

HOST = 'localhost'
PORT = 9090

puts "Conectando ao servidor em #{HOST}:#{PORT}..."
socket = TCPSocket.new(HOST, PORT)
puts "Conectado com sucesso."
socket.close
puts "Conexão encerrada."
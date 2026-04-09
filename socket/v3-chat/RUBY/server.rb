require 'socket'
require_relative './chat_handler'

PORT = 9092

server = TCPServer.new(PORT)
puts "Servidor escutando na porta #{PORT}..."
puts "Abrindo chat multiusuario..."

loop do
  client = server.accept

  Thread.new(client) do |conn|
    ChatHandler.handle_client(conn)
  end
end
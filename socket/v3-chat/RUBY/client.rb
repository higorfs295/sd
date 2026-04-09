require 'socket'
require 'thread'

HOST = '127.0.0.1'
PORT = 9092

puts "Digite seu nome de usuario:"
username = STDIN.gets&.strip

if username.nil? || username.empty?
  puts "Nome vazio. Encerrando."
  exit
end

puts "Conectando ao servidor em #{HOST}:#{PORT}..."

begin
  socket = TCPSocket.new(HOST, PORT)
rescue Errno::ECONNREFUSED
  puts "Erro: Conexao recusada."
  exit
end

socket.sync = true
socket.write("#{username}\n")

receiver = Thread.new do
  begin
    loop do
      line = socket.gets
      break if line.nil?
      print line
    end
  rescue
  end
end

puts "Digite mensagens. Use /quit para sair."

begin
  while (line = STDIN.gets)
    socket.write(line)
    break if line.strip == "/quit"
  end
rescue Interrupt
  puts "\nSaindo..."
ensure
  begin
    socket.shutdown(Socket::SHUT_RDWR)
  rescue
  end
  socket.close rescue nil
  receiver.join rescue nil
  puts "Conexao encerrada."
end
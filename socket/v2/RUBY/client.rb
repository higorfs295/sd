require 'socket'

HOST = 'localhost'
PORT = 9092

puts "Conectando ao servidor em #{HOST}:#{PORT}..."

begin
  socket = TCPSocket.new(HOST, PORT)
rescue Errno::ECONNREFUSED
  puts "Erro: Conexão recusada."
  exit
end

# Thread para ler do servidor assincronamente
receiver = Thread.new do
  begin
    loop do
      line = socket.gets
      break if line.nil?
      print line
    end
  rescue
    # Evita crash ao fechar o socket abruptamente
  end
end

puts "Digite mensagens. Ctrl+C para sair."

begin
  while (line = STDIN.gets)
    socket.write(line)
  end
rescue Interrupt
  puts "\nSaindo..."
ensure
  socket.close rescue nil
  receiver.join rescue nil
  puts "Conexão encerrada."
end
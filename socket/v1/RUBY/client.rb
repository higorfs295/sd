require 'socket'

HOST = '127.0.0.1'
PORT = 9090

puts "Conectando ao servidor em #{HOST}:#{PORT}..."
socket = TCPSocket.new(HOST, PORT)

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
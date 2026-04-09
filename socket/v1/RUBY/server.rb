require 'socket'

PORT = 9090

server = TCPServer.new(PORT)
puts "Escutando a porta #{PORT}..."

loop do
  client = server.accept
  puts "Cliente conectado."

  begin
    loop do
      line = client.gets
      break if line.nil?

      print "Recebido: #{line}"
      client.write(line)
    end
  rescue => e
    warn "Erro: #{e.message}"
  ensure
    client.close
    puts "Cliente desconectou. Voltando a escutar..."
  end
end
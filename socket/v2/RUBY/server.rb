require 'socket'

PORT = 9092

server = TCPServer.new(PORT)
puts "Escutando a porta #{PORT} (Modo Concorrente)..."

loop do
  # Aceita a conexão
  client = server.accept
  
  # Cria uma thread dedicada para o cliente
  Thread.new(client) do |conn|
    puts "[+] Cliente conectado."
    begin
      loop do
        line = conn.gets
        break if line.nil?

        print "Recebido: #{line}"
        conn.write(line) # Echo
      end
    rescue => e
      warn "[-] Erro com cliente: #{e.message}"
    ensure
      conn.close
      puts "[-] Cliente desconectou."
    end
  end
end
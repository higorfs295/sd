# frozen_string_literal: true

require 'json'
require 'socket'
require 'base64'

# =============================================================================
# protocol.rb — Camada de transporte da variante Ruby.
#
# Adaptação: no projeto original a comunicação usa gRPC (unário no plano de
# controle, streaming no plano de dados). Aqui usamos um protocolo simples,
# porém equivalente em papel: uma requisição = um objeto JSON numa linha, e uma
# resposta = um objeto JSON numa linha, sobre TCP. Bytes de chunk viajam como
# string base64 dentro do JSON. Isso preserva a natureza cliente/servidor e a
# separação controle/dados sem a dependência de gRPC.
# =============================================================================

module DFS
  module Protocol
    module_function

    # Envia uma requisição a host:port e devolve a resposta (Hash) decodificada.
    # Abre a conexão, escreve uma linha JSON, lê uma linha JSON, fecha.
    def request(host, port, payload, timeout = 30)
      sock = Socket.tcp(host, port, connect_timeout: timeout)
      sock.write(JSON.generate(payload) + "\n")
      line = read_line(sock, timeout)
      raise IOError, "conexão fechada por #{host}:#{port}" if line.nil?

      JSON.parse(line)
    ensure
      sock&.close
    end

    # Lê exatamente uma linha (terminada por \n) do socket.
    def read_line(sock, timeout = 30)
      buffer = +''
      loop do
        chunk = sock.read_nonblock(65_536, exception: false)
        case chunk
        when :wait_readable
          ready = IO.select([sock], nil, nil, timeout)
          return nil if ready.nil? # timeout
        when nil
          return nil # EOF
        else
          buffer << chunk
          idx = buffer.index("\n")
          return buffer[0...idx] if idx
        end
      end
    rescue EOFError
      nil
    end

    # Serve conexões TCP numa porta. Para cada conexão lê UMA requisição JSON,
    # chama o bloco handler e devolve a resposta JSON. Cada conexão em sua thread.
    def serve(host, port)
      server = TCPServer.new(host, port)
      loop do
        client = server.accept
        Thread.new(client) do |sock|
          begin
            line = read_line(sock, 60)
            next if line.nil?

            req = JSON.parse(line)
            resp = yield(req) # handler
            sock.write(JSON.generate(resp) + "\n")
          rescue StandardError => e
            begin
              sock.write(JSON.generate('ok' => false, 'error' => e.message) + "\n")
            rescue StandardError
              # cliente já foi embora
            end
          ensure
            sock.close
          end
        end
      end
    end

    def encode_bytes(bytes)
      Base64.strict_encode64(bytes)
    end

    def decode_bytes(str)
      Base64.strict_decode64(str)
    end
  end
end

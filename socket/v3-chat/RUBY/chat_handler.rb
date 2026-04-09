require 'thread'
require 'time'

module ChatHandler
  MAX_CLIENTS = 64

  @@clients = []
  @@mutex = Mutex.new

  class << self
    def timestamp
      Time.now.strftime("%Y-%m-%d %H:%M:%S")
    end

    def broadcast(message)
      sockets = []

      @@mutex.synchronize do
        sockets = @@clients.map { |c| c[:socket] }
      end

      sockets.each do |sock|
        begin
          sock.write("#{message}\n")
        rescue
        end
      end
    end

    def online_list
      @@mutex.synchronize do
        @@clients.map { |c| c[:name] }.join(" ")
      end
    end

    def register_client(sock, name)
      @@mutex.synchronize do
        return false if @@clients.length >= MAX_CLIENTS
        return false if @@clients.any? { |c| c[:name] == name }

        @@clients << { socket: sock, name: name }
        true
      end
    end

    def remove_client(sock)
      removed = nil

      @@mutex.synchronize do
        idx = @@clients.index { |c| c[:socket] == sock }
        if idx
          removed = @@clients[idx][:name]
          @@clients.delete_at(idx)
        end
      end

      removed
    end

    def handle_client(conn)
      name = nil

      begin
        name = conn.gets
        if name.nil?
          conn.close
          return
        end

        name = name.strip
        if name.empty?
          conn.write("Servidor: nome de usuario vazio.\n")
          conn.close
          return
        end

        unless register_client(conn, name)
          conn.write("Servidor: nome ja em uso ou sala cheia.\n")
          conn.close
          return
        end

        conn.write("Servidor: bem-vindo, #{name}!\n")
        current_online = online_list
        conn.write("Servidor: usuarios online agora -> #{current_online.empty? ? '(ninguem)' : current_online}\n")

        puts "[#{timestamp}] #{name} entrou no chat."
        broadcast("Servidor: [#{name}] entrou no chat.")

        loop do
          line = conn.gets
          break if line.nil?

          msg = line.gsub(/\r?\n/, "")
          next if msg.empty?
          break if msg == "/quit"

          message = "[#{timestamp}] #{name}: #{msg}"
          puts message
          broadcast(message)
        end
      rescue => e
        warn "[-] Erro com cliente: #{e.message}"
      ensure
        removed = remove_client(conn)
        if removed
          puts "[#{timestamp}] #{removed} saiu do chat."
          broadcast("Servidor: [#{removed}] saiu do chat.")
        end
        conn.close rescue nil
      end
    end
  end
end
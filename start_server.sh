#!/bin/bash
# Script para iniciar o servidor uvicorn sem problemas de timeout
# Uso: ./start_server.sh

cd "$(dirname "$0")"
source venv/bin/activate

# Verificar e parar processos existentes na porta 8234
if lsof -Pi :8234 -sTCP:LISTEN -t >/dev/null 2>&1 ; then
    echo "Porta 8234 já está em uso. Parando processos existentes..."
    pkill -f "uvicorn app:app" 2>/dev/null
    sleep 2
fi

# Verificar e liberar bloqueio do banco de dados se necessário
if [ -f "./crm-agent.db-wal" ] || [ -f "./crm-agent.db-shm" ]; then
    echo "Verificando bloqueios do banco de dados..."
    # Remover arquivos de lock temporários se existirem
    rm -f ./crm-agent.db-wal ./crm-agent.db-shm 2>/dev/null
fi

# Iniciar o servidor com timeouts aumentados para permitir respostas longas
echo "Iniciando servidor uvicorn..."
uvicorn app:app --host 0.0.0.0 --port 8234 --reload \
  --timeout-keep-alive 300 \
  --timeout-graceful-shutdown 30 \
  --ws-ping-interval 30 \
  --ws-ping-timeout 60 \
  > /tmp/backend-test.log 2>&1 &
SERVER_PID=$!

# Aguardar um pouco e verificar se iniciou corretamente
sleep 3
if curl -s http://localhost:8234/health >/dev/null 2>&1; then
    echo "✅ Servidor iniciado com sucesso na porta 8234 (PID: $SERVER_PID)"
    echo "Logs disponíveis em: /tmp/backend-test.log"
    echo "Para parar o servidor: pkill -f 'uvicorn app:app'"
else
    echo "⚠️  Servidor pode não ter iniciado corretamente. Verifique os logs:"
    tail -30 /tmp/backend-test.log
    echo ""
    echo "Para ver os logs em tempo real: tail -f /tmp/backend-test.log"
fi

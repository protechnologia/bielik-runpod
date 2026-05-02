#!/bin/bash
set -e

export MODEL="${MODEL:-SpeakLeash/bielik-11b-v3.0-instruct:Q8_0}"
export EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text}"
export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
export OLLAMA_MODELS="${OLLAMA_MODELS:-/root/data/ollama}"
export QDRANT_PATH="${QDRANT_PATH:-/root/data/qdrant}"
export PYTHONPATH="/tmp/init/api"

echo ">>> Instalacja Pythona..."
apt-get update -qq && apt-get install -y python3 python3-pip

cd /tmp/init

echo ">>> Instalacja zależności Python..."
pip install -q -r api/requirements.txt

echo ">>> Start Ollama..."
OLLAMA_MODELS="$OLLAMA_MODELS" ollama serve &

echo ">>> Czekam na gotowość Ollama..."
until curl -s "$OLLAMA_URL/api/tags" > /dev/null 2>&1; do
    sleep 2
done
echo ">>> Ollama gotowa."

echo ">>> Pobieranie modelu: $MODEL ..."
ollama pull "$MODEL"

echo ">>> Pobieranie modelu embeddingów: $EMBED_MODEL ..."
ollama pull "$EMBED_MODEL"

echo ">>> Start API..."
uvicorn api.main:app --host 0.0.0.0 --port 8000

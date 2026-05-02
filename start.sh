#!/bin/bash
set -e

echo ">>> Instalacja Pythona..."
apt-get update -qq && apt-get install -y python3 python3-pip

cd /tmp/init

echo ">>> Instalacja zależności Python..."
pip install -q -r api/requirements.txt

echo ">>> Start Ollama..."
OLLAMA_MODELS=/root/data/ollama ollama serve &
sleep 15

echo ">>> Pobieranie modelu Bielik 11B v3.0..."
ollama pull SpeakLeash/bielik-11b-v3.0-instruct:Q8_0

echo ">>> Pobieranie modelu embeddingów (nomic-embed-text)..."
ollama pull nomic-embed-text

echo ">>> Start API..."
OLLAMA_URL=http://localhost:11434 \
OLLAMA_MODELS=/root/data/ollama \
MODEL=SpeakLeash/bielik-11b-v3.0-instruct:Q8_0 \
EMBED_MODEL=nomic-embed-text \
QDRANT_PATH=/root/data/qdrant \
PYTHONPATH=/tmp/init/api \
uvicorn api.main:app --host 0.0.0.0 --port 8000
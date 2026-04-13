#!/bin/bash
set -e

echo ">>> Instalacja Pythona..."
apt-get update -qq && apt-get install -y python3 python3-pip

echo ">>> Instalacja zależności Python..."
cd /tmp/init/api
pip install -q -r requirements.txt

echo ">>> Start Ollama..."
ollama serve &
sleep 15

echo ">>> Pobieranie modelu Bielik 11B v3.0..."
ollama pull SpeakLeash/bielik-11b-v3.0-instruct:Q8_0

echo ">>> Start API..."
OLLAMA_URL=http://localhost:11434 \
MODEL=SpeakLeash/bielik-11b-v3.0-instruct:Q8_0 \
uvicorn main:app --host 0.0.0.0 --port 8000

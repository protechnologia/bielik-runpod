#!/bin/bash
set -e

echo ">>> Instalacja Pythona..."
apt-get update -qq && apt-get install -y python3 python3-pip git

echo ">>> Klonowanie repo..."
git clone https://github.com/protechnologia/bielik-runpod /workspace/bielik
cd /workspace/bielik/api

echo ">>> Instalacja zależności Python..."
pip install -q -r requirements.txt

echo ">>> Start Ollama..."
ollama serve &
sleep 8

echo ">>> Pobieranie modelu Bielik..."
ollama pull SpeakLeash/bielik-1.5b-v3.0-instruct:Q8_0

echo ">>> Start API..."
OLLAMA_URL=http://localhost:11434 \
MODEL=SpeakLeash/bielik-1.5b-v3.0-instruct:Q8_0 \
uvicorn main:app --host 0.0.0.0 --port 8000
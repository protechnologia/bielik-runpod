# bielik-runpod

Stack: Ollama + Bielik 11B v3.0 Q8_0 + Qdrant (RAG) + Docling (PDF) + Python REST API. Uruchamiany na RunPod przez on-start script.

---

## Struktura repo

```
bielik-runpod/
├── api/
│   ├── main.py
│   └── requirements.txt
└── start.sh
```

---

## Struktura Volume (`/root/data`)

```
/root/data/
├── ollama/      ← modele Ollama (Bielik, nomic-embed-text)
├── qdrant/      ← baza wektorowa Qdrant (dokumenty RAG)
└── hf_cache/    ← modele Docling/TableFormer (HuggingFace)
```

Oba katalogi persystują na Volume i przeżywają Terminate Poda.

---

## Tworzenie Template na RunPod

**Manage → My Templates → New Template**

| Pole | Wartość |
|---|---|
| Template Name | `Bielik-11B-v3-Q8` |
| Container Image | `runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04` |
| Container Start Command | *(patrz niżej)* |
| Expose HTTP Ports | `8000` |
| Expose TCP Ports | `22` |
| Container Disk | `20 GB` |
| Volume Disk | `35 GB` |
| Volume Mount Path | `/root/data` |

> ⚠️ Zmiana Volume Mount Path z `/root/.ollama` na `/root/data` wymaga **Terminate** istniejącego Poda i stworzenia nowego z nowym Template.

**Container Start Command:**
```
bash -c "apt-get update && apt-get install -y curl git zstd && curl -fsSL https://ollama.com/install.sh | sh && rm -rf /tmp/init && git clone --depth=1 https://github.com/protechnologia/bielik-runpod /tmp/init && bash /tmp/init/start.sh"
```

---

## Uruchamianie Poda

- **GPU:** RTX 4090
- **Cloud:** Secure Cloud (On Demand) — do prezentacji; Community Cloud — do testów
- **GPU Count:** 1

Pierwsze uruchomienie trwa ~15 minut (pobieranie modeli ~12 GB Bielik + ~274 MB nomic-embed-text + ~200 MB docling/TableFormer na Volume).  
Kolejne uruchomienia ~2 minuty — modele już są na Volume.

---

## Zmienne środowiskowe (w start.sh)

| Zmienna | Wartość |
|---|---|
| `OLLAMA_URL` | `http://localhost:11434` |
| `OLLAMA_MODELS` | `/root/data/ollama` |
| `MODEL` | `SpeakLeash/bielik-11b-v3.0-instruct:Q8_0` |
| `EMBED_MODEL` | `nomic-embed-text` |
| `QDRANT_PATH` | `/root/data/qdrant` |
| `HF_HOME` | `/root/data/hf_cache` |

---

## Endpointy API

| Metoda | Endpoint | Opis |
|---|---|---|
| `GET` | `/health` | Status Ollamy, modeli i kolekcji Qdrant |
| `POST` | `/ask` | Generowanie odpowiedzi (opcjonalnie z RAG) |
| `POST` | `/ingest` | Dodawanie chunków tekstowych do bazy wektorowej |
| `POST` | `/ingest/pdf` | Wgranie pliku PDF — automatyczny chunking i zapis do Qdrant |
| `GET` | `/collections` | Lista kolekcji Qdrant z liczbą wektorów |
| `DELETE` | `/collections/{name}` | Usunięcie kolekcji |
| `GET` | `/models` | Lista modeli załadowanych w Ollama |
| `POST` | `/pull` | Pobranie modelu przez Ollama |

---

## Test

URL Poda dostępny w panelu RunPod: **Connect → HTTP Service [Port 8000]**

**Zwykłe zapytanie:**
```bash
curl -X POST https://{POD_ID}-8000.proxy.runpod.net/ask \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Czym jest spółdzielnia energetyczna? Odpowiedz w 2 zdaniach."}'
```

**Wgranie PDF do bazy wektorowej:**
```bash
curl -X POST https://{POD_ID}-8000.proxy.runpod.net/ingest/pdf \
  -F "file=@ustawa_oze.pdf" \
  -F "collection=documents"
```

Przykładowa odpowiedź:
```json
{
  "filename": "ustawa_oze.pdf",
  "pages": 48,
  "chunks": 213,
  "ingested": 213,
  "collection": "documents"
}
```

**Dodanie pojedynczych tekstów do bazy wektorowej:**
```bash
curl -X POST https://{POD_ID}-8000.proxy.runpod.net/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "texts": [
      "Spółdzielnia energetyczna to forma organizacyjna zrzeszająca prosumentów.",
      "Członkowie spółdzielni mogą wspólnie produkować i rozliczać energię elektryczną."
    ],
    "metadata": [
      {"source": "ustawa_oze.pdf", "page": 12},
      {"source": "ustawa_oze.pdf", "page": 13}
    ]
  }'
```

**Zapytanie z RAG:**
```bash
curl -X POST https://{POD_ID}-8000.proxy.runpod.net/ask \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Kto może być członkiem spółdzielni?", "rag": true, "rag_top_k": 3}'
```

Przykładowa odpowiedź `/ask` z RAG:
```json
{
  "answer": "Członkiem spółdzielni energetycznej może być prosument...",
  "model": "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0",
  "time_total_s": 14.2,
  "time_to_first_token_s": 1.5,
  "tokens_generated": 104,
  "tokens_per_second": 7.3,
  "rag_chunks_used": 2
}
```

Swagger UI: `https://{POD_ID}-8000.proxy.runpod.net/docs`

---

## Uwagi

- Zmiany w Template wymagają **Terminate** istniejącego Poda i stworzenia nowego.
- Volume (`/root/data`) przeżywa Terminate — modele i dane Qdrant nie muszą być pobierane ponownie.
- `rm -rf /tmp/init` w start command zabezpiecza przed błędem przy ponownym starcie na tym samym węźle.
- Container Disk kasuje się przy każdym Stop — `git clone` i `pip install` wykonują się przy każdym starcie (~60 sek.).
- Volume Disk zwiększony do **35 GB**: ~12 GB Bielik + ~274 MB nomic-embed-text + ~200 MB docling/TableFormer + margines na dane Qdrant.

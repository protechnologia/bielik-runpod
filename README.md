# bielik-runpod

Stack: Ollama + Bielik 11B v3.0 Q8_0 + Qdrant (RAG) + Python REST API. Uruchamiany na RunPod przez on-start script.

---

## Struktura repo

```
bielik-runpod/
├── api/
│   ├── __init__.py
│   ├── main.py           ← endpointy FastAPI (każdy to 1-2 linie)
│   ├── config.py         ← stałe konfiguracyjne
│   ├── schemas.py        ← modele Pydantic requestów i odpowiedzi API
│   ├── ollama_client.py  ← klient HTTP do Ollamy (embed, generate, pull, check)
│   ├── qdrant_store.py   ← operacje na kolekcjach i wektorach Qdrant
│   ├── rag_retriever.py  ← embed zapytania → search → budowa kontekstu RAG
│   ├── ask_pipeline.py   ← orkiestracja: RAG → generowanie → metryki → AskResponse
│   ├── xlsx_ingester.py  ← walidacja, chunkowanie i indeksowanie plików XLSX
│   ├── xlsx_chunker.py   ← parsowanie XLSX na chunki tekstowe
│   └── requirements.txt
├── cli/
│   └── cli_xlsx_chunker.py
├── test/
│   └── test_xlsx_chunker.py
└── start.sh
```

---

## Struktura Volume (`/root/data`)

```
/root/data/
├── ollama/      ← modele Ollama (Bielik, nomic-embed-text)
└── qdrant/      ← baza wektorowa Qdrant (dokumenty RAG)
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

Pierwsze uruchomienie trwa ~13 minut (pobieranie modeli ~12 GB Bielik + ~274 MB nomic-embed-text na Volume).  
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

---

## Endpointy API

| Metoda | Endpoint | Opis |
|---|---|---|
| `GET` | `/health` | Status Ollamy, modeli i kolekcji Qdrant |
| `POST` | `/ask` | Generowanie odpowiedzi (opcjonalnie z RAG) |
| `POST` | `/ingest/xlsx` | Wgranie pliku XLSX — automatyczny chunking i zapis do Qdrant |
| `POST` | `/inspect/xlsx` | Wgranie pliku XLSX — podgląd chunków bez zapisu do Qdrant |
| `GET` | `/collections` | Lista kolekcji Qdrant z liczbą wektorów |
| `DELETE` | `/collections/{name}` | Usunięcie kolekcji |
| `GET` | `/models` | Lista modeli załadowanych w Ollama |
| `POST` | `/pull` | Pobranie modelu przez Ollama |

---

## Test

URL Poda dostępny w panelu RunPod: **Connect → HTTP Service [Port 8000]**

**Health check:**
```bash
curl https://{POD_ID}-8000.proxy.runpod.net/health
```

Przykładowa odpowiedź:
```json
{
  "status": "ok",
  "ollama": {
    "reachable": true,
    "model": "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0",
    "model_ready": true,
    "embed_model": "nomic-embed-text",
    "embed_ready": true,
    "available_models": [
      "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0",
      "nomic-embed-text:latest"
    ]
  },
  "qdrant": {
    "collections": ["documents"]
  }
}
```

**Zwykłe zapytanie:**
```bash
curl -X POST https://{POD_ID}-8000.proxy.runpod.net/ask \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Czym jest spółdzielnia energetyczna? Odpowiedz w 2 zdaniach."}'
```

**Wgranie XLSX do bazy wektorowej:**
```bash
curl -X POST https://{POD_ID}-8000.proxy.runpod.net/ingest/xlsx \
  -F "file=@rejestry.xlsx" \
  -F "source_label=ORNO OR-WE-516" \
  -F "collection=documents" \
  -F "rows_per_chunk=50"
```

Przykładowa odpowiedź:
```json
{
  "filename": "rejestry.xlsx",
  "sheets": 2,
  "chunks": 6,
  "ingested": 6,
  "collection": "documents"
}
```

**Podgląd chunków XLSX (bez zapisu do bazy):**
```bash
curl -X POST https://{POD_ID}-8000.proxy.runpod.net/inspect/xlsx \
  -F "file=@rejestry.xlsx" \
  -F "source_label=ORNO OR-WE-516" \
  -F "rows_per_chunk=50"
```

**Zapytanie z RAG:**
```bash
curl -X POST https://{POD_ID}-8000.proxy.runpod.net/ask \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Jakie jest napięcie znamionowe?", "rag": true, "rag_top_k": 3, "rag_score_threshold": 0.3}'
```

Przykładowa odpowiedź `/ask` z RAG:
```json
{
  "answer": "Napięcie znamionowe licznika ORNO OR-WE-516 wynosi 3x230/400V.",
  "model": "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0",
  "time_total_s": 14.2,
  "time_to_first_token_s": 1.5,
  "tokens_generated": 104,
  "tokens_per_second": 7.3,
  "rag_chunks_used": 2,
  "rag_chunks": [
    {
      "index": 1,
      "score": 0.8731,
      "source_label": "ORNO OR-WE-516",
      "sheet": "Rejestry odczytu",
      "text": "ORNO OR-WE-516 / Rejestry odczytu\n\nAdres | Nazwa | ..."
    }
  ]
}
```

Swagger UI: `https://{POD_ID}-8000.proxy.runpod.net/docs`

---

## Narzędzia lokalne

### cli_xlsx_chunker.py

Skrypt CLI do lokalnego testowania chunkera bez uruchamiania API. Parsuje plik XLSX przez `XlsxChunker` i wyświetla wynik analogiczny do endpointu `/inspect/xlsx`.

```bash
python cli/cli_xlsx_chunker.py <plik.xlsx> <source_label> [--rows-per-chunk N]
```

Przykład:
```bash
python cli/cli_xlsx_chunker.py rejestry.xlsx "ORNO OR-WE-516" --rows-per-chunk 30
```


Przykładowy output:
```
════════════════════════════════════════════════════════════════════════
  Plik:          rejestry.xlsx
  Source label:  ORNO OR-WE-516
  Rows per chunk:30
  Arkusze:       2  (Rejestry odczytu, Rejestry zapisu)
  Chunków łącznie: 8
════════════════════════════════════════════════════════════════════════

────────────────────────────────────────────────────────────────────────
  Chunk #1  |  Arkusz: Rejestry odczytu  |  Część: 1
  Znaki: 1842   Słowa: 312
────────────────────────────────────────────────────────────────────────
ORNO OR-WE-516 / Rejestry odczytu

Adres | Nazwa | ...
--- | --- | ...
...

════════════════════════════════════════════════════════════════════════
  Podsumowanie: 8 chunków | 14736 znaków | 2496 słów
════════════════════════════════════════════════════════════════════════
```

---

## Testy

Testy jednostkowe dla klasy `XlsxChunker` znajdują się w `test/test_xlsx_chunker.py`.

```bash
pip install pytest
pytest test/test_xlsx_chunker.py -v
```

---

## TODO

### Jakość RAG
- [ ] Reranking — hybrid search BM25 + cosine łączony przez RRF (Reciprocal Rank Fusion, k=60)
- [ ] Lepszy model embeddingów (np. `multilingual-e5-large`)
- [ ] HyDE — model generuje hipotetyczną odpowiedź, jej embedding idzie do Qdrant
- [ ] Osobne kolekcje per urządzenie

### Architektura i produkcyjność
- [ ] Asynchroniczny ingest + endpoint `/tasks/{id}` ze statusem
- [ ] Autoryzacja — API key
- [ ] Obsługa duplikatów przy ponownym wgraniu tego samego pliku
- [ ] Auto-wybór kolekcji przez embedding
- [ ] Prosty frontend

---

## Uwagi

- Zmiany w Template wymagają **Terminate** istniejącego Poda i stworzenia nowego.
- Volume (`/root/data`) przeżywa Terminate — modele i dane Qdrant nie muszą być pobierane ponownie.
- `rm -rf /tmp/init` w start command zabezpiecza przed błędem przy ponownym starcie na tym samym węźle.
- Container Disk kasuje się przy każdym Stop — `git clone` i `pip install` wykonują się przy każdym starcie (~60 sek.).
- Volume Disk ustawiony na **35 GB**: ~12 GB Bielik + ~274 MB nomic-embed-text + margines na dane Qdrant.
# bielik-runpod

REST API do uruchamiania polskiego modelu językowego **Bielik 11B v3.0** z obsługą RAG (Retrieval-Augmented Generation). Projekt jest zoptymalizowany pod RunPod z GPU RTX 4090, ale działa też lokalnie.

**Możliwości:**
- Generowanie odpowiedzi w języku polskim przez Bielik 11B
- RAG — odpowiedzi oparte na własnych dokumentach wgranych jako pliki XLSX
- Automatyczne chunkowanie i indeksowanie XLSX do bazy wektorowej Qdrant
- Wyszukiwanie semantyczne przez embeddingi `nomic-embed-text` z opcjonalnym rerankingiem BM25
- REST API z dokumentacją Swagger UI
- Narzędzia lokalne do budowania i ewaluacji golden setu (Recall@k, MRR)

**Stack:** Ollama + Bielik 11B v3.0 Q8_0 + Qdrant + FastAPI. Uruchamiany na RunPod przez on-start script.

---

## Pipeline RAG

Zapytanie użytkownika przechodzi przez trzy etapy:

1. **Embedder** (`nomic-embed-text`) — zamienia zapytanie na wektor i wyszukuje semantycznie podobne chunki w Qdrant.
2. **BM25 reranker** — spośród kandydatów z etapu 1. reankuje przez dopasowanie słów kluczowych, łącząc oba rankingi metodą RRF (Reciprocal Rank Fusion).
3. **LLM** (Bielik 11B) — generuje odpowiedź na podstawie wybranych fragmentów jako kontekst.

**Dlaczego same embeddingi nie wystarczają — wyzwanie terminów technicznych:**

Dokumenty z dziedziny automatyki przemysłowej są pełne symboli modeli, np. `OR-WE-520`, `SDM630`. Takie terminy stwarzają dwa niezależne problemy:

- **Embedder** uczy się reprezentacji semantycznych z dużych korpusów tekstu, w których symbole techniczne pojawiają się rzadko i bez kontekstu. Model słabo odróżnia `OR-WE-516` od `OR-WE-520` — oba wyglądają jak ciągi znaków o zbliżonej strukturze, więc ich wektory są do siebie podobne, choć dotyczą różnych urządzeń.
- **BM25** wymaga dokładnego dopasowania tokenów. Symbole techniczne zawierają separatory (myślniki, ukośniki, spacje). Użytkownik może wpisać ten sam symbol na różne sposoby: `OR-WE-520`, `ORWE520` lub samo `520` — każdy zapis powinien trafić na właściwy chunk.

Projekt rozwiązuje oba problemy: embeddingi zapewniają trafność semantyczną, a BM25 z wielopoziomową tokenizacją poprawia precyzję dla symboli technicznych.

**Tokenizacja BM25** działa wielopoziomowo, żeby symbol pasował niezależnie od tego, jak zostanie wpisany w zapytaniu:

1. Normalizacja: małe litery + usunięcie polskich diakrytyków (`Hasło` → `haslo`, `żółw` → `zolw`, `ł` → `l`).
2. Podział na segmenty alfanumeryczne — separatory są odrzucane (`OR-WE-520` → `["or", "we", "520"]`).
3. Każdy segment mieszający litery i cyfry jest dodatkowo rozbijany na części (`SDM630` → `["sdm", "630"]`, `3x230` → `["3", "x", "230"]`).
4. Gdy segmentów jest więcej niż jeden, do tokenów trafia też całe oryginalne słowo i konkatenacja segmentów (`OR-WE-520` → dodaje `"or-we-520"` i `"orwe520"`).
5. Tokeny jednoznakowe są odrzucane — eliminują szum z jednostek i skrótów (`"V"` z `400V`, `"x"` z `3x230`).

Przykłady:
```
"OR-WE-520"  → ["or", "we", "520", "or-we-520", "orwe520"]
"SDM630"     → ["sdm630", "sdm", "630"]
"3x230/400V" → ["3x230", "230", "400v", "400", "3x230/400v", "3x230400v"]
```

Dzięki temu zapytanie `"520"` trafi na chunk zawierający `"OR-WE-520"`, a zapytanie `"or-we-520"` dopasuje się zarówno przez całe słowo, jak i przez segmenty.

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
│   ├── bm25_reranker.py  ← reranking kandydatów z Qdrant metodą BM25
│   ├── rag_retriever.py  ← embed zapytania → search → (BM25) → budowa kontekstu RAG
│   ├── ask_pipeline.py   ← orkiestracja: RAG → generowanie → metryki → AskResponse
│   ├── xlsx_ingester.py  ← walidacja, chunkowanie i indeksowanie plików XLSX
│   ├── xlsx_chunker.py   ← parsowanie XLSX na chunki tekstowe
│   └── requirements.txt
├── cli/
│   ├── cli_xlsx_chunker.py  ← podgląd chunków XLSX lokalnie (bez API)
│   └── cli_golden_set.py    ← generowanie golden setu do ewaluacji RAG
├── data/
│   ├── xlsx/
│   │   ├── ORNO OR-WE-520.xlsx      ← mapa rejestrów Modbus licznika ORNO OR-WE-520
│   │   └── EASTRON SDM630.xlsx      ← mapa rejestrów Modbus licznika EASTRON SDM630
│   ├── golden_set.json              ← golden set z promptami ogólnymi (ORNO + EASTRON)
│   └── golden_set_unique.json       ← golden set z promptami kierowanymi (ORNO + EASTRON)
├── test/
│   ├── test_xlsx_chunker.py     ← testy jednostkowe XlsxChunker
│   └── eval_retriever.py        ← ewaluacja retrievera: embedder lub embedder + BM25 (Recall@k, MRR)
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

## Weryfikacja API

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

**Zapytanie bez RAG:**
```bash
curl -X POST https://{POD_ID}-8000.proxy.runpod.net/ask \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Czym jest spółdzielnia energetyczna? Odpowiedz w 2 zdaniach."}'
```

Przykładowa odpowiedź:
```json
{
  "answer": "Spółdzielnia energetyczna to...",
  "model": "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0",
  "time_total_s": 12.1,
  "time_to_first_token_s": 1.3,
  "tokens_generated": 87,
  "tokens_per_second": 7.2,
  "rag_chunks_used": null,
  "rag_chunks": null
}
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

Przykładowa odpowiedź:
```json
{
  "filename": "rejestry.xlsx",
  "sheets": 2,
  "chunks": 4,
  "items": [
    {
      "index": 1,
      "sheet": "Rejestry odczytu",
      "chunk": 1,
      "text": "ORNO OR-WE-516 / Rejestry odczytu\n\nAdres | Nazwa | ...",
      "char_count": 1842,
      "word_count": 312
    }
  ]
}
```

**Zapytanie z RAG i BM25:**
```bash
curl -X POST https://{POD_ID}-8000.proxy.runpod.net/ask \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Jakie jest napięcie znamionowe licznika ORNO OR-WE-516?", "rag": true, "rag_top_k": 3, "rag_score_threshold": 0.3, "bm25_candidates": 20}'
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

**Lista kolekcji Qdrant:**
```bash
curl https://{POD_ID}-8000.proxy.runpod.net/collections
```

Przykładowa odpowiedź:
```json
[
  {"name": "documents", "vectors_count": 6}
]
```

**Usunięcie kolekcji:**
```bash
curl -X DELETE https://{POD_ID}-8000.proxy.runpod.net/collections/documents
```

Przykładowa odpowiedź:
```json
{"deleted": "documents"}
```

**Lista modeli Ollama:**
```bash
curl https://{POD_ID}-8000.proxy.runpod.net/models
```

Przykładowa odpowiedź:
```json
{
  "models": [
    {
      "name": "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0",
      "size": 11800000000,
      "digest": "sha256:..."
    },
    {
      "name": "nomic-embed-text:latest",
      "size": 274000000,
      "digest": "sha256:..."
    }
  ]
}
```

**Pobranie modelu przez Ollama:**
```bash
curl -X POST https://{POD_ID}-8000.proxy.runpod.net/pull \
  -H "Content-Type: application/json" \
  -d '{"model": "nomic-embed-text"}'
```

Bez ciała requestu pobiera domyślny model generowania (z `config.MODEL`):
```bash
curl -X POST https://{POD_ID}-8000.proxy.runpod.net/pull
```

Przykładowa odpowiedź:
```json
{"status": "pulled", "model": "nomic-embed-text"}
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

### cli_golden_set.py

Skrypt CLI do budowania golden setu dla ewaluacji RAG. Ładuje jeden lub więcej plików XLSX, chunkuje je i zapisuje do JSON z pustą listą `prompts` do ręcznego lub AI wypełnienia.

```bash
python cli/cli_golden_set.py <plik1.xlsx> <etykieta1> [plik2.xlsx <etykieta2> ...] [--rows-per-chunk N] [--output FILE] [--seed N]
```

Przykład z jednym urządzeniem:
```bash
python cli/cli_golden_set.py "data/xlsx/ORNO OR-WE-520.xlsx" "ORNO OR-WE-520" --output data/golden_set.json
```

Przykład z dwoma urządzeniami (polecana forma — korpus lepiej testuje rozróżnianie urządzeń):
```bash
python cli/cli_golden_set.py \
  "data/xlsx/ORNO OR-WE-520.xlsx" "ORNO OR-WE-520" \
  "data/xlsx/EASTRON SDM630.xlsx" "EASTRON SDM630" \
  --output data/golden_set.json
```

Przykładowy output (`golden_set.json`):
```json
[
  {
    "chunk_id": 0,
    "prompts": [],
    "text": "ORNO OR-WE-520 / Rejestry odczytu\n\nAdres (hex) | Rejestr nr | ..."
  }
]
```

Pole `prompts` należy wypełnić listą pytań testowych (ręcznie lub przez AI), a następnie uruchomić ewaluator.

Projekt zawiera dwa gotowe golden sety:
- `golden_set.json` — prompty realistyczne (5 na chunk): sformułowane tak jak użytkownik mógłby naprawdę zapytać, często bez nazwy urządzenia lub rejestru; mierzą jakość RAG w warunkach zbliżonych do produkcji
- `golden_set_unique.json` — prompty ukierunkowane na unikalność (2 na chunk): zawierają nazwę urządzenia, adres hex lub nazwę rejestru, które jednoznacznie wskazują na konkretny chunk; mierzą górną granicę możliwości embeddera — jeśli tu wynik jest słaby, problem leży w modelu lub chunkingu, nie w jakości pytań

---

## Testy

### Testy jednostkowe

Testy jednostkowe dla klasy `XlsxChunker` znajdują się w `test/test_xlsx_chunker.py`.

```bash
pip install pytest
pytest test/test_xlsx_chunker.py -v
```

### Ewaluacja retrievera

Skrypt `test/eval_retriever.py` mierzy jakość retrievera na golden secie — bez Qdrant, wyłącznie przez porównanie cosinusowe wektorów z opcjonalnym rerankingiem BM25. Wyniki: Recall@1, Recall@2, Recall@3 i MRR.

Tryby pracy:
- `--bm25-candidates 0` — sam embedder (porównanie cosinusowe)
- `--bm25-candidates 20` — embedder + reranking BM25 (domyślnie)

Działa na CPU — `nomic-embed-text` (~274 MB) nie wymaga GPU. Embedowanie będzie wolniejsze niż na karcie graficznej (kilka sekund na tekst), ale przy małym golden secie czas jest do przyjęcia.

**Wymagania (jednorazowo):**

1. Zainstaluj zależności Pythona:
```bash
pip install -r api/requirements.txt
```

2. Zainstaluj Ollama — pobierz installer ze strony [ollama.com](https://ollama.com/download) i uruchom.

3. Pobierz model embeddingu:
```bash
ollama pull nomic-embed-text
```

**Uruchomienie:**

4. Uruchom Ollama (jeśli nie działa jako serwis):
```bash
ollama serve
```

5. Uruchom ewaluator:
```bash
python test/eval_retriever.py data/golden_set.json
```

Na obu golden setach:
```bash
python test/eval_retriever.py data/golden_set.json
python test/eval_retriever.py data/golden_set_unique.json
```

Opcjonalnie — wyłączony BM25, wyższe k, tryb szczegółowy lub zdalny Pod:
```bash
python test/eval_retriever.py data/golden_set.json --bm25-candidates 0
python test/eval_retriever.py data/golden_set.json --k 6
python test/eval_retriever.py data/golden_set.json --verbose
python test/eval_retriever.py data/golden_set.json --ollama-url https://{POD_ID}-11434.proxy.runpod.net
```

Przykładowy output (6 chunków — ORNO OR-WE-520 + EASTRON SDM630):
```
Embedowanie 6 chunków...
  [1/6] chunk_id=0
  ...
  [6/6] chunk_id=5

Ewaluacja (embedder + BM25 (kandydaci: 20))...

══════════════════════════════════════════════════
  Chunków:             6
  Par (prompt, chunk): 30
  BM25 kandydaci:      20
  Recall@1:            0.700  (21/30)
  Recall@2:            0.867  (26/30)
  Recall@3:            0.933  (28/30)
  MRR:                 0.789
══════════════════════════════════════════════════
```

Przykładowy output z `--verbose`:
```
  ✓ [1] "napięcie L1 rejestr ORNO OR-WE-516"
       #1 chunk_id=0  (cosine: 0.8321) ← poprawny
       #2 chunk_id=2  (cosine: 0.7102)
       #3 chunk_id=1  (cosine: 0.6891)
  ✗ [2] "reset licznika OR-WE-520"
       #1 chunk_id=0  (cosine: 0.8120)
       #2 chunk_id=1  (cosine: 0.7240) ← poprawny
       #3 chunk_id=2  (cosine: 0.6510)
```

---

## TODO

### Jakość RAG
- [ ] **Query routing / device resolution** (priorytet):
  - Przed odpytaniem RAG wysyłaj osobny call LLM z listą znanych urządzeń (z pamięci/pliku, nie z Qdrant)
  - LLM zwraca nazwę urządzenia z listy lub "UNKNOWN" (niski max_tokens, krótki prompt)
  - Jeśli urządzenie rozpoznane → Qdrant payload filter na `source_label` + semantic search
  - Jeśli UNKNOWN → fallback: semantic search bez filtra
  - Lista urządzeń uzupełniana automatycznie przy każdym `/ingest`
  - Uwaga: pytania cross-device ("które liczniki mają taryfę T4?") wymagają osobnej obsługi — dla UNKNOWN nie filtruj
- [ ] Lepszy model embeddingów (np. `multilingual-e5-large`)
- [ ] HyDE — model generuje hipotetyczną odpowiedź, jej embedding idzie do Qdrant
- [ ] Osobne kolekcje per urządzenie

### Architektura i produkcyjność
- [ ] Asynchroniczny ingest + endpoint `/tasks/{id}` ze statusem — przy dużych plikach embedding sekwencyjny będzie wolny
- [ ] Autoryzacja — API key
- [ ] Obsługa duplikatów przy ponownym wgraniu tego samego pliku
- [ ] Auto-wybór kolekcji przez embedding
- [ ] Prosty frontend

### Testy
- [ ] Testy jednostkowe dla `RagRetriever`, `AskPipeline`, `XlsxIngester` — nowe klasy nie mają pokrycia testami
- [ ] Tryb interaktywny golden setu — dla każdego chunku losuje irrelevant i prosi operatora o wpisanie promptów; obsługa ponownego losowania gdy oba chunki są podobne

---

## Uwagi

- Zmiany w Template wymagają **Terminate** istniejącego Poda i stworzenia nowego.
- Volume (`/root/data`) przeżywa Terminate — modele i dane Qdrant nie muszą być pobierane ponownie.
- `rm -rf /tmp/init` w start command zabezpiecza przed błędem przy ponownym starcie na tym samym węźle.
- Container Disk kasuje się przy każdym Stop — `git clone` i `pip install` wykonują się przy każdym starcie (~60 sek.).
- Volume Disk ustawiony na **35 GB**: ~12 GB Bielik + ~274 MB nomic-embed-text + margines na dane Qdrant.
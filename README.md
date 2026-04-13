# bielik-runpod

Stack: Ollama + Bielik 11B v3.0 Q8_0 + Python REST API. Uruchamiany na RunPod przez on-start script.

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
| Volume Disk | `20 GB` |
| Volume Mount Path | `/root/.ollama` |

**Container Start Command:**
```
bash -c "apt-get update && apt-get install -y curl git zstd && curl -fsSL https://ollama.com/install.sh | sh && rm -rf /tmp/init && git clone --depth=1 https://github.com/protechnologia/bielik-runpod /tmp/init && bash /tmp/init/start.sh"
```

---

## Uruchamianie Poda

- **GPU:** RTX 4090
- **Cloud:** Secure Cloud (On Demand) — do prezentacji; Community Cloud — do testów
- **GPU Count:** 1

Pierwsze uruchomienie trwa ~10 minut (pobieranie modelu ~12 GB na Volume).  
Kolejne uruchomienia ~2 minuty — model już jest na Volume.

---

## Zmienne środowiskowe (w start.sh)
| Zmienna | Wartość |
|---|---|
| `OLLAMA_URL` | `http://localhost:11434` |
| `MODEL` | `SpeakLeash/bielik-11b-v3.0-instruct:Q8_0` |

---

## Test

URL Poda dostępny w panelu RunPod: **Connect → HTTP Service [Port 8000]**

```bash
curl -X POST https://{POD_ID}-8000.proxy.runpod.net/ask \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Czym jest spółdzielnia energetyczna? Odpowiedz w 2 zdaniach."}'
```

Przykładowa odpowiedź:
```json
{
  "answer": "Spółdzielnia energetyczna to forma organizacyjna...",
  "model": "SpeakLeash/bielik-1.5b-v3.0-instruct:Q8_0",
  "time_total_s": 13.1,
  "time_to_first_token_s": 1.3,
  "tokens_generated": 91,
  "tokens_per_second": 7.8
}
```

Swagger UI: `https://{POD_ID}-8000.proxy.runpod.net/docs`

---

## Uwagi

- Zmiany w Template wymagają **Terminate** istniejącego Poda i stworzenia nowego.
- Volume (`/root/.ollama`) przeżywa Terminate — model nie musi być pobierany ponownie.
- `rm -rf /tmp/init` w start command zabezpiecza przed błędem przy ponownym starcie na tym samym węźle.
- Container Disk kasuje się przy każdym Stop — `git clone` i `pip install` wykonują się przy każdym starcie (~60 sek.).

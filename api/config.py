"""
Stałe konfiguracyjne aplikacji — wartości niezmienne między środowiskami.

Zmienne zależne od środowiska (OLLAMA_URL, QDRANT_PATH) są wczytywane
przez os.getenv() w main.py i tu nie należą — ich wartość różni się
między RunPodem, środowiskiem lokalnym i testami.
"""

# ── Modele Ollama ──────────────────────────────────────────────────────────────

MODEL        = "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0"
ROUTER_MODEL = "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0"
EMBED_MODEL  = "nomic-embed-text"

# ── Baza wektorowa ─────────────────────────────────────────────────────────────

# Wymiarowość wektorów produkowanych przez nomic-embed-text.
# Musi być zgodna z modelem embeddingu — zmiana modelu wymaga zmiany tej wartości
# i przeindeksowania wszystkich kolekcji.
VECTOR_SIZE = 768

DEFAULT_COLLECTION = "documents"

# ── Prompty systemowe ──────────────────────────────────────────────────────────

# Używany przy zwykłych zapytaniach (rag=false).
# Nie ogranicza wiedzy modelu — odpowiada na podstawie własnych danych treningowych.
SYSTEM_PROMPT = (
    "Jesteś pomocnym asystentem języka polskiego. "
    "Zawsze odpowiadaj po polsku, chyba że użytkownik wyraźnie poprosi o inny język. "
    "Odpowiadaj zwięźle i konkretnie, zgodnie z poleceniem użytkownika. "
    "Jeśli pytanie dotyczy aktualnych danych jak dzisiejsza data lub pogoda, "
    "poinformuj że nie masz dostępu do takich informacji."
)

# Używany przez Query Router (ROUTER_MODEL) do identyfikacji urządzenia z pytania.
# Model otrzymuje listę dostępnych urządzeń i pytanie użytkownika.
# Musi zwrócić DOKŁADNIE jedną nazwę z listy lub słowo "brak" — żadnego innego tekstu.
ROUTER_SYSTEM_PROMPT = (
    "Masz listę urządzeń. "
    "Twoim zadaniem jest określić, którego urządzenia dotyczy pytanie użytkownika.\n\n"
    "Zasady:\n"
    "1. Szukaj w pytaniu nazwy urządzenia lub jej fragmentu.\n"
    "2. Jeśli znajdziesz dopasowanie — odpowiedz DOKŁADNIE nazwą z listy, bez żadnych zmian.\n"
    "3. Jeśli pytanie nie dotyczy żadnego konkretnego urządzenia z listy — odpowiedz: brak\n"
    "4. Nie dodawaj żadnych innych słów, wyjaśnień ani znaków interpunkcyjnych."
)

# Używany gdy RAG jest aktywny (rag=true).
# Ogranicza model wyłącznie do kontekstu z Qdrant — zapobiega halucynacjom.
# Każdy chunk zaczyna się od prefiksu "{source_label} / {arkusz}".
# Instrukcja nakazuje modelowi zawsze cytować to źródło w odpowiedzi.
RAG_SYSTEM_PROMPT = (
    "Jesteś pomocnym asystentem języka polskiego. "
    "Odpowiadaj wyłącznie na podstawie podanego kontekstu. "
    "Każdy fragment kontekstu zaczyna się od linii w formacie 'Nazwa urządzenia / Arkusz'. "
    "Zawsze rozpocznij odpowiedź od linii: 'Źródło: Nazwa urządzenia.' — "
    "gdzie 'Nazwa urządzenia' to tekst przed znakiem '/' z pierwszej linii fragmentu. "
    "Kolumna 'Tryb' określa dostępność rejestru: "
    "'Odczyt' — tylko do odczytu, nie sugeruj jako modyfikowalnego. "
    "'Zapis' — do odczytu i zapisu, tylko takie sugeruj do zmiany. "
    "Odpowiadaj zwięźle — bez zbędnych wstępów i wyjaśnień. "
    "Jeśli odpowiedź nie wynika z kontekstu, odpowiedz dokładnie: "
    "'Nie znalazłem tej informacji w dostępnej dokumentacji.' "
    "Zawsze odpowiadaj po polsku."
)

<?php
/**
 * Proxy WP REST API → Bielik FastAPI.
 *
 * Endpoint: POST /wp-json/bielik/v1/ask
 *
 * Frontend wysyła tylko { prompt } — parametry RAG i URL API są czytane
 * z opcji WordPress (nigdy nie przechodzą przez przeglądarkę).
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * Klasa Bielik_Rest_Proxy — serwer-side proxy między frontendem WordPress
 * a backendowym serwerem FastAPI z modelem Bielik.
 *
 * ## Cel i architektura
 *
 * Przeglądarka nie może bezpośrednio wywoływać endpointu FastAPI, ponieważ:
 *  1. Serwer FastAPI jest zazwyczaj oddzielnym hostem (RunPod, VPS, lokalny Docker),
 *     więc bezpośrednie żądanie AJAX naruszyłoby zasadę Same-Origin Policy (CORS).
 *  2. Token autoryzacyjny Bearer nie może być przechowywany w JS ani w HTML —
 *     byłby widoczny dla każdego użytkownika przeglądającego stronę.
 *
 * Rozwiązanie: frontend wysyła żądanie POST tylko do endpointu WordPress
 * `/wp-json/bielik/v1/ask` (ten sam origin, brak CORS), a PHP po stronie serwera
 * odczytuje wszystkie parametry konfiguracyjne z opcji WP (adres API, token,
 * parametry RAG) i przekazuje żądanie do FastAPI przy użyciu `wp_remote_post`.
 *
 * ## Bezpieczeństwo
 *
 * - Token Bearer nigdy nie opuszcza serwera PHP.
 * - Frontend przekazuje wyłącznie treść pytania (prompt).
 * - Parametry RAG i konfiguracja API są pobierane wyłącznie z wp_options przez
 *   klasę Bielik_Admin_Settings — nie mogą być nadpisane przez użytkownika.
 *
 * ## Przepływ danych
 *
 * Browser → POST /wp-json/bielik/v1/ask { prompt }
 *         → PHP: Bielik_Rest_Proxy::handle()
 *         → wp_remote_post → FastAPI POST /ask { prompt + parametry RAG }
 *         → PHP: zwraca WP_REST_Response z pełną odpowiedzią FastAPI
 *         → Browser: wyświetla odpowiedź, statystyki, chunki RAG
 */
class Bielik_Rest_Proxy {

	/**
	 * Rejestruje hook WordPress uruchamiający rejestrację endpointu REST API.
	 *
	 * Metoda `init()` jest wywoływana raz przy bootstrapowaniu pluginu
	 * (w pliku bielik-rag-widget.php). Nie rejestruje endpointu od razu —
	 * zamiast tego podłącza metodę `register_route()` pod akcję `rest_api_init`,
	 * która jest wywoływana przez WordPress dokładnie w momencie, gdy jądro REST API
	 * jest gotowe do przyjęcia nowych tras.
	 *
	 * Wzorzec opóźnionej rejestracji (hook zamiast bezpośredniego wywołania)
	 * jest zalecany przez WordPress Codex, ponieważ gwarantuje, że wszystkie
	 * wewnętrzne struktury REST API są już zainicjalizowane.
	 *
	 * @return void
	 */
	public function init(): void {
		add_action( 'rest_api_init', [ $this, 'register_route' ] );
	}

	/**
	 * Rejestruje trasę REST API: POST /wp-json/bielik/v1/ask
	 *
	 * Wywołana przez hook `rest_api_init`. Tworzy endpoint przyjmujący
	 * żądania POST z jednym wymaganym parametrem `prompt`.
	 *
	 * ### Szczegóły rejestracji
	 *
	 * - **Namespace**: `bielik/v1` — zgodnie z konwencją WP REST API (vendor/version).
	 *   Pozwala na rozszerzenie w przyszłości o kolejne wersje bez konfliktu tras.
	 * - **Route**: `/ask` — pełny URL to `/wp-json/bielik/v1/ask`.
	 * - **permission_callback**: `__return_true` oznacza brak kontroli dostępu —
	 *   endpoint jest publicznie dostępny, co jest intencjonalne: widget ma działać
	 *   dla niezalogowanych odwiedzających. Bezpieczeństwo zapewniają: nonce CSRF
	 *   (weryfikowany przez WP automatycznie gdy nagłówek X-WP-Nonce jest obecny)
	 *   oraz fakt, że prawdziwe dane uwierzytelniające (token FastAPI) nigdy
	 *   nie trafiają do przeglądarki.
	 * - **args / prompt**: pole wymagane (`required: true`), typ string,
	 *   sanityzowane przez `sanitize_text_field` — usuwa tagi HTML, nadmiarowe
	 *   białe znaki i znaki kontrolne. Wordpress zwróci błąd 400, jeśli `prompt`
	 *   jest puste lub nieobecne w żądaniu.
	 *
	 * @return void
	 */
	public function register_route(): void {
		register_rest_route( 'bielik/v1', '/ask', [
			'methods'             => 'POST',
			'callback'            => [ $this, 'handle' ],
			'permission_callback' => '__return_true',
			'args'                => [
				'prompt' => [
					'required'          => true,
					'type'              => 'string',
					'sanitize_callback' => 'sanitize_text_field',
				],
			],
		] );
	}

	/**
	 * Obsługuje przychodzące żądanie POST, buduje payload i przekazuje je do FastAPI.
	 *
	 * Jest to główna logika biznesowa klasy — callback wywoływany przez WordPress REST
	 * API przy każdym żądaniu POST na /wp-json/bielik/v1/ask.
	 *
	 * ### Kroki wykonania
	 *
	 * 1. **Odczyt URL API** — pobiera adres serwera FastAPI z wp_options przez
	 *    `Bielik_Admin_Settings::get('api_url')`. Jeśli adres nie jest skonfigurowany,
	 *    metoda natychmiast zwraca błąd 503 Service Unavailable z czytelną wiadomością.
	 *
	 * 2. **Budowanie payloadu** — konstruuje tablicę parametrów wysyłanych do FastAPI:
	 *    - `prompt`              — treść pytania z żądania (już sanityzowana przez WP)
	 *    - `rag`                 — (bool) czy włączyć retrieval-augmented generation
	 *    - `collection`          — nazwa kolekcji Qdrant; domyślnie 'documents'
	 *    - `rag_top_k`           — (int) ile fragmentów RAG dołączyć do kontekstu
	 *    - `rag_score_threshold` — (float) minimalny próg podobieństwa wektora
	 *    - `bm25_candidates`     — (int) kandydaci dla re-rankingu BM25
	 *    - `query_router`        — (bool) czy używać klasyfikatora pytań
	 *    - `max_tokens`          — (int) limit tokenów generowanej odpowiedzi
	 *    - `temperature`         — stała wartość 0.1 (niska, dla odpowiedzi faktycznych)
	 *    Wszystkie parametry poza `prompt` i `temperature` są odczytywane z wp_options —
	 *    użytkownik frontendu nie może ich zmienić ani podejrzeć.
	 *
	 * 3. **Nagłówki** — domyślnie `Content-Type: application/json`. Jeśli w ustawieniach
	 *    podano token API, dodawany jest nagłówek `Authorization: Bearer <token>`.
	 *
	 * 4. **Wywołanie HTTP** — `wp_remote_post()` jest opakowaniem WordPress wokół cURL/streams,
	 *    które obsługuje redirecty, SSL, proxy. Timeout 120 sekund — modele LLM mogą
	 *    potrzebować czasu na generowanie długich odpowiedzi przy wysokich wartościach max_tokens.
	 *
	 * 5. **Obsługa błędów**:
	 *    - Błąd transportu (brak połączenia, timeout) → WP_Error z kodem `bielik_upstream_error`
	 *      i statusem HTTP 502 Bad Gateway.
	 *    - Odpowiedź nie jest poprawnym JSON → WP_Error `bielik_invalid_json`, status 502.
	 *    - Błędy zwrócone przez sam FastAPI (np. status 422, 500) są transparentnie
	 *      przepuszczane — status HTTP i ciało odpowiedzi z FastAPI trafiają do przeglądarki
	 *      bez modyfikacji.
	 *
	 * 6. **Odpowiedź sukcesu** — `WP_REST_Response` z dekodowaną tablicą JSON i oryginalnym
	 *    kodem HTTP z FastAPI (zazwyczaj 200). WordPress serializuje ją z powrotem do JSON.
	 *
	 * @param WP_REST_Request $request Obiekt żądania REST API wstrzyknięty przez WordPress.
	 *                                  Udostępnia metodę get_param() do pobierania
	 *                                  zwalidowanych i sanityzowanych parametrów.
	 *
	 * @return WP_REST_Response|WP_Error
	 *         WP_REST_Response — przy powodzeniu; ciało to tablica zdekodowana z JSON-a FastAPI
	 *         (klucze: answer, model, time_total_s, time_to_first_token_s, tokens_generated,
	 *         tokens_per_second, rag_chunks_used, rag_chunks[]).
	 *         WP_Error — przy błędzie konfiguracji (503), błędzie sieci (502) lub złym JSON (502).
	 */
	public function handle( WP_REST_Request $request ) {
		$api_url   = rtrim( Bielik_Admin_Settings::get( 'api_url' ), '/' );
		$debug     = (bool) Bielik_Admin_Settings::get( 'debug_mode' );
		$error_msg = __( 'Wystąpił błąd podczas przetwarzania zapytania. Spróbuj ponownie.', 'bielik-rag-widget' );

		if ( empty( $api_url ) ) {
			return new WP_Error(
				'bielik_no_api_url',
				$debug
					? __( 'Brak skonfigurowanego adresu API. Przejdź do Ustawienia → Bielik RAG.', 'bielik-rag-widget' )
					: $error_msg,
				[ 'status' => 503 ]
			);
		}

		$payload = [
			'prompt'              => $request->get_param( 'prompt' ),
			'rag'                 => (bool) Bielik_Admin_Settings::get( 'rag' ),
			'collection'          => Bielik_Admin_Settings::get( 'collection' ) ?: 'documents',
			'rag_top_k'           => (int)   Bielik_Admin_Settings::get( 'rag_top_k' ),
			'rag_score_threshold' => (float) Bielik_Admin_Settings::get( 'rag_score_threshold' ),
			'bm25_candidates'     => (int)   Bielik_Admin_Settings::get( 'bm25_candidates' ),
			'query_router'        => (bool)  Bielik_Admin_Settings::get( 'query_router' ),
			'max_tokens'          => (int)   Bielik_Admin_Settings::get( 'max_tokens' ),
			'temperature'         => 0.1,
		];

		$headers = [ 'Content-Type' => 'application/json' ];

		$token = Bielik_Admin_Settings::get( 'api_token' );
		if ( ! empty( $token ) ) {
			$headers['Authorization'] = 'Bearer ' . $token;
		}

		$response = wp_remote_post( $api_url . '/ask', [
			'headers'     => $headers,
			'body'        => wp_json_encode( $payload ),
			'timeout'     => 120,
			'data_format' => 'body',
		] );

		if ( is_wp_error( $response ) ) {
			return new WP_Error(
				'bielik_upstream_error',
				$debug ? $response->get_error_message() : $error_msg,
				[ 'status' => 502 ]
			);
		}

		$status = (int) wp_remote_retrieve_response_code( $response );
		$body   = wp_remote_retrieve_body( $response );
		$data   = json_decode( $body, true );

		if ( json_last_error() !== JSON_ERROR_NONE ) {
			return new WP_Error(
				'bielik_invalid_json',
				$debug
					? __( 'Serwer Bielik zwrócił nieprawidłową odpowiedź JSON.', 'bielik-rag-widget' )
					: $error_msg,
				[ 'status' => 502 ]
			);
		}

		if ( $status < 200 || $status >= 300 ) {
			return new WP_Error(
				'bielik_upstream_error',
				$debug
					? ( isset( $data['detail'] ) ? $data['detail'] : ( 'HTTP ' . $status ) )
					: $error_msg,
				[ 'status' => 502 ]
			);
		}

		return new WP_REST_Response( $data, $status );
	}
}

<?php
/**
 * Strona ustawień pluginu w panelu WordPress: Ustawienia → Bielik RAG.
 *
 * Klasa Bielik_Admin_Settings odpowiada za:
 *   1. Rejestrację strony opcji w menu WordPress (add_options_page).
 *   2. Rejestrację wszystkich opcji przez WordPress Settings API
 *      (register_setting + add_settings_section + add_settings_field).
 *   3. Renderowanie formularza HTML na stronie ustawień.
 *   4. Sanityzację wartości przed zapisem do bazy danych.
 *   5. Udostępnienie statycznej metody get() do odczytu opcji z innych klas.
 *
 * Wszystkie opcje przechowywane są w tabeli wp_options pod kluczami
 * z prefiksem 'bielik_', np. bielik_api_url, bielik_rag_top_k.
 *
 * Dlaczego ustawienia są tutaj, a nie w Controls Elementora?
 *   Parametry API (URL, token, parametry RAG) muszą być dostępne tylko
 *   serwerowi PHP — klasa Bielik_Rest_Proxy czyta je i przekazuje do FastAPI
 *   po stronie serwera. Dzięki temu przeglądarka nigdy nie widzi adresu API
 *   ani tokena Bearer, a użytkownicy edytujący stronę w Elementorze nie mogą
 *   przypadkowo zmienić konfiguracji backendowej.
 *
 * Inicjalizacja:
 *   Klasa jest tworzona w bielik-rag-widget.php przez ( new Bielik_Admin_Settings() )->init().
 *   init() rejestruje hooki WordPress — musi być wywołana przed 'admin_menu'
 *   i 'admin_init', czyli na wczesnym etapie ładowania WordPress.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class Bielik_Admin_Settings {

	/** Nazwa grupy opcji używana przez settings_fields() i register_setting(). */
	const OPTION_GROUP = 'bielik_rag_options';

	/** Slug strony ustawień używany w add_options_page() i add_settings_field(). */
	const PAGE_SLUG    = 'bielik-rag';

	/**
	 * Zwraca tablicę domyślnych wartości wszystkich opcji pluginu.
	 *
	 * Metoda jest statyczna, bo używana jest zarówno w instancji klasy
	 * (render_field(), register_settings()) jak i w statycznej metodzie get().
	 *
	 * Opis wartości domyślnych:
	 *   api_url             — pusty string; plugin nie działa bez adresu API,
	 *                         ale brak wartości daje użytkownikowi czytelny błąd
	 *                         503 zamiast cichego niepowodzenia.
	 *   api_token           — pusty string; token jest opcjonalny (do przyszłej
	 *                         autoryzacji Bearer na serwerze FastAPI).
	 *   collection          — 'documents'; domyślna nazwa kolekcji Qdrant zgodna
	 *                         z konfiguracją w config.py projektu.
	 *   rag                 — '1' (włączone); wartość '1' lub '' zamiast bool,
	 *                         bo WordPress przechowuje opcje jako stringi,
	 *                         a checkboxy przy niezaznaczeniu nie wysyłają wartości.
	 *   query_router        — '' (wyłączony); kosztuje ~1-2s więcej na zapytanie,
	 *                         więc domyślnie wyłączony.
	 *   rag_top_k           — 3; dobry kompromis między kontekstem a szybkością.
	 *   rag_score_threshold — 0.3; odrzuca słabo pasujące chunki.
	 *   bm25_candidates     — 20; pobranie 20 kandydatów przed rerankingiem BM25+RRF
	 *                         zapewnia dobre wyniki przy małym narzucie czasowym.
	 *   max_tokens          — 512; wystarczające dla typowych odpowiedzi technicznych.
	 *   trim_to_sentence    — '1' (włączone); jeśli odpowiedź jest ucięta przez limit
	 *                         tokenów (done_reason == "length"), przycina ją do ostatniego
	 *                         pełnego zdania (.!?…).
	 *
	 * @return array<string, mixed> Tablica [klucz => wartość_domyślna].
	 */
	public static function defaults(): array {
		return [
			'api_url'             => '',
			'api_token'           => '',
			'collection'          => 'documents',
			'rag'                 => '1',
			'query_router'        => '',
			'rag_top_k'           => 3,
			'rag_score_threshold' => 0.3,
			'bm25_candidates'     => 20,
			'max_tokens'          => 512,
			'trim_to_sentence'    => '1',
			'debug_mode'          => '',
		];
	}

	/**
	 * Zwraca wartość pojedynczej opcji pluginu z fallbackiem do wartości domyślnej.
	 *
	 * Metoda statyczna — pozwala innym klasom (Bielik_Rest_Proxy) odczytywać
	 * opcje bez tworzenia instancji Bielik_Admin_Settings.
	 *
	 * Klucz w bazie danych to 'bielik_' . $key, np. dla $key = 'api_url'
	 * zostanie odczytana opcja 'bielik_api_url' z tabeli wp_options.
	 * Prefiks 'bielik_' chroni przed kolizją z opcjami innych pluginów.
	 *
	 * Jeśli opcja nie istnieje w bazie (np. świeża instalacja), zwracana jest
	 * wartość z defaults(). Jeśli klucz nie istnieje w defaults(), fallback to ''.
	 *
	 * @param  string $key  Klucz opcji bez prefiksu 'bielik_', np. 'api_url'.
	 * @return mixed        Wartość opcji (string, int lub float zależnie od klucza).
	 */
	public static function get( string $key ) {
		$defaults = self::defaults();
		$value    = get_option( 'bielik_' . $key, $defaults[ $key ] ?? '' );
		return $value;
	}

	/**
	 * Rejestruje hooki WordPress dla klasy.
	 *
	 * Wywoływana raz przy ładowaniu pluginu. Wszystkie hooki są admin-only
	 * (admin_menu, admin_init, plugin_action_links_*), więc nie obciążają
	 * frontendu.
	 *
	 * Hooki:
	 *   admin_menu    — dodaje pozycję "Bielik RAG" w Ustawienia.
	 *   admin_init    — rejestruje sekcje i pola przez Settings API.
	 *   plugin_action_links_{basename} — dodaje link "Ustawienia" na liście
	 *     pluginów (obok Dezaktywuj), żeby łatwiej było się dostać do konfiguracji.
	 *
	 * @return void
	 */
	public function init(): void {
		add_action( 'admin_menu',    [ $this, 'add_page' ] );
		add_action( 'admin_init',    [ $this, 'register_settings' ] );
		add_filter( 'plugin_action_links_' . plugin_basename( BIELIK_PLUGIN_FILE ),
		            [ $this, 'action_links' ] );
	}

	/**
	 * Dodaje podstronę ustawień do menu Ustawienia w panelu WordPress.
	 *
	 * Parametry add_options_page():
	 *   - Tytuł zakładki przeglądarki / strony: "Bielik RAG — ustawienia".
	 *   - Tytuł w menu bocznym: "Bielik RAG".
	 *   - Wymagana zdolność (capability): 'manage_options' — tylko administratorzy.
	 *   - Slug strony: PAGE_SLUG = 'bielik-rag' (URL: options-general.php?page=bielik-rag).
	 *   - Callback renderujący HTML: $this->render_page().
	 *
	 * @return void
	 */
	public function add_page(): void {
		add_options_page(
			__( 'Bielik RAG — ustawienia', 'bielik-rag-widget' ),
			__( 'Bielik RAG', 'bielik-rag-widget' ),
			'manage_options',
			self::PAGE_SLUG,
			[ $this, 'render_page' ]
		);
	}

	/**
	 * Rejestruje wszystkie sekcje i pola ustawień przez WordPress Settings API.
	 *
	 * Konfiguracja pól jest opisana jako tablica asocjacyjna $fields.
	 * Elementy z kluczem '_section' => true tworzą nową sekcję (add_settings_section),
	 * pozostałe elementy tworzą pola (register_setting + add_settings_field).
	 *
	 * Dla każdego pola:
	 *   - register_setting() rejestruje opcję w grupie OPTION_GROUP i przypisuje
	 *     sanitizer o nazwie sanitize_{klucz} (metody tej klasy).
	 *   - add_settings_field() przypisuje pole do ostatnio otwartej sekcji
	 *     i renderuje je przez $this->render_field() z parametrami z $fields.
	 *
	 * Metoda sanitize_{klucz} musi istnieć dla każdego pola (nie-sekcji).
	 * WordPress wywołuje ją automatycznie przy zapisie formularza — zapewnia
	 * to walidację i oczyszczenie danych przed wpisem do bazy.
	 *
	 * Sekcje:
	 *   'section_connection' — URL API, token Bearer, nazwa kolekcji Qdrant.
	 *   'section_rag'        — przełączniki i parametry pobierania chunków RAG.
	 *   'section_generation' — limit tokenów, przycinanie odpowiedzi do pełnego zdania.
	 *
	 * @return void
	 */
	public function register_settings(): void {
		$fields = [
			// Section: Połączenie
			'section_connection' => [
				'_section' => true,
				'title'    => __( 'Połączenie z API', 'bielik-rag-widget' ),
			],
			'api_url' => [
				'label'       => __( 'URL serwera Bielik', 'bielik-rag-widget' ),
				'type'        => 'url',
				'placeholder' => 'https://{POD_ID}-8000.proxy.runpod.net',
				'description' => __( 'Adres bazowy REST API bez końcowego ukośnika, np. <code>http://localhost:8000</code> lub adres RunPod.', 'bielik-rag-widget' ),
			],
			'api_token' => [
				'label'       => __( 'Token API (Bearer)', 'bielik-rag-widget' ),
				'type'        => 'password',
				'placeholder' => '',
				'description' => __( 'Opcjonalny token autoryzacyjny — zostaw puste jeśli API nie wymaga uwierzytelnienia.', 'bielik-rag-widget' ),
			],
			'collection' => [
				'label'       => __( 'Kolekcja Qdrant', 'bielik-rag-widget' ),
				'type'        => 'text',
				'placeholder' => 'documents',
				'description' => __( 'Nazwa kolekcji wektorowej w Qdrant. Domyślnie: <code>documents</code>.', 'bielik-rag-widget' ),
			],

			// Section: RAG
			'section_rag' => [
				'_section' => true,
				'title'    => __( 'Ustawienia RAG', 'bielik-rag-widget' ),
			],
			'rag' => [
				'label'       => __( 'Włącz RAG', 'bielik-rag-widget' ),
				'type'        => 'checkbox',
				'description' => __( 'Odpowiedzi oparte na dokumentach wgranych do Qdrant.', 'bielik-rag-widget' ),
			],
			'query_router' => [
				'label'       => __( 'Query Router (Bielik 11B)', 'bielik-rag-widget' ),
				'type'        => 'checkbox',
				'description' => __( 'Model identyfikuje urządzenie z pytania i zawęża Qdrant do pasującego <code>source_label</code>. Dodaje ~1–2 s do czasu odpowiedzi.', 'bielik-rag-widget' ),
			],
			'rag_top_k' => [
				'label'       => __( 'Liczba chunków RAG (top_k)', 'bielik-rag-widget' ),
				'type'        => 'number',
				'min'         => 1,
				'max'         => 20,
				'step'        => 1,
				'description' => __( 'Ile najlepiej dopasowanych fragmentów trafi do kontekstu LLM. Zalecane: 3–5.', 'bielik-rag-widget' ),
			],
			'rag_score_threshold' => [
				'label'       => __( 'Próg podobieństwa cosinusowego', 'bielik-rag-widget' ),
				'type'        => 'number',
				'min'         => 0,
				'max'         => 1,
				'step'        => 0.05,
				'description' => __( 'Chunki z wynikiem poniżej progu są odrzucane. Wartość 0.0 wyłącza filtr.', 'bielik-rag-widget' ),
			],
			'bm25_candidates' => [
				'label'       => __( 'Kandydaci BM25', 'bielik-rag-widget' ),
				'type'        => 'number',
				'min'         => 0,
				'max'         => 500,
				'step'        => 1,
				'description' => __( 'Liczba kandydatów pobieranych z Qdrant przed rerankingiem BM25+RRF. Wartość 0 wyłącza BM25 — zwracane jest bezpośrednio top_k z Qdrant.', 'bielik-rag-widget' ),
			],

			// Section: Generowanie
			'section_generation' => [
				'_section' => true,
				'title'    => __( 'Generowanie odpowiedzi', 'bielik-rag-widget' ),
			],
			'max_tokens' => [
				'label'       => __( 'Maks. tokenów odpowiedzi', 'bielik-rag-widget' ),
				'type'        => 'number',
				'min'         => 64,
				'max'         => 4096,
				'step'        => 64,
				'description' => __( 'Limit długości odpowiedzi modelu.', 'bielik-rag-widget' ),
			],
			'trim_to_sentence' => [
				'label'       => __( 'Przytnij do pełnego zdania', 'bielik-rag-widget' ),
				'type'        => 'checkbox',
				'description' => __( 'Jeśli odpowiedź jest ucięta przez limit tokenów, przycina ją do ostatniego pełnego zdania (.!?…).', 'bielik-rag-widget' ),
			],

			// Section: Diagnostyka
			'section_diagnostics' => [
				'_section' => true,
				'title'    => __( 'Diagnostyka', 'bielik-rag-widget' ),
			],
			'debug_mode' => [
				'label'       => __( 'Tryb debugowania', 'bielik-rag-widget' ),
				'type'        => 'checkbox',
				'description' => __( 'Wyświetla szczegółowe komunikaty błędów w widgecie (np. odpowiedź serwera, kody HTTP). Wyłącz na produkcji — użytkownicy będą widzieć tylko ogólny komunikat.', 'bielik-rag-widget' ),
			],
		];

		$current_section = '';

		foreach ( $fields as $key => $field ) {
			if ( ! empty( $field['_section'] ) ) {
				$current_section = $key;
				add_settings_section(
					$key,
					$field['title'],
					'__return_false',
					self::PAGE_SLUG
				);
				continue;
			}

			$option_name = 'bielik_' . $key;
			register_setting( self::OPTION_GROUP, $option_name, [
				'sanitize_callback' => [ $this, 'sanitize_' . $key ],
			] );

			add_settings_field(
				$option_name,
				$field['label'],
				[ $this, 'render_field' ],
				self::PAGE_SLUG,
				$current_section,
				array_merge( $field, [ 'option_name' => $option_name, 'key' => $key ] )
			);
		}
	}

	/**
	 * Renderuje pojedyncze pole formularza HTML na stronie ustawień.
	 *
	 * Wywoływana przez WordPress Settings API jako callback add_settings_field().
	 * Tablica $args zawiera wszystkie atrybuty pola zdefiniowane w register_settings()
	 * plus 'option_name' (np. 'bielik_api_url') i 'key' (np. 'api_url').
	 *
	 * Obsługiwane typy pól:
	 *   - 'checkbox': renderuje <input type="checkbox"> z opisem jako labelką.
	 *     Wartość zaznaczonego checkboxa to '1', odznaczonego — brak wysłanego pola
	 *     (PHP nie otrzymuje nic, sanitizer zwraca '').
	 *   - pozostałe ('text', 'url', 'password', 'number'): renderuje <input>
	 *     z obsługą atrybutów min/max/step dla type="number".
	 *
	 * Opis pola ($description) renderowany jest przez wp_kses() ograniczone
	 * do tagu <code> — blokuje XSS przy jednoczesnym pozwoleniu na formatowanie
	 * nazw kluczy i wartości konfiguracyjnych znacznikiem <code>.
	 *
	 * Wartości wyjściowe są zawsze escapowane przez esc_attr(), co chroni przed
	 * sytuacją, gdy w bazie danych znalazłby się niesanityzowany ciąg.
	 *
	 * @param  array $args {
	 *   @type string $option_name  Pełna nazwa opcji WP, np. 'bielik_api_url'.
	 *   @type string $key          Krótki klucz, np. 'api_url'.
	 *   @type string $type         Typ inputa: 'text'|'url'|'password'|'number'|'checkbox'.
	 *   @type string $placeholder  Placeholder dla inputów tekstowych.
	 *   @type string $description  HTML opisu pola (dozwolony tylko <code>).
	 *   @type int|float $min       Minimalna wartość (type=number).
	 *   @type int|float $max       Maksymalna wartość (type=number).
	 *   @type int|float $step      Krok wartości (type=number).
	 * }
	 * @return void
	 */
	public function render_field( array $args ): void {
		$option_name = $args['option_name'];
		$defaults    = self::defaults();
		$value       = get_option( $option_name, $defaults[ $args['key'] ] ?? '' );
		$type        = $args['type'];
		$placeholder = $args['placeholder'] ?? '';
		$description = $args['description'] ?? '';

		if ( $type === 'checkbox' ) {
			printf(
				'<label><input type="checkbox" name="%s" value="1" %s> %s</label>',
				esc_attr( $option_name ),
				checked( '1', $value, false ),
				wp_kses( $description, [ 'code' => [] ] )
			);
			return;
		}

		$extra = '';
		if ( isset( $args['min'] ) )  $extra .= ' min="'  . esc_attr( $args['min'] )  . '"';
		if ( isset( $args['max'] ) )  $extra .= ' max="'  . esc_attr( $args['max'] )  . '"';
		if ( isset( $args['step'] ) ) $extra .= ' step="' . esc_attr( $args['step'] ) . '"';

		printf(
			'<input type="%s" name="%s" value="%s" placeholder="%s" class="regular-text" %s>',
			esc_attr( $type ),
			esc_attr( $option_name ),
			esc_attr( $value ),
			esc_attr( $placeholder ),
			$extra
		);

		if ( $description ) {
			printf( '<p class="description">%s</p>', wp_kses( $description, [ 'code' => [] ] ) );
		}
	}

	// ── Sanitizery ────────────────────────────────────────────────────────────

	/**
	 * Sanityzuje URL serwera Bielik API.
	 *
	 * esc_url_raw() normalizuje URL, usuwa niedozwolone protokoły i znaki.
	 * trim() usuwa przypadkowe spacje z początku i końca (częsty błąd przy wklejaniu).
	 * Nie waliduje czy URL jest osiągalny — to weryfikuje proxy przy pierwszym żądaniu.
	 *
	 * @param  mixed $v Wartość z formularza.
	 * @return string   Bezpieczny URL lub pusty string.
	 */
	public function sanitize_api_url( $v )             { return esc_url_raw( trim( $v ) ); }

	/**
	 * Sanityzuje token API Bearer.
	 *
	 * sanitize_text_field() usuwa HTML, zbędne spacje i niewidoczne znaki.
	 * Token jest przechowywany w wp_options i nigdy nie trafia do przeglądarki —
	 * klasa Bielik_Rest_Proxy używa go wyłącznie do nagłówka Authorization.
	 *
	 * @param  mixed $v Wartość z formularza.
	 * @return string   Oczyszczony token lub pusty string.
	 */
	public function sanitize_api_token( $v )           { return sanitize_text_field( $v ); }

	/**
	 * Sanityzuje nazwę kolekcji Qdrant.
	 *
	 * sanitize_key() zamienia na małe litery, usuwa spacje i znaki specjalne —
	 * Qdrant akceptuje tylko alfanumeryczne nazwy z myślnikami i podkreśleniami.
	 * Fallback do 'documents' gdy wynik jest pusty (np. użytkownik wpisał same spacje).
	 *
	 * @param  mixed $v Wartość z formularza.
	 * @return string   Bezpieczna nazwa kolekcji, co najmniej 'documents'.
	 */
	public function sanitize_collection( $v )          { return sanitize_key( $v ) ?: 'documents'; }

	/**
	 * Sanityzuje wartość checkboxa "Włącz RAG".
	 *
	 * Checkbox wysyła '1' gdy zaznaczony, nic gdy odznaczony.
	 * Przechowujemy '1' lub '' (pusty string), żeby checked() w render_field
	 * działało poprawnie przy porównaniu '1' == get_option().
	 *
	 * @param  mixed $v Wartość z formularza ('1' lub brak).
	 * @return string   '1' gdy włączony, '' gdy wyłączony.
	 */
	public function sanitize_rag( $v )                 { return $v ? '1' : ''; }

	/**
	 * Sanityzuje wartość checkboxa "Query Router".
	 *
	 * @param  mixed $v Wartość z formularza.
	 * @return string   '1' gdy włączony, '' gdy wyłączony.
	 */
	public function sanitize_query_router( $v )        { return $v ? '1' : ''; }

	/**
	 * Sanityzuje liczbę chunków RAG (top_k).
	 *
	 * Wymusza zakres 1–20. Wartość 0 byłaby bezsensu (brak chunków),
	 * wartości powyżej 20 niepotrzebnie zwiększają kontekst LLM.
	 *
	 * @param  mixed $v Wartość z formularza.
	 * @return int      Liczba z zakresu [1, 20].
	 */
	public function sanitize_rag_top_k( $v )           { return max( 1, min( 20, (int) $v ) ); }

	/**
	 * Sanityzuje próg podobieństwa cosinusowego.
	 *
	 * Podobieństwo cosinusowe mieści się w zakresie [0.0, 1.0].
	 * Wartość 0.0 wyłącza filtr — wszystkie chunki trafiają do kontekstu.
	 *
	 * @param  mixed $v Wartość z formularza.
	 * @return float    Wartość z zakresu [0.0, 1.0].
	 */
	public function sanitize_rag_score_threshold( $v ) { return max( 0.0, min( 1.0, (float) $v ) ); }

	/**
	 * Sanityzuje liczbę kandydatów BM25.
	 *
	 * 0 wyłącza reranking BM25 — Qdrant zwraca bezpośrednio top_k wyników.
	 * Górna granica 500 jest zapasem bezpieczeństwa przed absurdalnymi wartościami.
	 *
	 * @param  mixed $v Wartość z formularza.
	 * @return int      Liczba z zakresu [0, 500].
	 */
	public function sanitize_bm25_candidates( $v )     { return max( 0, min( 500, (int) $v ) ); }

	/**
	 * Sanityzuje maksymalną liczbę tokenów odpowiedzi.
	 *
	 * Minimum 64 gwarantuje sensowną odpowiedź, maksimum 4096 nie przekracza
	 * okna kontekstu modelu Bielik 11B. Krok 64 w formularzu wynika z
	 * wyrównania tokenizatora (wielokrotności 64 są wydajniejsze).
	 *
	 * @param  mixed $v Wartość z formularza.
	 * @return int      Liczba z zakresu [64, 4096].
	 */
	public function sanitize_max_tokens( $v )          { return max( 64, min( 4096, (int) $v ) ); }

	/**
	 * Sanityzuje wartość checkboxa "Przytnij do pełnego zdania".
	 *
	 * @param  mixed $v Wartość z formularza.
	 * @return string   '1' gdy włączony, '' gdy wyłączony.
	 */
	public function sanitize_trim_to_sentence( $v )    { return $v ? '1' : ''; }

	/**
	 * Sanityzuje wartość checkboxa "Tryb debugowania".
	 *
	 * @param  mixed $v Wartość z formularza.
	 * @return string   '1' gdy włączony, '' gdy wyłączony.
	 */
	public function sanitize_debug_mode( $v )          { return $v ? '1' : ''; }

	// ── Render page ───────────────────────────────────────────────────────────

	/**
	 * Renderuje stronę HTML ustawień pluginu.
	 *
	 * Sprawdza uprawnienia użytkownika — jeśli nie ma 'manage_options',
	 * kończy działanie bez renderowania (dodatkowa warstwa bezpieczeństwa
	 * ponad mechanizmem capability w add_options_page).
	 *
	 * Formularz jest wysyłany metodą POST na options.php (standardowa strona
	 * WordPress do obsługi formularzy Settings API). WordPress automatycznie
	 * weryfikuje nonce i obsługuje zapis opcji, wywołując przed tym sanitizery.
	 *
	 * settings_fields() generuje ukryte pola: nonce, action oraz option_page
	 * powiązane z OPTION_GROUP — bez nich WordPress odrzuci zapis.
	 *
	 * do_settings_sections() renderuje kolejno wszystkie sekcje i pola
	 * zarejestrowane dla PAGE_SLUG przez add_settings_section/add_settings_field.
	 *
	 * @return void
	 */
	public function render_page(): void {
		if ( ! current_user_can( 'manage_options' ) ) {
			return;
		}
		?>
		<div class="wrap">
			<h1><?php esc_html_e( 'Bielik RAG — ustawienia', 'bielik-rag-widget' ); ?></h1>
			<p><?php esc_html_e( 'Konfiguracja połączenia z REST API modelu Bielik 11B i parametrów RAG. Ustawienia dotyczą wszystkich instancji widgetu Elementor na stronie.', 'bielik-rag-widget' ); ?></p>
			<form method="post" action="options.php">
				<?php
				settings_fields( self::OPTION_GROUP );
				do_settings_sections( self::PAGE_SLUG );
				submit_button( __( 'Zapisz ustawienia', 'bielik-rag-widget' ) );
				?>
			</form>
		</div>
		<?php
	}

	/**
	 * Dodaje link "Ustawienia" do wiersza pluginu na liście pluginów WordPress.
	 *
	 * Filtr 'plugin_action_links_{basename}' przekazuje tablicę istniejących
	 * linków (np. "Dezaktywuj"). Ta metoda dokłada link "Ustawienia" na początku
	 * tablicy (array_unshift), żeby pojawił się przed "Dezaktywuj".
	 *
	 * Skraca drogę do konfiguracji — administrator nie musi szukać ustawień
	 * w menu bocznym po aktywacji pluginu.
	 *
	 * @param  array $links Istniejące linki akcji pluginu.
	 * @return array        Tablica z dodanym linkiem "Ustawienia" na początku.
	 */
	public function action_links( array $links ): array {
		$url = admin_url( 'options-general.php?page=' . self::PAGE_SLUG );
		array_unshift( $links, sprintf(
			'<a href="%s">%s</a>',
			esc_url( $url ),
			esc_html__( 'Ustawienia', 'bielik-rag-widget' )
		) );
		return $links;
	}
}

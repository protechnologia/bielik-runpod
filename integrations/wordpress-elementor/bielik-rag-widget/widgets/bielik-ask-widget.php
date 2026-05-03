<?php
/**
 * Elementor Widget — Bielik RAG Ask.
 *
 * Controls dotyczą wyłącznie prezentacji (treści i wygląd).
 * Parametry API i RAG są konfigurowane w Ustawienia → Bielik RAG.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * Klasa Bielik_Ask_Widget — widget Elementor do zadawania pytań modelowi Bielik.
 *
 * ## Przeznaczenie
 *
 * Widget renderuje interaktywny formularz Q&A: pole tekstowe na pytanie,
 * przycisk "Zapytaj", pole z odpowiedzią modelu, pasek statystyk wydajności
 * (czas, TTFT, tokeny/s, model) oraz listę przycisków do fragmentów RAG
 * z możliwością podglądu w modalnym oknie.
 *
 * ## Filozofia podziału konfiguracji
 *
 * Widget celowo nie zawiera żadnych parametrów technicznych (URL API, token,
 * opcje RAG). Taki podział wynika z dwóch powodów:
 *
 *  1. **Bezpieczeństwo** — parametry API i token Bearer nie powinny być dostępne
 *     w edytorze Elementor, który renderuje dane po stronie klienta.
 *     Wszystkie parametry techniczne są przechowywane wyłącznie w wp_options
 *     i obsługiwane przez klasę Bielik_Admin_Settings.
 *
 *  2. **Separacja odpowiedzialności** — edytor Elementor odpowiada za wygląd
 *     i treści widoczne dla użytkownika (tytuł, kolory, typografia).
 *     Panel admina WordPress odpowiada za konfigurację integracji z API.
 *
 * ## Zależności
 *
 * - CSS: `bielik-ask` (assets/css/bielik-ask.css) — rejestrowany przez główny plik
 *   pluginu, ładowany automatycznie gdy widget jest na stronie.
 * - JS:  `bielik-ask` (assets/js/bielik-ask.js) — inicjalizuje interaktywność
 *   widgetu; odczytuje `BielikConfig.restUrl` i `BielikConfig.nonce` wstrzyknięte
 *   przez `wp_localize_script` w głównym pliku pluginu.
 *
 * ## Jak działa renderowanie
 *
 * Metoda `render()` generuje kompletny HTML widgetu po stronie PHP (SSR).
 * JavaScript w bielik-ask.js szuka elementów po klasach BEM i podpina obsługę
 * zdarzeń — nie tworzy elementów DOM dynamicznie (z wyjątkiem przycisków chunków).
 *
 * Dlatego HTML musi zawierać wszystkie kontenery z góry (answer-box, stats, chunks,
 * modal), ukryte atrybutem `hidden`. JS odkrywa je w miarę potrzeby.
 *
 * ## Wielokrotne instancje na stronie
 *
 * Każdy widget renderuje własną kopię HTML z unikalnymi ID opartymi na `$this->get_id()`.
 * JavaScript identyfikuje powiązany modal po atrybucie `data-modal` na liście chunków.
 * Dzięki temu kilka widgetów na tej samej stronie działa niezależnie.
 *
 * @see Bielik_Admin_Settings Zarządza parametrami technicznymi (API URL, RAG, token).
 * @see Bielik_Rest_Proxy     Proxy PHP przekazujące żądania do serwera FastAPI.
 * @see assets/js/bielik-ask.js Logika frontendu (fetch, renderowanie wyników, modal).
 */
class Bielik_Ask_Widget extends \Elementor\Widget_Base {

	/**
	 * Zwraca unikalną nazwę (slug) widgetu używaną wewnętrznie przez Elementor.
	 *
	 * Nazwa musi być globalnie unikalna wśród wszystkich zainstalowanych widgetów.
	 * Elementor używa jej jako klucza w rejestrze widgetów, w zapisanych danych strony
	 * (post meta) oraz w CSS-owym selektorze `.elementor-widget-{name}` otaczającym
	 * każdą instancję widgetu.
	 *
	 * Wartość `bielik_ask` odpowiada nazwie hooka Elementor Frontend:
	 * `frontend/element_ready/bielik_ask.default` — hook używany w bielik-ask.js
	 * do reinicjalizacji widgetu po edycji w panelu Elementor (np. po drag & drop,
	 * zmianie ustawień, skopiowaniu).
	 *
	 * @return string Slug widgetu.
	 */
	public function get_name(): string {
		return 'bielik_ask';
	}

	/**
	 * Zwraca wyświetlaną nazwę widgetu w panelu Elementor.
	 *
	 * Nazwa pojawia się w wyszukiwarce widgetów, na pasku tytułu podczas edycji
	 * oraz w podpowiedzi (tooltip) przy ikonie widgetu w bibliotece Elementor.
	 * Opakowana w `esc_html__()` dla obsługi tłumaczeń i zapobiegania XSS.
	 *
	 * @return string Zlokalizowana nazwa widgetu.
	 */
	public function get_title(): string {
		return esc_html__( 'Bielik RAG — Pytanie', 'bielik-rag-widget' );
	}

	/**
	 * Zwraca identyfikator ikony widgetu wyświetlanej w bibliotece Elementor.
	 *
	 * Elementor korzysta z zestawu ikon `eicon-*` (własna biblioteka fontów)
	 * oraz Font Awesome (`fa fa-*`). Ikona `eicon-chat` (dymek rozmowy)
	 * najlepiej oddaje charakter widgetu Q&A.
	 *
	 * @return string Klasa CSS ikony Elementor.
	 */
	public function get_icon(): string {
		return 'eicon-chat';
	}

	/**
	 * Zwraca tablicę kategorii, do których należy widget w bibliotece Elementor.
	 *
	 * Kategorie decydują o tym, w której zakładce biblioteki pojawi się widget.
	 * Dostępne kategorie domyślne: 'basic', 'pro-elements', 'general', 'woocommerce'.
	 * Kategoria `general` jest najbardziej ogólna i zawsze dostępna (nie wymaga Elementor Pro).
	 *
	 * @return string[] Tablica slugów kategorii.
	 */
	public function get_categories(): array {
		return [ 'general' ];
	}

	/**
	 * Zwraca słowa kluczowe umożliwiające znalezienie widgetu przez wyszukiwarkę Elementor.
	 *
	 * Gdy użytkownik wpisze np. "bielik" lub "rag" w polu wyszukiwania widgetów,
	 * Elementor przeszukuje tę tablicę i wyświetla pasujące wyniki.
	 * Dobrze dobrane słowa kluczowe skracają czas znajdowania widgetu.
	 *
	 * @return string[] Tablica słów kluczowych (bez lokalizacji — wyszukiwarka używa exact match).
	 */
	public function get_keywords(): array {
		return [ 'bielik', 'rag', 'ai', 'pytanie', 'llm', 'chat' ];
	}

	/**
	 * Zwraca tablicę uchwytów (handles) arkuszy CSS potrzebnych przez ten widget.
	 *
	 * Elementor ładuje wymienione arkusze automatycznie na każdej stronie,
	 * na której widget jest użyty — nie trzeba ich ręcznie kolejkować (enqueue).
	 * Arkusz `bielik-ask` musi być wcześniej zarejestrowany przez `wp_register_style()`
	 * w hooku `wp_enqueue_scripts` (rejestracja odbywa się w głównym pliku pluginu).
	 *
	 * Uwaga: w edytorze Elementor style są ładowane bez względu na tę metodę —
	 * Elementor ładuje wszystkie zarejestrowane style w środowisku edytora.
	 * Metoda ta ma znaczenie głównie dla frontendu (widoku publicznego).
	 *
	 * @return string[] Tablica uchwytów zarejestrowanych arkuszy CSS.
	 */
	public function get_style_depends(): array {
		return [ 'bielik-ask' ];
	}

	/**
	 * Zwraca tablicę uchwytów (handles) skryptów JS potrzebnych przez ten widget.
	 *
	 * Analogicznie do `get_style_depends()` — Elementor załaduje wymienione skrypty
	 * automatycznie na stronach z tym widgetem. Skrypt `bielik-ask` musi być
	 * wcześniej zarejestrowany przez `wp_register_script()` w głównym pliku pluginu.
	 *
	 * Skrypt jest kolejkowany z `$in_footer = true`, więc trafi na koniec `<body>`.
	 * Dzięki temu DOM jest już zbudowany w momencie, gdy `DOMContentLoaded` odpala
	 * inicjalizację widgetu w bielik-ask.js.
	 *
	 * @return string[] Tablica uchwytów zarejestrowanych skryptów JS.
	 */
	public function get_script_depends(): array {
		return [ 'bielik-ask' ];
	}

	// ── Controls ──────────────────────────────────────────────────────────────

	/**
	 * Rejestruje wszystkie kontrolki (pola konfiguracji) widgetu w panelu Elementor.
	 *
	 * Metoda wywoływana przez Elementor podczas ładowania widgetu. Definiuje pełną
	 * strukturę panelu edytora: zakładki, sekcje i pola. Każde zdefiniowane pole
	 * jest automatycznie zapisywane przez Elementor w post meta strony i dostępne
	 * w metodzie `render()` przez `$this->get_settings_for_display()`.
	 *
	 * ### Struktura panelu
	 *
	 * **Zakładka Content — sekcja "Treści"** (TAB_CONTENT):
	 * - `widget_title`    — tytuł nagłówka widgetu (TEXT)
	 * - `widget_subtitle` — podtytuł opisowy (TEXT, label_block)
	 * - `placeholder_text`— tekst placeholder w polu pytania (TEXTAREA)
	 * - `button_text`     — etykieta przycisku wysyłającego (TEXT)
	 * - `chunks_label`    — nagłówek sekcji ze źródłami RAG (TEXT)
	 *
	 * **Zakładka Style — sekcja "Kolory"** (TAB_STYLE):
	 * - `color_primary`       — główny kolor marki (przycisk, tytuł, chunk buttons);
	 *                           domyślnie granatowy #003d7a
	 * - `color_primary_hover` — kolor hover przycisku i chunk buttons;
	 *                           domyślnie pomarańczowy #e67e22
	 * - `color_widget_bg`     — tło całego widgetu; domyślnie białe #ffffff
	 * - `color_answer_bg`     — tło pudełka z odpowiedzią; domyślnie jasnoniebieski #eef3fa
	 * - `color_stats_bg`      — tło paska statystyk; domyślnie jasnoszary #f5f7fa
	 *
	 *   Kolory używają mechanizmu `selectors` Elementor — CSS jest generowany
	 *   automatycznie z selektorem `{{WRAPPER}}` (unikalny selektor instancji widgetu),
	 *   co zapewnia izolację między wieloma widgetami na stronie.
	 *
	 * **Zakładka Style — sekcja "Typografia"** (TAB_STYLE):
	 * - `typo_title`  — typografia tytułu (Group_Control_Typography)
	 * - `typo_answer` — typografia treści odpowiedzi
	 * - `typo_stats`  — typografia paska statystyk
	 *
	 *   Group_Control_Typography to złożona kontrolka Elementor generująca kilka
	 *   właściwości CSS naraz (font-family, font-size, font-weight, line-height,
	 *   letter-spacing, text-transform). Użytkownik ma dostęp do pełnego wyboru czcionek
	 *   Google Fonts bez pisania kodu.
	 *
	 * **Zakładka Style — sekcja "Układ i obramowanie"** (TAB_STYLE):
	 * - `border_radius`  — zaokrąglenie rogów widgetu i przycisku (SLIDER 0–32px)
	 * - `widget_shadow`  — cień widgetu (Group_Control_Box_Shadow)
	 *
	 * @return void
	 */
	protected function register_controls(): void {

		// ── Content: Treści ───────────────────────────────────────────────────
		$this->start_controls_section( 'section_content', [
			'label' => esc_html__( 'Treści', 'bielik-rag-widget' ),
			'tab'   => \Elementor\Controls_Manager::TAB_CONTENT,
		] );

		$this->add_control( 'widget_title', [
			'label'   => esc_html__( 'Tytuł', 'bielik-rag-widget' ),
			'type'    => \Elementor\Controls_Manager::TEXT,
			'default' => esc_html__( 'Zapytaj Bielika', 'bielik-rag-widget' ),
		] );

		$this->add_control( 'widget_subtitle', [
			'label'   => esc_html__( 'Podtytuł', 'bielik-rag-widget' ),
			'type'    => \Elementor\Controls_Manager::TEXT,
			'default' => esc_html__( 'Zadaj pytanie dotyczące dokumentacji technicznej urządzeń', 'bielik-rag-widget' ),
			'label_block' => true,
		] );

		$this->add_control( 'placeholder_text', [
			'label'       => esc_html__( 'Placeholder pola pytania', 'bielik-rag-widget' ),
			'type'        => \Elementor\Controls_Manager::TEXTAREA,
			'default'     => esc_html__( 'Np. Jakie jest napięcie znamionowe licznika ORNO OR-WE-516?', 'bielik-rag-widget' ),
			'rows'        => 2,
			'label_block' => true,
		] );

		$this->add_control( 'button_text', [
			'label'   => esc_html__( 'Tekst przycisku', 'bielik-rag-widget' ),
			'type'    => \Elementor\Controls_Manager::TEXT,
			'default' => esc_html__( 'Zapytaj', 'bielik-rag-widget' ),
		] );

		$this->add_control( 'chunks_label', [
			'label'   => esc_html__( 'Etykieta sekcji źródeł RAG', 'bielik-rag-widget' ),
			'type'    => \Elementor\Controls_Manager::TEXT,
			'default' => esc_html__( 'Źródła RAG', 'bielik-rag-widget' ),
		] );

		$this->end_controls_section();

		// ── Style: Kolory ─────────────────────────────────────────────────────
		$this->start_controls_section( 'section_style_colors', [
			'label' => esc_html__( 'Kolory', 'bielik-rag-widget' ),
			'tab'   => \Elementor\Controls_Manager::TAB_STYLE,
		] );

		$this->add_control( 'color_primary', [
			'label'   => esc_html__( 'Kolor główny (przycisk, tytuł, linki)', 'bielik-rag-widget' ),
			'type'    => \Elementor\Controls_Manager::COLOR,
			'default' => '#003d7a',
			'selectors' => [
				'{{WRAPPER}} .bielik-widget__btn'                  => 'background-color: {{VALUE}}',
				'{{WRAPPER}} .bielik-widget__title'                => 'color: {{VALUE}}',
				'{{WRAPPER}} .bielik-widget__chunk-btn'            => 'color: {{VALUE}}; border-color: {{VALUE}}',
				'{{WRAPPER}} .bielik-widget__answer-label'         => 'color: {{VALUE}}',
				'{{WRAPPER}} .bielik-widget__chunks-label'         => 'color: {{VALUE}}',
			],
		] );

		$this->add_control( 'color_primary_hover', [
			'label'   => esc_html__( 'Kolor przycisku (hover)', 'bielik-rag-widget' ),
			'type'    => \Elementor\Controls_Manager::COLOR,
			'default' => '#e67e22',
			'selectors' => [
				'{{WRAPPER}} .bielik-widget__btn:hover'            => 'background-color: {{VALUE}}',
				'{{WRAPPER}} .bielik-widget__chunk-btn:hover'      => 'background-color: {{VALUE}}; border-color: {{VALUE}}; color: #fff',
			],
		] );

		$this->add_control( 'color_widget_bg', [
			'label'   => esc_html__( 'Tło widgetu', 'bielik-rag-widget' ),
			'type'    => \Elementor\Controls_Manager::COLOR,
			'default' => '#ffffff',
			'selectors' => [
				'{{WRAPPER}} .bielik-widget' => 'background-color: {{VALUE}}',
			],
		] );

		$this->add_control( 'color_answer_bg', [
			'label'   => esc_html__( 'Tło pola odpowiedzi', 'bielik-rag-widget' ),
			'type'    => \Elementor\Controls_Manager::COLOR,
			'default' => '#eef3fa',
			'selectors' => [
				'{{WRAPPER}} .bielik-widget__answer-box' => 'background-color: {{VALUE}}',
			],
		] );

		$this->add_control( 'color_stats_bg', [
			'label'   => esc_html__( 'Tło paska statystyk', 'bielik-rag-widget' ),
			'type'    => \Elementor\Controls_Manager::COLOR,
			'default' => '#f5f7fa',
			'selectors' => [
				'{{WRAPPER}} .bielik-widget__stats' => 'background-color: {{VALUE}}',
			],
		] );

		$this->end_controls_section();

		// ── Style: Typografia ─────────────────────────────────────────────────
		$this->start_controls_section( 'section_style_typo', [
			'label' => esc_html__( 'Typografia', 'bielik-rag-widget' ),
			'tab'   => \Elementor\Controls_Manager::TAB_STYLE,
		] );

		$this->add_group_control( \Elementor\Group_Control_Typography::get_type(), [
			'name'     => 'typo_title',
			'label'    => esc_html__( 'Tytuł', 'bielik-rag-widget' ),
			'selector' => '{{WRAPPER}} .bielik-widget__title',
		] );

		$this->add_group_control( \Elementor\Group_Control_Typography::get_type(), [
			'name'     => 'typo_answer',
			'label'    => esc_html__( 'Treść odpowiedzi', 'bielik-rag-widget' ),
			'selector' => '{{WRAPPER}} .bielik-widget__answer-text',
		] );

		$this->add_group_control( \Elementor\Group_Control_Typography::get_type(), [
			'name'     => 'typo_stats',
			'label'    => esc_html__( 'Statystyki', 'bielik-rag-widget' ),
			'selector' => '{{WRAPPER}} .bielik-widget__stat',
		] );

		$this->end_controls_section();

		// ── Style: Układ ──────────────────────────────────────────────────────
		$this->start_controls_section( 'section_style_layout', [
			'label' => esc_html__( 'Układ i obramowanie', 'bielik-rag-widget' ),
			'tab'   => \Elementor\Controls_Manager::TAB_STYLE,
		] );

		$this->add_control( 'border_radius', [
			'label'      => esc_html__( 'Zaokrąglenie rogów', 'bielik-rag-widget' ),
			'type'       => \Elementor\Controls_Manager::SLIDER,
			'range'      => [ 'px' => [ 'min' => 0, 'max' => 32 ] ],
			'default'    => [ 'unit' => 'px', 'size' => 8 ],
			'selectors'  => [
				'{{WRAPPER}} .bielik-widget'             => 'border-radius: {{SIZE}}{{UNIT}}',
				'{{WRAPPER}} .bielik-widget__answer-box' => 'border-radius: calc({{SIZE}}{{UNIT}} - 2px)',
				'{{WRAPPER}} .bielik-widget__btn'        => 'border-radius: {{SIZE}}{{UNIT}}',
			],
		] );

		$this->add_group_control( \Elementor\Group_Control_Box_Shadow::get_type(), [
			'name'     => 'widget_shadow',
			'label'    => esc_html__( 'Cień widgetu', 'bielik-rag-widget' ),
			'selector' => '{{WRAPPER}} .bielik-widget',
		] );

		$this->end_controls_section();
	}

	// ── Render ────────────────────────────────────────────────────────────────

	/**
	 * Renderuje kompletny HTML widgetu po stronie PHP.
	 *
	 * Metoda wywoływana przez Elementor przy każdym wyświetleniu strony zawierającej
	 * widget — zarówno na froncie (widok publiczny), jak i w podglądzie edytora Elementor.
	 * Wynik jest bezpośrednio echo-wany do bufora wyjściowego (brak wartości zwracanej).
	 *
	 * ### Unikalne ID instancji
	 *
	 * Każdy widget na stronie generuje unikalne ID:
	 * - `$widget_id = 'bielik-' . $this->get_id()` — ID kontenera `.bielik-widget`
	 * - `$modal_id  = 'bielik-modal-' . $this->get_id()` — ID elementu modalnego
	 *
	 * `get_id()` zwraca alfanumeryczny klucz przypisany przez Elementor do konkretnej
	 * instancji widgetu (np. `a1b2c3`). Dzięki unikalnym ID można umieścić wiele
	 * widgetów na tej samej stronie bez kolizji.
	 *
	 * ### Struktura HTML
	 *
	 * ```
	 * .bielik-widget#bielik-{id}
	 * ├── .bielik-widget__header
	 * │   ├── h3.bielik-widget__title         (jeśli niepuste)
	 * │   └── p.bielik-widget__subtitle       (jeśli niepuste)
	 * ├── .bielik-widget__form
	 * │   ├── textarea.bielik-widget__input
	 * │   ├── p.bielik-widget__hint           "Ctrl + Enter aby wysłać"
	 * │   └── button.bielik-widget__btn
	 * │       ├── span.bielik-widget__btn-text
	 * │       └── span.bielik-widget__btn-spinner   (aria-hidden, zawsze w DOM)
	 * ├── .bielik-widget__error [hidden]      (role="alert" — czytniki ekranu)
	 * └── .bielik-widget__results [hidden]
	 *     ├── .bielik-widget__answer-box
	 *     │   ├── .bielik-widget__answer-label  "Odpowiedź"
	 *     │   └── .bielik-widget__answer-text   (wypełniany przez JS)
	 *     ├── .bielik-widget__stats
	 *     │   ├── [stat] Czas          data-stat="time_total_s"
	 *     │   ├── [stat] TTFT          data-stat="time_to_first_token_s"
	 *     │   ├── [stat] Tokeny        data-stat="tokens_generated"
	 *     │   ├── [stat] Tok/s         data-stat="tokens_per_second"
	 *     │   ├── [stat] Chunki RAG    data-stat="rag_chunks_used"  [hidden, warunkowo]
	 *     │   └── [stat] Model         data-stat="model"
	 *     └── .bielik-widget__chunks [hidden]
	 *         ├── .bielik-widget__chunks-label
	 *         └── .bielik-widget__chunks-list  data-modal="bielik-modal-{id}"
	 *
	 * .bielik-chunk-modal#bielik-modal-{id} [hidden]  (poza widgetem, na poziomie body)
	 * ├── .bielik-chunk-modal__backdrop
	 * └── .bielik-chunk-modal__box
	 *     ├── .bielik-chunk-modal__header
	 *     │   ├── span.bielik-chunk-modal__title#bielik-modal-{id}-title
	 *     │   └── button.bielik-chunk-modal__close   "×"
	 *     ├── .bielik-chunk-modal__meta              (wypełniany przez JS)
	 *     └── pre.bielik-chunk-modal__text           (wypełniany przez JS)
	 * ```
	 *
	 * ### Atrybuty data-stat
	 *
	 * Elementy `<span data-stat="...">` są wypełniane przez `renderResult()` w bielik-ask.js.
	 * Atrybut wskazuje klucz w obiekcie odpowiedzi FastAPI (AskResponse):
	 * - `time_total_s`          — całkowity czas od wysłania do odebrania odpowiedzi (s)
	 * - `time_to_first_token_s` — czas do pierwszego tokenu (TTFT), mierzy opóźnienie (s)
	 * - `tokens_generated`      — liczba wygenerowanych tokenów (int)
	 * - `tokens_per_second`     — przepustowość generowania (float, 1 miejsce po przecinku)
	 * - `rag_chunks_used`       — liczba fragmentów RAG użytych w kontekście (int)
	 * - `model`                 — pełna nazwa modelu np. "SpeakLeash/bielik-11b-v3.0-instruct:Q4_K_M"
	 *
	 * ### Sekcja .bielik-chunk-modal
	 *
	 * Modal renderowany jest poza kontenerem `.bielik-widget` (na tym samym poziomie w DOM),
	 * z `position: fixed` i `z-index: 99999`. Renderowanie poza widgetem jest konieczne,
	 * ponieważ modal musi przykrywać całą stronę — gdyby był dzieckiem `.bielik-widget`,
	 * mógłby być przycinany przez `overflow: hidden` rodzica lub przez niższy z-index.
	 *
	 * Powiązanie widget → modal odbywa się przez atrybut `data-modal` na liście chunków:
	 * `data-modal="bielik-modal-{id}"` — JS szuka elementu o tym ID.
	 *
	 * ### Bezpieczeństwo
	 *
	 * Wszystkie wartości z ustawień Elementor (`$settings`) są escapowane przez:
	 * - `esc_html()` — dla tekstu wyświetlanego między tagami
	 * - `esc_attr()` — dla wartości w atrybutach HTML
	 * - `esc_html_e()` / `esc_attr_e()` — jak wyżej, ale z echo i natychmiastowym wydrukiem
	 *
	 * Dynamiczne dane (fragmenty RAG, odpowiedź modelu) są wstawiane wyłącznie przez JS
	 * z użyciem `textContent` (nigdy `innerHTML`), co eliminuje ryzyko XSS.
	 *
	 * @return void Wynik jest echo-wany bezpośrednio, brak wartości zwracanej.
	 */
	protected function render(): void {
		$settings  = $this->get_settings_for_display();
		$widget_id = 'bielik-' . $this->get_id();
		$modal_id  = 'bielik-modal-' . $this->get_id();
		?>
		<div class="bielik-widget" id="<?php echo esc_attr( $widget_id ); ?>">

			<div class="bielik-widget__header">
				<?php if ( ! empty( $settings['widget_title'] ) ) : ?>
					<h3 class="bielik-widget__title">
						<?php echo esc_html( $settings['widget_title'] ); ?>
					</h3>
				<?php endif; ?>
				<?php if ( ! empty( $settings['widget_subtitle'] ) ) : ?>
					<p class="bielik-widget__subtitle">
						<?php echo esc_html( $settings['widget_subtitle'] ); ?>
					</p>
				<?php endif; ?>
			</div>

			<div class="bielik-widget__form">
				<textarea
					class="bielik-widget__input"
					placeholder="<?php echo esc_attr( $settings['placeholder_text'] ); ?>"
					rows="3"
					aria-label="<?php esc_attr_e( 'Pytanie', 'bielik-rag-widget' ); ?>"
				></textarea>
				<p class="bielik-widget__hint">
					<?php esc_html_e( 'Ctrl + Enter aby wysłać', 'bielik-rag-widget' ); ?>
				</p>
				<button class="bielik-widget__btn" type="button">
					<span class="bielik-widget__btn-text">
						<?php echo esc_html( $settings['button_text'] ); ?>
					</span>
					<span class="bielik-widget__btn-spinner" aria-hidden="true"></span>
				</button>
			</div>

			<div class="bielik-widget__error" hidden role="alert"></div>

			<div class="bielik-widget__results" hidden>

				<div class="bielik-widget__answer-box">
					<div class="bielik-widget__answer-label">
						<?php esc_html_e( 'Odpowiedź', 'bielik-rag-widget' ); ?>
					</div>
					<div class="bielik-widget__answer-text"></div>
				</div>

				<div class="bielik-widget__stats" aria-label="<?php esc_attr_e( 'Statystyki', 'bielik-rag-widget' ); ?>">
					<div class="bielik-widget__stat">
						<span class="bielik-widget__stat-label"><?php esc_html_e( 'Czas', 'bielik-rag-widget' ); ?></span>
						<span class="bielik-widget__stat-value" data-stat="client_time_s">—</span>
						<span class="bielik-widget__stat-unit">s</span>
					</div>
					<div class="bielik-widget__stat">
						<span class="bielik-widget__stat-label"><?php esc_html_e( 'LLM', 'bielik-rag-widget' ); ?></span>
						<span class="bielik-widget__stat-value" data-stat="time_total_s">—</span>
						<span class="bielik-widget__stat-unit">s</span>
					</div>
					<div class="bielik-widget__stat">
						<span class="bielik-widget__stat-label"><?php esc_html_e( 'TTFT', 'bielik-rag-widget' ); ?></span>
						<span class="bielik-widget__stat-value" data-stat="time_to_first_token_s">—</span>
						<span class="bielik-widget__stat-unit">s</span>
					</div>
					<div class="bielik-widget__stat">
						<span class="bielik-widget__stat-label"><?php esc_html_e( 'Tokeny', 'bielik-rag-widget' ); ?></span>
						<span class="bielik-widget__stat-value" data-stat="tokens_generated">—</span>
					</div>
					<div class="bielik-widget__stat">
						<span class="bielik-widget__stat-label"><?php esc_html_e( 'Tok/s', 'bielik-rag-widget' ); ?></span>
						<span class="bielik-widget__stat-value" data-stat="tokens_per_second">—</span>
					</div>
					<div class="bielik-widget__stat bielik-widget__stat--rag" hidden>
						<span class="bielik-widget__stat-label"><?php esc_html_e( 'Chunki RAG', 'bielik-rag-widget' ); ?></span>
						<span class="bielik-widget__stat-value" data-stat="rag_chunks_used">—</span>
					</div>
					<div class="bielik-widget__stat bielik-widget__stat--model">
						<span class="bielik-widget__stat-label"><?php esc_html_e( 'Model', 'bielik-rag-widget' ); ?></span>
						<span class="bielik-widget__stat-value bielik-widget__stat-value--model" data-stat="model">—</span>
					</div>
				</div>

				<div class="bielik-widget__chunks" hidden>
					<div class="bielik-widget__chunks-label">
						<?php echo esc_html( $settings['chunks_label'] ); ?>
					</div>
					<div class="bielik-widget__chunks-list"
					     data-modal="<?php echo esc_attr( $modal_id ); ?>">
					</div>
				</div>

			</div>
		</div>

		<div class="bielik-chunk-modal"
		     id="<?php echo esc_attr( $modal_id ); ?>"
		     hidden
		     role="dialog"
		     aria-modal="true"
		     aria-labelledby="<?php echo esc_attr( $modal_id ); ?>-title">
			<div class="bielik-chunk-modal__backdrop"></div>
			<div class="bielik-chunk-modal__box">
				<div class="bielik-chunk-modal__header">
					<span class="bielik-chunk-modal__title"
					      id="<?php echo esc_attr( $modal_id ); ?>-title"></span>
					<button class="bielik-chunk-modal__close"
					        aria-label="<?php esc_attr_e( 'Zamknij', 'bielik-rag-widget' ); ?>">
						&times;
					</button>
				</div>
				<div class="bielik-chunk-modal__meta"></div>
				<pre class="bielik-chunk-modal__text"></pre>
			</div>
		</div>
		<?php
	}
}

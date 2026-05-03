<?php
/**
 * Plugin Name:  Bielik RAG Widget
 * Plugin URI:   https://github.com/protechnologia/bielik-runpod
 * Description:  Widget Elementor do zadawania pytań modelowi Bielik 11B z obsługą RAG.
 * Version:      1.0.4
 * Author:       ProTechnologia
 * Author URI:   http://protechnologia.pl
 * Text Domain:  bielik-rag-widget
 * Requires PHP: 7.4
 *
 * ## Architektura pluginu
 *
 * Plugin składa się z czterech warstw:
 *
 *  1. **Bootstrap** (ten plik) — definiuje stałe, ładuje klasy, rejestruje hooki WP.
 *  2. **Panel admina** (`includes/class-admin-settings.php`) — strona konfiguracji
 *     w Ustawienia → Bielik RAG; przechowuje parametry techniczne w wp_options.
 *  3. **Proxy REST API** (`includes/class-rest-proxy.php`) — endpoint
 *     POST /wp-json/bielik/v1/ask, który pośredniczy między przeglądarką a FastAPI.
 *  4. **Widget Elementor** (`widgets/bielik-ask-widget.php`) — renderuje HTML formularza
 *     Q&A; kontrolki tylko prezentacyjne (treści, kolory, typografia).
 *
 * Assety frontendowe:
 *  - `assets/css/bielik-ask.css` — style BEM dla widgetu i modalu chunków RAG.
 *  - `assets/js/bielik-ask.js`  — logika AJAX, renderowanie wyników, obsługa modalu.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * Bezwzględna ścieżka do głównego pliku pluginu.
 *
 * Używana przez WordPress do identyfikacji pluginu (np. w filtrze `plugin_action_links_`).
 * Wartość: absolutna ścieżka systemu plików, np. `/var/www/html/wp-content/plugins/bielik-rag-widget/bielik-rag-widget.php`.
 */
define( 'BIELIK_PLUGIN_FILE',    __FILE__ );

/**
 * Bezwzględna ścieżka do katalogu pluginu (z trailing slash).
 *
 * Używana do ładowania plików PHP przez `require_once`. Przykład:
 * `/var/www/html/wp-content/plugins/bielik-rag-widget/`
 * Zbudowana przez `plugin_dir_path(__FILE__)`, który obsługuje poprawnie
 * zarówno standardowy katalog pluginów, jak i niestandardowe lokalizacje.
 */
define( 'BIELIK_PLUGIN_PATH',    plugin_dir_path( __FILE__ ) );

/**
 * Publiczny URL katalogu pluginu (z trailing slash).
 *
 * Używana do budowania URL-i assetów (CSS, JS, obrazy) w `wp_register_style()`
 * i `wp_register_script()`. Przykład:
 * `https://example.com/wp-content/plugins/bielik-rag-widget/`
 * `plugin_dir_url(__FILE__)` automatycznie uwzględnia protokół (http/https)
 * i ścieżkę do wp-content.
 */
define( 'BIELIK_PLUGIN_URL',     plugin_dir_url( __FILE__ ) );

/**
 * Wersja pluginu — string używany jako parametr `$ver` w `wp_register_style/script`.
 *
 * WordPress doklejа wersję jako query string do URL assetów (?ver=1.0.0),
 * co wymusza odświeżenie cache przeglądarki po aktualizacji pluginu.
 * Należy zwiększać przy każdym wydaniu zmieniającym CSS lub JS.
 */
define( 'BIELIK_PLUGIN_VERSION', '1.0.4' );

// ── Admin settings page ──────────────────────────────────────────────────────

/**
 * Ładuje klasę panelu administracyjnego i inicjalizuje jej hooki WordPress.
 *
 * `require_once` gwarantuje jednokrotne załadowanie pliku — bezpieczne nawet
 * jeśli coś wywołałoby tę linię dwukrotnie (choć nie powinno).
 * `( new Bielik_Admin_Settings() )->init()` tworzy instancję i od razu rejestruje
 * hooki `admin_menu`, `admin_init` i `plugin_action_links_*`.
 *
 * @see Bielik_Admin_Settings::init()
 */
require_once BIELIK_PLUGIN_PATH . 'includes/class-admin-settings.php';
( new Bielik_Admin_Settings() )->init();

// ── REST API proxy ───────────────────────────────────────────────────────────

/**
 * Ładuje klasę proxy REST API i rejestruje endpoint /wp-json/bielik/v1/ask.
 *
 * `init()` podłącza `register_route()` pod hook `rest_api_init`, który jest
 * wywoływany przez WordPress gdy inicjalizuje się serwer REST API (przed odpowiedzią
 * na każde żądanie do /wp-json/). Endpoint przyjmuje POST z parametrem `prompt`
 * i przekazuje żądanie do serwera FastAPI z modelem Bielik.
 *
 * @see Bielik_Rest_Proxy::init()
 * @see Bielik_Rest_Proxy::handle()
 */
require_once BIELIK_PLUGIN_PATH . 'includes/class-rest-proxy.php';
( new Bielik_Rest_Proxy() )->init();

// ── Enqueue frontend assets ──────────────────────────────────────────────────

/**
 * Rejestruje i konfiguruje assety CSS i JS widgetu Bielik.
 *
 * Callback podłączony pod hook `wp_enqueue_scripts`, który WordPress wywołuje
 * podczas budowania frontendu strony (nie w adminie). Używamy `wp_register_*`
 * zamiast `wp_enqueue_*`, ponieważ faktyczne kolejkowanie (enqueue) jest
 * delegowane do Elementor — nastąpi automatycznie tylko na stronach, gdzie
 * widget jest użyty (patrz `get_style_depends()` i `get_script_depends()`
 * w klasie Bielik_Ask_Widget).
 *
 * ### Rejestracja CSS
 *
 * `wp_register_style( 'bielik-ask', ... )` rejestruje arkusz pod uchwytem
 * `bielik-ask`. Elementor zakolejkuje go automatycznie gdy wykryje widget
 * z `get_style_depends() = ['bielik-ask']`.
 *
 * ### Rejestracja JS
 *
 * `wp_register_script( 'bielik-ask', ..., $in_footer = true )` rejestruje skrypt
 * do wstawienia przed `</body>`, co gwarantuje dostępność pełnego DOM podczas
 * inicjalizacji widgetu przez `DOMContentLoaded` lub `Elementor Frontend` hooks.
 *
 * ### Lokalizacja skryptu (BielikConfig)
 *
 * `wp_localize_script()` wstrzykuje do strony blok JavaScript:
 * ```js
 * var BielikConfig = { "restUrl": "...", "nonce": "..." };
 * ```
 * Obiekt jest dostępny globalnie w bielik-ask.js.
 *
 * - `restUrl` — pełny URL endpointu proxy, np.
 *   `https://example.com/wp-json/bielik/v1/ask`.
 *   Zbudowany przez `rest_url()` + `esc_url()` — obsługuje niestandardowe
 *   ścieżki WP i zapewnia poprawny protokół.
 *
 * - `nonce` — jednorazowy token CSRF wygenerowany przez `wp_create_nonce('wp_rest')`.
 *   JS dołącza go jako nagłówek `X-WP-Nonce` do każdego żądania fetch.
 *   WordPress weryfikuje nonce automatycznie dla zalogowanych użytkowników.
 *   Dla niezalogowanych gości nonce nadal jest wysyłany — WP akceptuje żądania
 *   REST od anonimowych użytkowników gdy `permission_callback = __return_true`.
 *
 * @return void
 */
add_action( 'wp_enqueue_scripts', function () {
	wp_register_style(
		'bielik-ask',
		BIELIK_PLUGIN_URL . 'assets/css/bielik-ask.css',
		[],
		BIELIK_PLUGIN_VERSION
	);
	wp_register_script(
		'bielik-ask',
		BIELIK_PLUGIN_URL . 'assets/js/bielik-ask.js',
		[],
		BIELIK_PLUGIN_VERSION,
		true
	);
	wp_localize_script( 'bielik-ask', 'BielikConfig', [
		'restUrl' => esc_url( rest_url( 'bielik/v1/ask' ) ),
		'nonce'   => wp_create_nonce( 'wp_rest' ),
	] );
} );

// ── Register Elementor widget ────────────────────────────────────────────────

/**
 * Ładuje i rejestruje widget Bielik_Ask_Widget w rejestrze Elementor.
 *
 * Callback podłączony pod hook `elementor/widgets/register`, który Elementor
 * wywołuje po załadowaniu swojego jądra — gwarantuje, że klasa bazowa
 * `\Elementor\Widget_Base` jest dostępna w momencie rozszerzenia.
 *
 * Wzorzec `require_once` wewnątrz callbacku jest celowy: plik widgetu nie jest
 * ładowany gdy Elementor nie jest aktywny (np. na stronach bez edytora lub gdy
 * plugin Elementor jest wyłączony). Dzięki temu unikamy fatal error "class not found"
 * przy próbie rozszerzenia `\Elementor\Widget_Base`.
 *
 * `$widgets_manager->register( new Bielik_Ask_Widget() )` — metoda `register()`
 * przyjmuje instancję widgetu, odczytuje jej slug przez `get_name()` i dodaje
 * do wewnętrznego rejestru. Od tego momentu widget jest dostępny w bibliotece
 * Elementor, edytorze drag-and-drop i na stronach frontendowych.
 *
 * @param \Elementor\Widgets_Manager $widgets_manager Menedżer widgetów Elementor,
 *                                                     wstrzykiwany automatycznie przez WP.
 * @return void
 */
add_action( 'elementor/widgets/register', function ( $widgets_manager ) {
	require_once BIELIK_PLUGIN_PATH . 'widgets/bielik-ask-widget.php';
	$widgets_manager->register( new Bielik_Ask_Widget() );
} );

// ── Warn if Elementor is not active ─────────────────────────────────────────

/**
 * Wyświetla ostrzeżenie w panelu admina gdy Elementor nie jest aktywny.
 *
 * Callback podłączony pod hook `admin_notices`, wywołany przez WordPress
 * na każdej stronie panelu administracyjnego przed wyświetleniem zawartości.
 *
 * `did_action('elementor/loaded')` zwraca liczbę całkowitą > 0 jeśli hook
 * `elementor/loaded` został już wyemitowany — co oznacza, że Elementor jest
 * załadowany i aktywny. Przy wartości > 0 funkcja natychmiast kończy działanie
 * (early return), nie wyświetlając żadnego komunikatu.
 *
 * Jeśli Elementor nie jest aktywny, wyświetla notice typu `notice-warning`
 * (żółte tło) z klasą `is-dismissible` (przycisk zamknięcia ×).
 * Komunikat jest zlokalizowany przez `esc_html__()`.
 *
 * Ostrzeżenie jest wyświetlane na wszystkich stronach admina — nie tylko na
 * stronie pluginu — bo brak Elementor powoduje, że cały widget jest niefunkcjonalny.
 *
 * @return void
 */
add_action( 'admin_notices', function () {
	if ( did_action( 'elementor/loaded' ) ) {
		return;
	}
	printf(
		'<div class="notice notice-warning is-dismissible"><p>%s</p></div>',
		esc_html__( 'Bielik RAG Widget wymaga aktywnego pluginu Elementor.', 'bielik-rag-widget' )
	);
} );

/**
 * Bielik RAG Widget — logika frontendowa.
 *
 * Plik jest ładowany na każdej stronie WordPress, na której aktywny jest
 * plugin Bielik RAG Widget (rejestracja przez wp_register_script + get_script_depends).
 * Nie ma zewnętrznych zależności — działa w czystym vanilla JS (ES5+).
 *
 * Konfiguracja wstrzykiwana przez wp_localize_script (bielik-rag-widget.php):
 *   window.BielikConfig.restUrl  — pełny URL endpointu proxy WordPress,
 *                                  np. https://example.com/wp-json/bielik/v1/ask
 *   window.BielikConfig.nonce    — jednorazowy token WordPress REST API
 *                                  (nagłówek X-WP-Nonce), chroni przed CSRF.
 *
 * Cały kod owinięty jest w IIFE (Immediately Invoked Function Expression),
 * żeby żadna zmienna nie wyciekła do globalnego scope okna przeglądarki.
 *
 * Przepływ działania:
 *   1. Po załadowaniu DOM wywołuje initAll(), która szuka wszystkich
 *      elementów .bielik-widget na stronie i inicjalizuje każdy oddzielnie.
 *   2. Każdy widget działa niezależnie — można ich mieć wiele na jednej stronie.
 *   3. Po kliknięciu przycisku lub Ctrl+Enter: fetch → proxy WP → FastAPI Bielik.
 *   4. Odpowiedź JSON trafia do renderResult(), który wypełnia DOM.
 *   5. Chunki RAG są renderowane jako przyciski otwierające modal.
 */

(function () {
	'use strict';

	/* ── Helpers ────────────────────────────────────────────────────────── */

	/**
	 * Escapuje ciąg znaków do bezpiecznego użycia jako innerHTML.
	 *
	 * Tworzy tymczasowy element div, ustawia jego textContent (co automatycznie
	 * zamienia znaki specjalne HTML na encje), a następnie zwraca gotowy innerHTML.
	 * Dzięki temu żaden tekst pochodzący z API nie może wstrzyknąć tagów HTML
	 * (np. <script> w nazwie źródła RAG lub treści chunka).
	 *
	 * @param  {string} str - Dowolny ciąg znaków do escapowania.
	 * @returns {string}    - Ciąg z escaped encjami HTML (&amp;, &lt;, &gt; itd.).
	 */
	function esc(str) {
		var d = document.createElement('div');
		d.textContent = str;
		return d.innerHTML;
	}

	/**
	 * Pokazuje element HTML usuwając atrybut `hidden`.
	 *
	 * Używane zamiast style.display, żeby respektować domyślny display
	 * elementu zdefiniowany w CSS (block, flex, grid itp.).
	 * CSS zawiera regułę [hidden] { display: none !important; },
	 * więc dodanie/usunięcie atrybutu jest wystarczające.
	 *
	 * @param {HTMLElement} el - Element do pokazania.
	 */
	function show(el) { el.removeAttribute('hidden'); }

	/**
	 * Ukrywa element HTML ustawiając atrybut `hidden`.
	 *
	 * @param {HTMLElement} el - Element do ukrycia.
	 */
	function hide(el) { el.setAttribute('hidden', ''); }

	/* ── Init single widget ─────────────────────────────────────────────── */

	/**
	 * Inicjalizuje jeden widget Bielik RAG na stronie.
	 *
	 * Funkcja pobiera referencje do wszystkich istotnych elementów DOM
	 * wewnątrz przekazanego kontenera, a następnie podpina nasłuchiwacze
	 * zdarzeń (kliknięcie przycisku, Ctrl+Enter na textarea, zdarzenia modala).
	 *
	 * Wzorzec domknięcia (closure): funkcje wewnętrzne submit(), setLoading(),
	 * renderResult(), openModal() i closeModal() mają dostęp do zmiennych
	 * zadeklarowanych w initWidget() przez closure, co eliminuje potrzebę
	 * przekazywania elementów jako parametry przy każdym wywołaniu.
	 *
	 * Zabezpieczenie przed podwójną inicjalizacją: caller przed wywołaniem
	 * ustawia dataset.bielikInit = '1', więc nawet gdyby funkcja była
	 * wywołana ponownie dla tego samego elementu, initAll() to zablokuje.
	 *
	 * Dlaczego jeden widget na raz, a nie globalne zmienne?
	 * Na stronie może znajdować się wiele instancji widgetu Elementor
	 * jednocześnie — każda musi mieć własny stan (odpowiedź, spinner, modal).
	 *
	 * @param {HTMLElement} container - Element .bielik-widget (korzeń widgetu).
	 */
	function initWidget(container) {
		var input      = container.querySelector('.bielik-widget__input');
		var btn        = container.querySelector('.bielik-widget__btn');
		var btnText    = container.querySelector('.bielik-widget__btn-text');
		var errorBox   = container.querySelector('.bielik-widget__error');
		var results    = container.querySelector('.bielik-widget__results');
		var answerText = container.querySelector('.bielik-widget__answer-text');
		var statValues = container.querySelectorAll('[data-stat]');
		var ragStat    = container.querySelector('.bielik-widget__stat--rag');
		var chunksWrap = container.querySelector('.bielik-widget__chunks');
		var chunksList = container.querySelector('.bielik-widget__chunks-list');

		/*
		 * ID modala jest zapisane jako atrybut data-modal na liście chunków,
		 * a nie hardkodowane — każda instancja widgetu Elementor ma unikalny
		 * sufiks ID (np. bielik-modal-abc123), więc wiele widgetów na stronie
		 * nie będzie ze sobą kolidować.
		 */
		var modalId    = chunksList ? chunksList.getAttribute('data-modal') : null;
		var modal      = modalId ? document.getElementById(modalId) : null;

		/* Obrona przed niepełnym HTML (np. błąd szablonu Elementora). */
		if (!input || !btn) return;

		/* ── Submit ─────────────────────────────────────────────────────── */

		/**
		 * Wysyła pytanie użytkownika do serwera i obsługuje odpowiedź.
		 *
		 * Kolejność operacji:
		 *   1. Walidacja: jeśli pole jest puste, ustawia focus i kończy pracę.
		 *   2. Przełącza widget w stan ładowania (przycisk nieaktywny, spinner).
		 *   3. Ukrywa poprzednią odpowiedź i poprzedni błąd.
		 *   4. Wysyła żądanie POST do proxy WordPress REST API:
		 *      - URL i nonce pobrane z window.BielikConfig (wstrzykuje PHP).
		 *      - Ciało żądania zawiera tylko { prompt } — wszystkie parametry RAG
		 *        są konfigurowane w panelu WordPress i dodawane przez PHP proxy,
		 *        dzięki czemu nigdy nie przechodzą przez przeglądarkę.
		 *      - Nagłówek X-WP-Nonce zabezpiecza przed atakami CSRF.
		 *   5. Odpowiedź JSON jest parsowana niezależnie od kodu HTTP (fetch nie
		 *      rzuca błędu dla 4xx/5xx), a następnie ręcznie sprawdzany jest res.ok.
		 *   6. Sukces → renderResult(data). Błąd → wyświetlenie komunikatu.
		 *   7. finally: zawsze wyłącza stan ładowania.
		 *
		 * Dlaczego .then().then() zamiast async/await?
		 * Dla kompatybilności ze starszymi przeglądarkami bez transpilacji.
		 * Plik nie przechodzi przez Babel/webpack.
		 */
		function submit() {
			var prompt = input.value.trim();
			if (!prompt) {
				input.focus();
				return;
			}

			setLoading(true);
			hide(errorBox);
			hide(results);

			var startTime = Date.now();
			var config = window.BielikConfig || {};
			var url    = config.restUrl || '/wp-json/bielik/v1/ask';
			var nonce  = config.nonce  || '';

			fetch(url, {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					'X-WP-Nonce':   nonce,
				},
				body: JSON.stringify({ prompt: prompt }),
			})
			.then(function (res) {
				/*
				 * fetch() nie odrzuca Promise dla błędów HTTP (404, 500 itp.) —
				 * robimy to ręcznie. Najpierw parsujemy JSON (żeby dostać komunikat
				 * błędu z body), a dopiero potem sprawdzamy res.ok.
				 */
				return res.json().then(function (data) {
					return { ok: res.ok, status: res.status, data: data };
				});
			})
			.then(function (res) {
				if (!res.ok) {
					var msg = (res.data && (res.data.message || res.data.code))
						? res.data.message || res.data.code
						: 'Błąd ' + res.status;
					throw new Error(msg);
				}
				res.data.client_time_s = (Date.now() - startTime) / 1000;
				renderResult(res.data);
			})
			.catch(function (err) {
				errorBox.textContent = err.message || 'Wystąpił błąd podczas przetwarzania zapytania. Spróbuj ponownie.';
				show(errorBox);
			})
			.finally(function () {
				setLoading(false);
			});
		}

		/* ── Loading state ──────────────────────────────────────────────── */

		/**
		 * Włącza lub wyłącza wizualny stan ładowania przycisku.
		 *
		 * Gdy `on === true`:
		 *   - Ustawia btn.disabled = true, co blokuje kolejne kliknięcia
		 *     i pomija przycisk w nawigacji klawiaturą (tabindex).
		 *   - Dodaje klasę --loading, która przez CSS pokazuje spinner
		 *     (animowany okrąg CSS keyframes) i wygasza opacity przycisku.
		 *
		 * Gdy `on === false`:
		 *   - Przywraca przycisk do stanu interaktywnego.
		 *
		 * CSS definiuje: .bielik-widget__btn--loading .bielik-widget__btn-spinner
		 * { display: inline-block; } — sam spinner jest zawsze w DOM, tylko
		 * CSS go ukrywa/pokazuje, żeby nie było przeskoku szerokości przycisku.
		 *
		 * @param {boolean} on - true = włącz loading, false = wyłącz.
		 */
		function setLoading(on) {
			btn.disabled = on;
			if (on) {
				btn.classList.add('bielik-widget__btn--loading');
			} else {
				btn.classList.remove('bielik-widget__btn--loading');
			}
		}

		/* ── Render result ──────────────────────────────────────────────── */

		/**
		 * Wypełnia DOM danymi z odpowiedzi API Bielik i pokazuje sekcję wyników.
		 *
		 * Obsługuje pola odpowiadające schematowi AskResponse z api/schemas.py:
		 *   - answer              (string)  — tekst odpowiedzi modelu
		 *   - model               (string)  — nazwa załadowanego modelu
		 *   - time_total_s        (float)   — całkowity czas odpowiedzi w sekundach
		 *   - time_to_first_token_s (float) — czas do pierwszego tokenu (TTFT)
		 *   - tokens_generated    (int)     — liczba wygenerowanych tokenów
		 *   - tokens_per_second   (float)   — prędkość generowania
		 *   - rag_chunks_used     (int)     — liczba chunków użytych w kontekście
		 *   - rag_chunks          (array)   — lista obiektów RagChunk
		 *
		 * Sekcja statystyk:
		 *   Każdy element z atrybutem data-stat="<klucz>" jest automatycznie
		 *   wypełniany odpowiednią wartością. Liczby całkowite wyświetlane są
		 *   bez miejsc dziesiętnych, pozostałe zaokrąglane do 2 miejsc.
		 *   Statystyka "Chunki RAG" jest pokazywana tylko gdy rag_chunks_used > 0,
		 *   bo przy odpowiedziach bez RAG nie ma sensu wyświetlać "0".
		 *
		 * Sekcja chunków:
		 *   Lista jest najpierw czyszczona (innerHTML = ''), następnie tworzone są
		 *   przyciski dla każdego chunka. Etykieta przycisku = source_label + sheet
		 *   (jeśli dostępne) oraz score z dokładnością 3 miejsc dziesiętnych.
		 *   Każdy przycisk ma domknięcie na swój chunk, więc kliknięcie otwiera
		 *   modal z właściwymi danymi bez tablicy globalnej.
		 *
		 * @param {Object}   data              - Odpowiedź JSON z API (AskResponse).
		 * @param {string}   data.answer       - Treść odpowiedzi modelu.
		 * @param {number}   [data.rag_chunks_used] - Liczba użytych chunków RAG.
		 * @param {Array}    [data.rag_chunks]  - Lista chunków (obiekty RagChunk).
		 */
		function renderResult(data) {
			/* answer */
			var answerRaw = (data.answer || '').trim();
			var answerEsc = answerRaw.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
			answerText.innerHTML = answerEsc.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

			/* stats */
			statValues.forEach(function (el) {
				var key = el.getAttribute('data-stat');
				var val = data[key];
				if (val === undefined || val === null) {
					el.textContent = '—';
				} else if (typeof val === 'number') {
					el.textContent = Number.isInteger(val) ? val : val.toFixed(2);
				} else {
					el.textContent = val;
				}
			});

			/* rag chunks stat — show only when RAG was used */
			if (ragStat) {
				if (data.rag_chunks_used > 0) {
					show(ragStat);
				} else {
					hide(ragStat);
				}
			}

			/* chunk buttons */
			if (chunksList) {
				chunksList.innerHTML = '';
				var chunks = data.rag_chunks || [];
				if (chunks.length > 0) {
					chunks.forEach(function (chunk, i) {
						var btn = document.createElement('button');
						btn.className  = 'bielik-widget__chunk-btn';
						btn.type       = 'button';
						btn.dataset.chunkIndex = i;

						var label = chunk.source_label || ('Chunk ' + (i + 1));
						if (chunk.sheet) label += ' / ' + chunk.sheet;

						/*
						 * Bezpieczne HTML: esc() escapuje label ze zmiennych API,
						 * score jest liczbą — nie wymaga escapowania.
						 */
						btn.innerHTML  =
							'<span>' + esc(label) + '</span>' +
							'<span class="bielik-widget__chunk-score">' +
								(typeof chunk.score === 'number' ? chunk.score.toFixed(3) : '') +
							'</span>';

						btn.addEventListener('click', function () {
							openModal(chunk, label);
						});

						chunksList.appendChild(btn);
					});
					show(chunksWrap);
				} else {
					hide(chunksWrap);
				}
			}

			show(results);
		}

		/* ── Modal ──────────────────────────────────────────────────────── */

		/**
		 * Otwiera modal z pełną treścią chunka RAG.
		 *
		 * Modal jest globalnym elementem na stronie (poza kontenerem widgetu),
		 * dzięki czemu nie jest przycinany przez overflow:hidden rodziców.
		 * Jego ID jest powiązane z konkretną instancją widgetu przez atrybut
		 * data-modal na .bielik-widget__chunks-list.
		 *
		 * Po otwarciu:
		 *   - Tytuł modala = label chunka (source_label / sheet).
		 *   - Sekcja meta = metadane: indeks w Qdrant, score podobieństwa
		 *     cosinusowego (4 miejsca dziesiętne), źródło, arkusz.
		 *     Tylko pola obecne w obiekcie chunk są wyświetlane — brakujące
		 *     są pomijane bez błędu.
		 *   - Treść chunka wyświetlana w <pre> z textContent (nie innerHTML),
		 *     co gwarantuje bezpieczne renderowanie surowego tekstu.
		 *   - document.body.style.overflow = 'hidden' blokuje scrollowanie strony
		 *     pod modalem; closeModal() przywraca tę wartość.
		 *   - Focus przenoszony na przycisk zamknięcia — wspiera nawigację
		 *     klawiaturą i czytniki ekranu (role=dialog, aria-modal=true
		 *     są ustawione w HTML widgetu).
		 *
		 * @param {Object} chunk        - Obiekt RagChunk z API.
		 * @param {number} [chunk.index]       - Numer chunka w Qdrant.
		 * @param {number} [chunk.score]       - Score podobieństwa cosinusowego.
		 * @param {string} [chunk.source_label]- Nazwa urządzenia/dokumentu.
		 * @param {string} [chunk.sheet]       - Nazwa arkusza Excel (jeśli dotyczy).
		 * @param {string} [chunk.text]        - Pełna treść fragmentu dokumentu.
		 * @param {string} title        - Etykieta do wyświetlenia w nagłówku modala.
		 */
		function openModal(chunk, title) {
			if (!modal) return;

			var titleEl    = modal.querySelector('.bielik-chunk-modal__title');
			var metaEl     = modal.querySelector('.bielik-chunk-modal__meta');
			var textEl     = modal.querySelector('.bielik-chunk-modal__text');

			if (titleEl) titleEl.textContent = title;

			if (metaEl) {
				var metaItems = [];
				if (chunk.index !== undefined) {
					metaItems.push({ label: 'Indeks', value: chunk.index });
				}
				if (chunk.score !== undefined) {
					metaItems.push({ label: 'Score', value: typeof chunk.score === 'number' ? chunk.score.toFixed(4) : chunk.score });
				}
				if (chunk.source_label) {
					metaItems.push({ label: 'Źródło', value: chunk.source_label });
				}
				if (chunk.sheet) {
					metaItems.push({ label: 'Arkusz', value: chunk.sheet });
				}
				metaEl.innerHTML = metaItems.map(function (item) {
					return '<span class="bielik-chunk-modal__meta-item">' +
						'<span class="bielik-chunk-modal__meta-label">' + esc(item.label) + ':</span>' +
						'<span>' + esc(String(item.value)) + '</span>' +
					'</span>';
				}).join('');
			}

			if (textEl) textEl.textContent = chunk.text || '';

			show(modal);
			document.body.style.overflow = 'hidden';

			var closeBtn = modal.querySelector('.bielik-chunk-modal__close');
			if (closeBtn) closeBtn.focus();
		}

		/**
		 * Zamyka modal chunka.
		 *
		 * Ukrywa element modala i przywraca scrollowanie strony.
		 * Focus nie jest ręcznie przenoszony z powrotem na przycisk chunka —
		 * przeglądarka robi to automatycznie po usunięciu elementu z widoku.
		 */
		function closeModal() {
			if (!modal) return;
			hide(modal);
			document.body.style.overflow = '';
		}

		/* ── Modal events ───────────────────────────────────────────────── */

		/*
		 * Trzy sposoby zamknięcia modala:
		 *   1. Kliknięcie tła (backdrop) — naturalny gest "kliknij obok".
		 *   2. Kliknięcie przycisku × — dla użytkowników korzystających z myszy.
		 *   3. Naciśnięcie Escape — standard dostępności (WCAG 2.1, kryterium 2.1.2).
		 * Nasłuchiwacz Escape jest na samym modalu, nie na document, żeby
		 * nie kolidować z innymi modalami na stronie (np. Elementor Lightbox).
		 */
		if (modal) {
			var backdrop = modal.querySelector('.bielik-chunk-modal__backdrop');
			var closeBtn = modal.querySelector('.bielik-chunk-modal__close');

			if (backdrop) backdrop.addEventListener('click', closeModal);
			if (closeBtn) closeBtn.addEventListener('click', closeModal);

			modal.addEventListener('keydown', function (e) {
				if (e.key === 'Escape') closeModal();
			});
		}

		/* ── Button & keyboard ──────────────────────────────────────────── */

		btn.addEventListener('click', submit);

		/*
		 * Ctrl+Enter (Windows/Linux) lub Cmd+Enter (Mac) wysyła formularz.
		 * Standardowy Enter w textarea wstawia nowy wiersz, co jest pożądane
		 * przy dłuższych pytaniach. Ctrl+Enter jest konwencją stosowaną
		 * w Slack, GitHub, ChatGPT i innych interfejsach z textarea.
		 * preventDefault() zapobiega wstawieniu znaku nowej linii po wysłaniu.
		 */
		input.addEventListener('keydown', function (e) {
			if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
				e.preventDefault();
				submit();
			}
		});
	}

	/* ── Boot ────────────────────────────────────────────────────────────── */

	/**
	 * Inicjalizuje wszystkie widgety Bielik RAG obecne w DOM.
	 *
	 * Szuka każdego elementu .bielik-widget i wywołuje initWidget() dla tych,
	 * które nie mają jeszcze ustawionego data-bielik-init.
	 * Flaga data-bielik-init chroni przed podwójną inicjalizacją, która mogłaby
	 * zarejestrować zdarzenia dwukrotnie i wysyłać podwójne żądania do API.
	 *
	 * Wywoływana w dwóch momentach:
	 *   - Przy standardowym ładowaniu strony (DOMContentLoaded lub od razu
	 *     jeśli DOM już jest gotowy).
	 *   - Z hooka Elementor (patrz niżej) — po każdym odświeżeniu widgetu
	 *     w edytorze Elementor lub po pierwszym wyrenderowaniu na frontend.
	 */
	function initAll() {
		document.querySelectorAll('.bielik-widget').forEach(function (el) {
			if (!el.dataset.bielikInit) {
				el.dataset.bielikInit = '1';
				initWidget(el);
			}
		});
	}

	/*
	 * Standardowe ładowanie strony.
	 * Jeśli skrypt ładowany jest z defer lub na końcu <body>, DOM może już być
	 * gotowy (readyState = 'interactive' lub 'complete') — wtedy initAll()
	 * wywoływana jest od razu bez czekania na zdarzenie.
	 */
	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', initAll);
	} else {
		initAll();
	}

	/*
	 * Integracja z Elementor Frontend API.
	 *
	 * Elementor po wyrenderowaniu każdego widgetu (zarówno w edytorze jak i na
	 * frontend) odpala akcję 'frontend/element_ready/{widget_name}.default'.
	 * Nazwa widgetu 'bielik_ask' pochodzi z Bielik_Ask_Widget::get_name().
	 *
	 * Dlaczego to konieczne?
	 *   - W edytorze Elementor użytkownik może przeciągać i kopiować widgety,
	 *     co tworzy nowe elementy DOM już PO pierwszym DOMContentLoaded.
	 *     Bez hooka nowe instancje widgetu nie byłyby zainicjalizowane.
	 *   - Na stronie z cache'owaniem lub lazy load inicjalizacja przez sam
	 *     DOMContentLoaded mogłaby wyprzedzić wyrenderowanie widgetu.
	 *
	 * Dwa scenariusze:
	 *   1. elementorFrontend już istnieje (skrypt Elementora załadowany przed
	 *      naszym) — rejestrujemy hook bezpośrednio.
	 *   2. elementorFrontend jeszcze nie istnieje (nasze skrypty załadowane
	 *      przed Elementorem) — słuchamy eventu 'elementor/frontend/init'
	 *      i rejestrujemy hook po jego wystąpieniu.
	 *
	 * $scope to obiekt jQuery zawierający korzeń widgetu Elementor (.elementor-widget).
	 * Wewnątrz niego szukamy .bielik-widget, który jest naszym właściwym kontenerem.
	 */
	if (window.elementorFrontend) {
		window.elementorFrontend.hooks.addAction(
			'frontend/element_ready/bielik_ask.default',
			function ($scope) {
				var el = $scope[0];
				if (el) {
					var widget = el.querySelector('.bielik-widget');
					if (widget && !widget.dataset.bielikInit) {
						widget.dataset.bielikInit = '1';
						initWidget(widget);
					}
				}
			}
		);
	} else {
		window.addEventListener('elementor/frontend/init', function () {
			if (window.elementorFrontend) {
				window.elementorFrontend.hooks.addAction(
					'frontend/element_ready/bielik_ask.default',
					function ($scope) {
						var el = $scope[0];
						if (el) {
							var widget = el.querySelector('.bielik-widget');
							if (widget && !widget.dataset.bielikInit) {
								widget.dataset.bielikInit = '1';
								initWidget(widget);
							}
						}
					}
				);
			}
		});
	}

}());

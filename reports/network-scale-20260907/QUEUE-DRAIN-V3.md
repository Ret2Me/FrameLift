# Kolejny próg treningu podczas pobierania — 8 września 2026

## Wdrożenie i zakres

Na prośbę użytkownika pozostawiono pobieranie i trening jako osobne usługi.
Etap `labels-25000` wymaga co najmniej 25 000 kwalifikujących się etykiet
**dla każdego zadania**, nie 25 000 surowych odpowiedzi API. Trener użyje całego
dostępnego zbioru z chwili zamrożenia etapu. Nie uruchamiano ponownie starego
dopasowania na niezmienionych danych ani nie obniżano progu.

Dotychczasowa kolejka rozdzielała strony między miesięczne fronty pobierania,
preferując dni z mniejszą liczbą pobranych stron. Nowe miesiące opóźniały
ukończenie wcześniej rozpoczętych dni. Trener przyjmuje tylko kompletne dni,
więc wzrost surowego archiwum nie przekładał się na wzrost kohorty treningowej.

Nowy adapter `work/operations/collect_queue_drain_v3.py` wybiera spośród
nieukończonych dni z zakończonym okresem ponowienia: najpierw największą liczbę
pobranych stron, a przy remisie wcześniejszy dzień. Nie czyta przy wyborze
etykiet, wyników modeli ani tożsamości stacji/satelity. Dzień jest ukończony
wyłącznie po rzeczywistym zakończeniu paginacji API przez oryginalny kolektor.
Po zatwierdzeniu ostatniej strony wysyła nieblokujące żądanie uruchomienia
`telemetry-yield-large-history-v2.service`. Timer co 30 minut pozostaje
zabezpieczeniem; blokada trenera i bramki progów nadal obowiązują.

Zmiana kolejności wpływa na skład pośrednich kohort. Nie jest to losowanie
reprezentatywnej populacji ani dowód poprawy trafności modelu. Nie zmieniono
definicji etykiet, rezerwacji testów, progów, parametrów modeli, limitów API,
opóźnień po błędach ani istniejących wyników. Warunkowe dekodowanie pozostaje
wskaźnikiem obecności pliku demodulacji, a nie potwierdzeniem poprawnych CRC.

## Dowody z uruchomienia

- 23 testy przeszły: nowa kolejka, nieblokujące wyzwalanie trenera, odzyskiwanie
  przerwanej zmiany protokołu oraz istniejące testy rozszerzenia i treningu v2.
- Przed wdrożeniem sprawdzono zamrożone zależności modelu i pobierania.
- Tylko kolektor tego badania został na krótko zatrzymany, aby uzyskać jego
  wyłączną blokadę. Po zapisaniu poprawki uruchomiono go ponownie. Nie usuwano
  danych i nie zmieniano innych badań na serwerze.
- O 21:26 UTC archiwum miało 214 045 unikalnych surowych obserwacji i 10
  ukończonych dni, wobec 8 przed zmianą. Dwa dni rzeczywiście się ukończyły,
  a żądania uruchomienia trenera zostały przyjęte.
- Synchronizacja o 21:26:07 UTC: 68 352 kwalifikujące się unikalne wiersze,
  30 137 etykiet sygnału i 19 594 etykiety warunkowego dekodowania. Przed zmianą
  było odpowiednio 60 227, 26 607 i 16 602.
- **W chwili tego zapisu etap 25 000 jeszcze nie trenował:** do jego bramki
  brakowało 5 406 etykiet dekodowania. Brak nowego dopasowania oznacza również
  brak nowego wyniku skuteczności. Ustawione wyzwalanie uruchomi etap, gdy
  kwalifikacja nowych, kompletnych dni dostarczy wystarczająco wiele etykiet.

## Audyt i dalsza kontrola

`protocol-before-queue-drain-v3.json` zachowuje poprzedni protokół,
`protocol-queue-drain-v3.prepared.json` umożliwia dokończenie przerwanej zmiany,
a `queue-drain-v3-amendment.json` dokumentuje kolejność i stan sprzed zmiany.
Dwa nowe skrypty i ich test są objęte sumami kontrolnymi `source_files`.
Stare pola protokołu i wpisy źródeł pozostały niezmienione; zmieniła się
tożsamość protokołu w metadanych archiwum, nie jego obserwacje ani kursory.

Aktywna konfiguracja usługi jest w
`/home/ubuntu/.config/systemd/user/telemetry-yield-network-scale.service.d/queue-drain-v3.conf`.
Kolejne zmiany zamrożonych źródeł wymagają nowego wariantu lub jawnej poprawki.

Aktualny stan należy odczytywać z:

- `progress.json` w tym katalogu: rzeczywisty przyrost i stan pobierania;
- `queue-drain-v3-trainer-wakeup.json`: ostatnie **żądanie**, nie dowód treningu;
- `../large-history-v2-20260907/cohort-progress.json`: kwalifikujące się etykiety;
- `../large-history-v2-20260907/progress.json` oraz artefakty `labels-25000/`:
  rzeczywisty start, wyniki, znacznik ukończenia i audyt nowego etapu.

Graphify pomógł zidentyfikować zależności historii i zamrożonych eksperymentów;
bieżące liczby oraz stan usług sprawdzono bezpośrednio, nie na podstawie grafu.

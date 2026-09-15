# Sprawdzenie, czy prognoza pogody poprawia przewidywanie odbioru

Stan na **10 września 2026, 19:50 UTC**: porównanie jest zaimplementowane, przetestowane, zarejestrowane i podłączone do automatycznego uruchomienia po ukończeniu kolekcji. **Nie ma jeszcze nowego wyniku skuteczności na rzeczywistych odbiorach.** Nie zastąpiono modelu działającej kampanii.

## Rzeczywisty postęp

Poprzedni etap był postępem: wdrożono trwałe limity i automatyczne wznawianie kolektora. W tym etapie pierwszy zaplanowany cykl rzeczywiście działał od 19:35 i zakończył się poprawnie **19:38:55 UTC**. Dodał **30 kompletnych partii i 493 pary lokalizacja–uruchomienie prognozy**. Łącznie mamy:

| Wielkość | Stan po audycie |
|---|---:|
| Kompletne partie | 65 / 1132 |
| Pary lokalizacja–uruchomienie GFS | 1079 / 19389 |
| Obserwacje z dopasowaną prognozą siedmiodniową | 2481 |
| Satelity w tym dopasowaniu | 37 |
| Stacje w tym dopasowaniu | 122 |
| Obserwacje z dopasowaną prognozą jednogodzinną | 0 — jeszcze nie osiągnięto tych partii |

Wszystkie 2481 dopasowań mają pięć niepustych zmiennych pogodowych. Nowy audyt ponownie sprawdził surowe odpowiedzi, parser, jednostki, sumy plików, konkretną godzinę przelotu i deklarowane warunki dostępności. Nie jest to niezależne potwierdzenie prawdy meteorologicznej. Liczby nie oznaczają nowych etykiet odbioru ani ukończenia kohorty. Ślad: `reports/single-run-weather-v1-65-batch-audit-20260910.json`, SHA-256 `d5548c030bb66f06910d552f001e646c9b27354bfb6fcd37bf729a5d75d6e772`.

Kolektor jest teraz pomiędzy cyklami, a jego timer pozostaje aktywny: następna próba **20:05 UTC**. Nie wykonano ręcznego pobierania omijającego trwałe limity. Pozostało około 81 GiB wolnego miejsca na używanym wolumenie; niczego nie usuwano z innych badań.

## Jakie metody zostaną porównane

Na tych samych obserwacjach i tym samym wyniku odbioru:

1. **Historia odbiorów** — istniejąca reguła `last10_2`, oddzielnie dla obecności sygnału i artefaktu demodulacji przy potwierdzonym sygnale.
2. **Historia bez brakującego poziomu** — wcześniej zbadany `available_last10`, jako dodatkowy punkt odniesienia, nie automatycznie promowany zwycięzca.
3. **Historia z korektą prawdopodobieństw** — uczona, regularizowana korekta wyrazu wolnego i nachylenia względem historycznego prawdopodobieństwa, bez pogody.
4. **Ta sama korekta z prognozą pogody** — temperatura, wilgotność, ciśnienie, wiatr i opad; porównanie postaci liniowej i z wyrazami kwadratowymi.

Najważniejsza różnica do zmierzenia to **metoda 4 względem 3**. Przewaga nad samą surową historią nie wystarczy, by przypisać korzyść pogodzie. Wyniki względem pozostałych prostych metod także zostaną pokazane.

Korekta jest regresją logistyczną, nie RL ani Monte Carlo. Minimalizuje regularizowany błąd logarytmiczny, a wariant wybiera według czerwcowego błędu prawdopodobieństw (Brier). Nachylenie względem historii jest nieujemne. Używa zainstalowanych NumPy 2.5.2 i SciPy 1.18.1; obsługa ograniczeń i zakończenia optymalizacji jest zgodna z [dokumentacją SciPy L-BFGS-B](https://docs.scipy.org/doc/scipy/reference/optimize.minimize-lbfgsb.html). To eksperymentalny kandydat, nie dowód, że ta rodzina jest najlepsza z możliwych.

## Rozdzielenie uczenia i oceny

Przegląd samych identyfikatorów i dat pokazał, że w tej kohorcie plan GFS obejmuje czerwiec i sierpień. Czerwcowe obserwacje przypadają na 1–14 czerwca, więc nie zaplanowano nieistniejącego zbioru z końca miesiąca.

- Wewnętrzne uczenie: czerwcowe wyniki dostępne pod jawnym założeniem `koniec obserwacji + 24 h` najpóźniej **5 czerwca, 00:00 UTC**.
- Wybór parametrów: pozostałe czerwcowe przykłady od **12 czerwca**, dla horyzontów 1 h i 7 dni. Wszystkie etykiety użyte do uczenia muszą poprzedzać chwilę wystawienia każdej ocenianej predykcji. Ta przerwa jest istotna zwłaszcza dla prognozy siedmiodniowej.
- Końcowe dopasowanie wybranego wariantu: tylko czerwiec, przed sierpniowymi chwilami wystawienia predykcji.
- Ocena: sierpień, bez dopasowania parametrów ani wyboru wariantu na jego wynikach. W prognozach kroczących wolno wykorzystać wcześniejsze sierpniowe odbiory dopiero po zakładanym opóźnieniu 24 h; nie jest to jeden plan wystawiony na cały miesiąc.

Osobno powstaną wyniki dla znanych obiektów, niewidzianych satelitów i niewidzianych stacji. Dla dwóch ostatnich wariantów wszystkie etykiety jednej z pięciu grup `ID modulo 5` są wykluczone z historii, czerwcowego uczenia, doboru parametrów i normalizacji. Każdy sierpniowy przykład zostanie oceniony raz w swojej grupie. Same metadane lokalizacji stacji mogą być dostępne bez jej etykiet — „niewidziana stacja” oznacza tu brak jej wyników odbioru w uczeniu, nie całkowity brak informacji o stacji.

Wszystkie metody zachowują te same sierpniowe identyfikatory dla danego punktu końcowego. Brak prognozy pogodowej nie usuwa trudniejszej obserwacji z porównania. Statystyki obejmą błędy prawdopodobieństw, poprawność decyzji przy progu 0,5, precyzję i wykrywalność pozytywnych odbiorów oraz wyniki przy budżecie 20% najlepiej ocenionych obserwacji. Przedziały różnic będą liczone przez wspólne losowanie dni lub całych obiektów: 2000 losowań, seed 42, minimum 10 grup. Są opisowe, bez korekty na wiele porównań.

## Brakujące parametry i pochodzenie pogody

Średnie, skale i wskaźniki braków wyznacza wyłącznie zbiór uczący. Pojedynczy brak jest obsługiwany bez pobierania informacji z przyszłości. Jeśli żaden dostępny parametr pogodowy nie występował w uczeniu, metoda pogodowa zwraca wynik odpowiedniego modelu bez pogody. Ta sama reguła obowiązuje podczas wyboru wariantu i późniejszej oceny. Standaryzowane wartości ograniczono do ±6, a nieobserwowane w uczeniu kolumny pozostają wyłączone.

Łączenie danych wymaga dokładnego identyfikatora obserwacji, satelity, stacji, horyzontu i godziny. Ciśnienie hPa jest przeliczane na kPa. Faktyczny moment pobrania pozostaje zapisany i nie jest cofany w czasie. Używamy jawnego trybu retrospektywnego z założeniem publikacji GFS 12 h po inicjalizacji i dostępności wcześniejszych metadanych stacji po 24 h. To **nie dowodzi historycznej dostępności prognozy w naszym systemie**. Szersze ograniczenia źródła są opisane w `reports/single-run-weather-v1-20260910/README.md`.

## Uruchomienie i weryfikacja implementacji

Protokół zarejestrowano **19:48:25 UTC**, przed uczeniem na połączonych rzeczywistych danych. Kod i zależności są przypięte sumami SHA-256; nie zmieniono oryginalnego planu GFS ani zamrożonego środowiska kampanii.

**93 testy przeszły w 1,79 s**, obejmując nowy moduł i wcześniejszą logikę historii oraz kolektora. Obejmują m.in.:

- uczenie zależności pogodowej i sprawdzenie jej na oddzielnych, sztucznie wygenerowanych przykładach;
- kontrolę, że zmiana sierpniowych etykiet i pogody nie zmienia dopasowanych parametrów;
- rzeczywiste wykluczenie etykiet całych satelitów z historii;
- brak wpływu danych walidacyjnych na średnie i skale;
- identyczną obsługę braków podczas doboru i oceny modelu;
- odmowę uczenia przed kompletnym audytem, po zmianie danych lub przy błędnym dopasowaniu czasu;
- sprawdzenie ukończonego wyniku bez ponownego trenowania.

Test syntetyczny sprawdza działanie kodu na celowo wprowadzonej zależności, **nie skuteczność na SatNOGS**. Strażnik uruchomienia potwierdził 30 modułów projektu wyłącznie z zamrożonego środowiska. Wcześniejszych przebiegów 90 i 92 testów nie sumujemy z końcowym przebiegiem 93.

Usługa `telemetry-yield-weather-ablation-v1` została rzeczywiście uruchomiona i zakończyła się poprawnie **19:49:43 UTC**, zwracając `awaiting_complete_archive`, `training_started=false`. Timer jest włączony, następne sprawdzenie **20:20 UTC**, potem co godzinę. Dopiero pełny audyt wszystkich 1132 partii umożliwia uczenie. Analiza ma limit 3 GiB RAM, jednego rdzenia CPU i dwóch godzin; nie korzysta z sieci. Niepełne wyniki po przerwanym procesie wymagają sprawdzenia, nie automatycznego nadpisania. Po sukcesie następne wybudzenia sprawdzają zapisane wyniki zamiast trenować ponownie. Żaden wariant nie jest automatycznie promowany.

Główne ślady:

- `work/operations/single_run_weather_ablation_v1.py` — implementacja.
- `work/single-run-weather-ablation-v1/registered-20260910/protocol.json` — protokół, SHA-256 `39cbff3f1862ed11396fa649b1a8dc71fee7f6f570ab8d4e5cf83a3ba48f2a3c`.
- `work/single-run-weather-ablation-v1/registered-20260910/design-inventory.json` — liczebności według dat i dostępności, bez wykorzystania wartości etykiet.
- `reports/single-run-weather-ablation-v1-deploy-tests-20260910.xml` i odpowiadający mu `deploy-test-guard` — testy i kontrola środowiska.
- `reports/single-run-weather-ablation-v1-20260910/tooling.sha256` — 15 plików sprawdzanych przed usługą.

Graphify pomógł odnaleźć wcześniejsze reguły rozdziału czasu, historii i porównań. Istniejące implementacje wykorzystano jako punkty odniesienia, a nowy eksperyment pozostawiono poza zamrożoną kampanią; starego grafu nie traktowano jako dowodu aktualnego stanu usługi.

## Czego ten etap jeszcze nie dowodzi

Sierpień był już wcześniej analizowany. Nawet poprawa w tym porównaniu pozostanie wynikiem rozwojowym wymagającym nowej walidacji. Test dotyczy pogody ziemskiej, nie udowadnia korzyści z pogody kosmicznej, pomiarów anteny ani prognozy miesięcznej. Etykiety są obciążone wyborem obserwacji do przeglądu; artefakt demodulacji nie jest automatycznie ramką z poprawnym CRC ani liczbą unikalnych próbek.

Pełny cel pozostaje aktywny: kolekcja jest częściowa, ilościowe dane antenowe nie zostały uzupełnione, odporność predykcji na nowe obiekty nie została wykazana, a wymagana kampania co najmniej 30-dniowa jeszcze się nie zakończyła. Uprawnienia i blokady stacji są nadal konieczne do oceny rzeczywistego sterowania odbiorami. Nie wykonano zapisu zadań w SatNOGS.

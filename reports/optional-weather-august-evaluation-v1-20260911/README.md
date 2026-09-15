# Ocena modeli pogodowych na pełnym sierpniu

## Stan

Wdrożono osobno zarejestrowany moduł oceny wcześniej wyuczonych modeli na całym sierpniu. Został przetestowany i podłączony do godzinowego harmonogramu systemowego. **Nie wykonano jeszcze rzeczywistej oceny sierpnia**, ponieważ zbieranie pogody nie jest zakończone. Nie wykonano nowego treningu ani nie zastąpiono modelu głównej kampanii.

Stan kolektora potwierdzony 11 września 2026: **420 poprawnych pakietów, 10 udokumentowanych niedostępności, 702 zapytania bez ostatecznego rozstrzygnięcia**, łącznie 1132 zaplanowane zapytania. Kolektor zakończył ostatni sprawdzony cykl poprawnie i czeka na limit API. Zapisany termin dopuszczenia kolejnej próby to **18:53:26 UTC tego dnia**. Nie oznacza to zakończenia całego pobierania o tej godzinie ani gwarancji odpowiedzi dostawcy.

To kontynuacja ukończonego treningu czerwcowego, nie nowa próba wyboru korzystniejszych ustawień. Tamten etap zapisał 44 pary modeli: kalibrację historii i historię z pogodą. W czerwcowym doborze ustawień pogoda zwiększała błąd w 40 z 44 wariantów; wszystkie modele, również niekorzystne wyniki, pozostają zachowane. Czerwcowego wyniku nie przedstawia się jako wyniku przyszłej walidacji sierpniowej.

## Co zostanie porównane

Każde z dwóch zadań — pojawienie się sygnału i artefakt demodulacji przy niezależnie potwierdzonym sygnale — ma osobną ocenę dla wyprzedzenia jednej godziny i siedmiu dni. Dla każdej pary zadanie–wyprzedzenie są trzy osie: bez wykluczania grup, wykluczone satelity i wykluczone stacje. Daje to **12 zestawień**, wykorzystujących wszystkie 44 zapisane pary modeli.

W każdym zestawieniu porównuje się cztery niezmienione metody: historię, wariant historii pomijający nieobecny poziom uśredniania, kalibrację historii i korektę pogodową. Główny wynik to sparowana różnica błędu prawdopodobieństwa między pogodą i kalibracją historii, podawana osobno dla każdego zadania, wyprzedzenia i osi. Dodatkowe wyniki obejmują decyzje przy stałym progu 0,5, precyzję wskazań przy progu 0,8, ich pokrycie oraz jednakowy budżet wyboru najwyższych 20% prognoz.

Niepewność opisuje losowanie całych grup dni lub obiektów: 2000 powtórzeń z ziarnem 42, przedział percentylowy 95% tylko przy co najmniej dziesięciu grupach. To analiza opisowa, bez korekty wielokrotnych porównań. Nie należy interpretować dwunastu zestawień ani pięciu części podziału jako niezależnych eksperymentów.

## Warunek dopuszczenia danych

Liczby zapisane przez kolektor służą jedynie do ograniczenia niepotrzebnych odczytów. Jeżeli wskazują niepełny zbiór, moduł kończy pracę przed wczytaniem sierpniowych etykiet do oceny. Sam komunikat o ukończeniu nie upoważnia jednak do liczenia wyników.

Przed oceną musi powstać nowy, kompletny zapis kontrolny. Istniejący audytor przejmuje oryginalną blokadę kolektora, odtwarza dokładnie plan całej kohorty, sprawdza surowe pakiety i udokumentowane niedostępności oraz zachowuje wszystkie pary obserwacja–wyprzedzenie. Czytnik wymaga, aby **każde pierwotne zapytanie miało rozstrzygnięcie**. Brak odpowiedzi, nieznany błąd lub trwające przenoszenie pakietu nie stają się automatycznie dowodem niedostępności prognozy.

Sprawdzane są tożsamości oryginalnego planu, źródłowego zbioru, kolejki i wszystkich czerwcowych cech. Czerwcowa część nowego eksportu musi mieć dokładnie ten sam skrót co cechy użyte w rzeczywiście zakończonym treningu. Nie można podmienić brakujących czerwcowych wartości nowymi danymi i nadal przedstawiać wcześniejszych modeli jako trenowanych na tym nowym zbiorze.

To inny, jawny warunek niż wcześniejsza bramka wymagająca 1132 poprawnych pakietów. Stary kod i protokół pozostają niezmienione. Nowy eksperyment dopuszcza udokumentowane braki, ale nie dopuszcza niedokończonego pobierania i nie zmniejsza z tego powodu populacji oceny.

## Zachowanie rozdzielenia uczenia i oceny

Moduł nie wywołuje dopasowania, wyboru parametrów ani zmiany progów. Wczytuje zapisane parametry z jednoznacznie wskazanego protokołu i potwierdzenia ukończenia treningu. Odtwarza czerwcowe panele tylko w celu sprawdzenia ich skrótów i członkostwa; następnie wylicza sierpniowe predykcje tymi samymi modelami.

Sprawdza również, czy wszystkie wyniki użyte do dopasowania były dostępne według przyjętego założenia przed terminami predykcji sierpniowych. Wykluczone grupy satelitów i stacji nie mogą mieć własnych etykiet w historii. Każda oś musi obejmować dokładnie te same, unikalne sierpniowe identyfikatory danego zadania. Zachowuje się przypadki bez pogody, a nie tylko te z pełnym zestawem cech.

Wcześniejsze sierpniowe wyniki mogą wejść do historii późniejszych sierpniowych przelotów dopiero po założonym opóźnieniu 24 godzin od końca odbioru. Jest to **odtwarzanie kroczących predykcji**, nie jednorazowe zatwierdzenie całego planu miesiąc wcześniej. Nie stanowi również dowodu rzeczywistego historycznego otrzymania danych w takich terminach.

## Wznawianie i ochrona wyników

Każde z dwunastu zestawień zapisuje prognozy, podsumowanie i potwierdzenie ich skrótów. Ponowne uruchomienie może wykorzystać już ukończone, zgodne zestawienie bez przeliczania. Częściowo zapisany katalog jest zachowywany i wymaga sprawdzenia; program nie nadpisuje go nową próbą. Zmiana protokołu, wersji danych pogodowych, zapisanych prognoz lub nieoczekiwany plik powodują odrzucenie odczytu.

Końcowe potwierdzenie obejmuje wszystkie dwanaście zestawień, podsumowanie łączne i powiązanie z pełnym eksportem pogody. Publiczny czytnik ukończonego wyniku wymaga jawnego skrótu tego potwierdzenia. Sprawdzana jest także kolejność rejestracji, powiązania danych, zapisu zestawień i ukończenia. Żaden wynik tego modułu sam nie oznacza gotowości publikacyjnej ani promocji modelu.

## Testy i rzeczywiste uruchomienie

Wstępnie przeszło 61 testów. Po dodaniu pełnego przebiegu od rejestracji do potwierdzenia ukończenia, kontroli wznowień i chronologii, **końcowo przeszły 143 testy w 23,27 s**. Obejmują kod nowej oceny oraz regresje treningu, audytu pogody i dodatkowego kontrolera modeli. Te liczby nie są miarami trafności odbioru. Kontrola importów potwierdziła 30 modułów z właściwego zamrożonego środowiska, zero spoza niego i niezmienioną tożsamość źródeł.

Testy sprawdziły zgodność predykcji z oryginalną procedurą przy zablokowanych wywołaniach uczenia, zachowanie identycznych identyfikatorów w dwunastu porównaniach, brak oceny przy niedokończonym pobieraniu, odrzucenie nieprawdziwego komunikatu o kompletności i zmienionych czerwcowych cech, działanie ponownego odczytu oraz ochronę przed nadpisaniem częściowych wyników i przejściem przez dowiązania do innych katalogów badań.

Wystąpił jeden błąd kolejności wdrożenia: pierwszą usługę uruchomiono o 02:18:36 UTC, gdy proces rejestracji jeszcze pracował. Usługa zakończyła się kodem 1 z powodu braku pliku skrótu protokołu; nie policzyła wyników i nie utworzyła zbioru oceny. Zachowano ten zapis w dzienniku. Harmonogram został wstrzymany, a rejestracja ukończyła się poprawnie. Jej rzeczywisty czas to **02:19:16.132127 UTC**.

Po potwierdzeniu zakończenia rejestracji usługę uruchomiono ponownie: **PID 244184, start 02:19:44, koniec 02:19:55 UTC, kod 0**. Zwróciła `awaiting_full_acquisition`, `august_scored=False`, `training_performed=False`, `external_requests=0`. W katalogu eksperymentu były wyłącznie protokół, jego skrót i plik blokady — bez końcowego eksportu pogody, predykcji, podsumowań czy potwierdzenia ukończenia oceny.

Harmonogram następnie przywrócono. Potwierdzono stan `enabled`, `active`, `waiting`; zapisany wtedy kolejny termin to **03:00:05 UTC**. Usługa ma niski priorytet, limit jednego rdzenia CPU i 3 GB pamięci, a rodziny gniazd ograniczono do lokalnego AF_UNIX. Nie służy do pobierania danych. Sprawdzono też zgodność bajtową zainstalowanych plików usługi i harmonogramu z ich zarejestrowanymi wersjami.

## Rejestracja i artefakty

Protokół obejmuje 33 pliki źródłowe i zależności. Jego skrót to `c83fea37679741ba4ec1c82f908621926f72550a9223ba57db235d492f8a73fa`. Wymagane wcześniejsze potwierdzenie treningu pozostaje `9d8a983ff19fda773ef5b1046c9a52c5ddc8ca7773c3a669f627269ebc7d466f`, a jego protokół `03ad2b7e1c9502bb58b770bc9f3f8c8c6c68084e17486366bda897de286d5626`.

- [Protokół oceny](/home/ubuntu/telemetry-yield/work/optional-weather-august-evaluation-v1/registered-20260911/protocol.json).
- [Implementacja](/home/ubuntu/telemetry-yield/work/operations/optional_weather_august_evaluation_v1.py) i [testy](/home/ubuntu/telemetry-yield/work/operations/test_optional_weather_august_evaluation_v1.py).
- [Końcowe 143 testy](/home/ubuntu/telemetry-yield/reports/optional-weather-august-evaluation-v1-release-20260911.xml) i [kontrola środowiska](/home/ubuntu/telemetry-yield/reports/optional-weather-august-evaluation-v1-release-20260911-runtime.json).
- [Ukończony trening czerwcowy i jego ograniczenia](/home/ubuntu/telemetry-yield/reports/optional-weather-june-prefit-v1-20260911/README.md).

Graphify skierowało kontrolę na moduły historii, kohorty, danych pogodowych i oceny. Zapytanie `weather history evaluation completeness cohort receipt` znalazło 446 węzłów, z których pokazano 39; 407 ucięto limitem. Nowy kod operacyjny sprawdzono bezpośrednio, nie opierając twierdzeń o nim na starym grafie. Nie wykonywano nowej ekstrakcji LLM; nie oznacza to zerowego kosztu całej pracy.

## Otwarte warunki publikacji

Wdrożenie harmonogramu nie kończy pobierania, oceny modelu ani miesięcznej kampanii. Nadal brakuje pełnego rozstrzygnięcia prognoz, rzeczywistych sierpniowych wyników tego eksperymentu, przyszłych odbiorów oraz spełnienia wszystkich bramek co najmniej trzydziestodniowej kampanii. Czerwiec i sierpień były wcześniej widoczne w pracach rozwojowych, więc wynik sierpniowy nie będzie świeżym niezależnym potwierdzeniem.

Założenia dostępności prognoz, metadanych i etykiet pozostają jawne. Nie powstaje tu dowód poprawności CRC, większej liczby unikalnych próbek, korzyści z ilościowych parametrów anteny czy kosmicznej pogody. Bez danych o wykonaniu planu i uprawnień operatorów kampania shadow nie staje się eksperymentem mierzącym rzeczywisty przyrost odbiorów. Cel publikacyjny pozostaje aktywny i nie jest uznany za osiągnięty.

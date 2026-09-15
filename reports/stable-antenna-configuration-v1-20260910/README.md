# Stabilna identyfikacja konfiguracji anten

## Wynik

Wdrożono i przetestowano oddzielny adapter danych stacji, który zachowuje sprawdzoną interpretację kierunku anteny po zmianie samego znacznika aktywności lub liczników. Nie podmieniono zamrożonego modelu, miesięcznej kampanii ani zakończonego porównania planów.

Rejestr pięciu wcześniej sprawdzonych konfiguracji zapisano **10 września 2026 o 21:09:02 UTC**. Porównanie lokalnych zapisów wykonano o 21:09:53, a niezależną kontrolę zakończono o 21:12:24. Porównywane odpowiedzi API zostały rzeczywiście pobrane około 13:46; nie przedstawiamy ich jako nowych pobrań o 21:09.

| Sprawdzana właściwość | Wynik |
|---|---:|
| Stacje porównane na dwóch zapisanych zestawach danych | 12 |
| Odpowiedzi, których pełna suma kontrolna się zmieniła | 4 |
| Rzeczywiste zmiany zachowanych pól konfiguracji | 0 |
| Interpretacje zachowane przez wcześniejsze porównanie pełnych odpowiedzi | 3 z 5 |
| Interpretacje zachowane przez nowy adapter | 5 z 5 |

Zmiany dotyczyły wyłącznie `last_seen` na stacjach 432, 1698, 1710 i 2029. Dwie ostatnie mają sprawdzone deklaracje anten skierowanych odpowiednio na wschód i zachód. Poprzedni adapter przestawał je rozpoznawać po zwykłym odświeżeniu czasu aktywności. Nowy adapter zachowuje te deklaracje, jednocześnie zapisując sumę pełnej aktualnej odpowiedzi i sumę starszej odpowiedzi, na której oparto interpretację. Siedem niesprawdzonych stacji pozostaje niesprawdzonych.

To wynik kontroli niezawodności wejścia, **nie wzrost trafności prognoz odbioru z 60% do 100%**. Nie uzyskano nowych etykiet odbioru ani nie dopasowywano modelu do tego porównania.

## Co zmienia implementacja

Klucz kierunku anteny pomija tylko osiem jawnie wymienionych pól: `last_seen`, `observations`, `future_observations`, `success_rate`, `is_connected`, `is_available`, `testing`, `status`. Wszystkie pozostałe pola są zachowane — także opis, współrzędne, właściciel, anteny, pasma, ograniczenia wysokości oraz każde nowe, nierozpoznane pole. Ich zmiana powoduje brak dopasowania i konieczność przeglądu. Porównanie jest celowo ostrożne; nawet redakcyjna zmiana opisu może wymagać ponownego sprawdzenia.

Pominięcie dostępności w **kluczu kierunku** nie wyłącza sprawdzania dostępności. Każda odpowiedź jest ponownie przepuszczana przez istniejący parser stacji. Brak połączenia, niedostępność, tryb testowy lub niepoprawny typ flagi blokują użycie stacji. Flagi muszą być rzeczywistymi wartościami logicznymi; napis `"false"` nie zastępuje wartości `false`.

To nie jest klucz całego harmonogramu. Historia obserwacji, zajętość, konserwacja, zadania innych użytkowników i TLE nadal wymagają osobnej aktualizacji. Pole `blockers_checked=false` jawnie przypomina, że sam adapter nie sprawdza kalendarza.

Nie dodano domyślnego zysku, temperatury szumowej, szerokości wiązki, tłumienia ani prawdopodobieństwa. Nieprecyzyjny opis stacji 2830 nadal przenosi wyłącznie deklarowaną polaryzację i ostrzeżenie o przeznaczeniu do wykrywania sygnału; nie zamienia się automatycznie w kąt 60° lub pewną porażkę dekodowania.

Rejestr powstaje wyłącznie ze zweryfikowanego wcześniejszego audytu i jego oryginalnych odpowiedzi. Sprawdzane są sumy plików, wersja środowiska i odtworzenie wcześniejszych interpretacji. Ładowanie rejestru wymaga oczekiwanej sumy kontrolnej. Rejestr można załadować raz na przebieg, a jego wewnętrzne wpisy są niezmienne; zmiana zwróconego słownika nie zanieczyszcza następnej predykcji.

Interpretacja nie jest dostępna wcześniej niż nowy rejestr i użyta odpowiedź. Nie można użyć przyszłych danych, wpisać nowej interpretacji do dawnych przelotów ani zastąpić sprawdzonej konfiguracji jeszcze starszą odpowiedzią. Wiek odpowiedzi jest jawnie raportowany. Adapter **nie ustala sam operacyjnego terminu ważności danych** — wywołujący planer musi wymagać odpowiednio świeżego odczytu i reagować na zmiany sprzętu.

## Testy i niezależna kontrola

Końcowy zestaw przeszedł **182 testy w 2,95 s**: 57 nowych testów adaptera, 14 testów niezależnego sprawdzania oraz 111 istniejących testów anten i planowania. Potwierdzono załadowanie 30 modułów wyłącznie z zamrożonego środowiska. Wcześniejsze przebiegi 158 i 168 testów są podzbiorami; nie należy ich sumować. Jedna próba rozszerzenia zestawu miała błędną nazwę pliku i nie uruchomiła testów; po poprawieniu ścieżki pełny zestaw przeszedł.

Niezależny weryfikator nie importuje adaptera ani jego funkcji identyfikacji. Osobno odtwarza zachowane pola, porównuje interpretacje z wcześniejszym audytem, kontroluje wszystkie 12 stacji, czasy i sumy odpowiedzi. Zapisuje także dokładne bajty użytych plików pamięci podręcznej, aby przyszłe odświeżenie cache nie usunęło materiału dowodowego. Kontrola dotyczy pochodzenia i zgodności danych, nie niezależnego pomiaru anten.

Nie zmieniły się sumy głównej konfiguracji (`f07c157858b85c55bb25b62df50695bd93678a8b9fa16c80b717279903c53977`) ani rejestru kampanii (`a7f5370ff6e844ef980c104d795f93c5bc468e059516ef83956d4002f7a57722`). Nie wykonano zapytań z tego adaptera do internetu ani zmian zadań w SatNOGS.

## Powiązanie z pracami nad skutecznością

Poprawka umożliwia dalsze używanie kierunków anten przy odświeżaniu danych stacji. Osobne [pełne porównanie planów](/home/ubuntu/telemetry-yield/reports/antenna-policy-replan-v1-20260910/README.md) wykazało już możliwość zastąpienia 89 wybranych okien całkowicie po odradzanej stronie nieba. Łagodniejsza reguła kosztowała 0,06856% modelowej wartości celu, a ostrożna reguła odrzucająca również okna mieszane — 1,84582%. Nie są to pomiary liczby odebranych ramek. Bieżąca poprawka nie zmienia definicji ani wyników tamtego eksperymentu.

Pobieranie archiwalnych prognoz GFS pozostaje osobnym zadaniem. Audyt po cyklu o 21:05 potwierdził **111 z 1132 paczek**, **1815 par lokalizacja–uruchomienie modelu** i zgodność 444 plików. Pełne pięć zmiennych pogodowych jest dostępne dla 1093 obserwacji przy wyprzedzeniu godzinowym i 3726 przy tygodniowym; te zbiory mogą się pokrywać. Historyczna dostępność prognoz pozostaje jawnym założeniem, a nie dowiedzionym odbiorem w przeszłości. Prognoza miesięczna nie jest dostępna w tym źródle.

O 21:08:41 wystąpił błąd połączenia. Nadzorca zapisał przerwę do 22:08:41; przy obecnym timerze pierwsza kolejna próba po tej przerwie może wypaść o 22:35 UTC, o ile pozwolą pozostałe limity. Timer pozostaje włączony. Eksperyment wpływu pogody nadal czeka na kompletny, z góry określony zbiór; nie trenuje na przypadkowym pierwszym fragmencie pobierania.

## Pliki i wykorzystanie

- [Adapter](/home/ubuntu/telemetry-yield/work/operations/stable_antenna_configuration_v1.py) i [rejestr](/home/ubuntu/telemetry-yield/work/stable-antenna-configuration-v1/registry-20260910.json).
- [Porównanie zapisów](/home/ubuntu/telemetry-yield/reports/stable-antenna-configuration-v1-20260910/snapshot-replay.json) i [niezależne potwierdzenie z zachowanymi bajtami cache](/home/ubuntu/telemetry-yield/reports/stable-antenna-configuration-v1-20260910/independent-verification.json).
- [182 testy](/home/ubuntu/telemetry-yield/reports/stable-antenna-configuration-v1-verified-tests-20260910.xml), [kontrola środowiska](/home/ubuntu/telemetry-yield/reports/stable-antenna-configuration-v1-verified-tests-20260910-runtime.json) i [audyt 111 paczek pogody](/home/ubuntu/telemetry-yield/reports/single-run-weather-v1-111-batch-audit-20260910.json).

Interfejs do dalszej integracji: `Registry.load(..., expected_sha256=..., source_identity=...)`, następnie `registry.context_from_response(response, as_of=czas_wydania_predykcji)`. Zwrócony kontekst współpracuje z istniejącym `track_context`. Nie włączono go potajemnie do wcześniej zarejestrowanego ramienia głównego kampanii.

Graphify wskazał istniejący parser `station_from_satnogs`, model `AntennaSpec` oraz kontrolę pochodzenia danych, dzięki czemu poprawka korzysta z tych samych ograniczeń dostępności i zakresów częstotliwości. Graf służył nawigacji, a nie ustalaniu bieżącego stanu usług. Użyto sześciu słów z jego słownika: `antenna`, `hardware`, `inventory`, `station`, `identity`, `source`; budżet wyjścia zapytania wynosił około 1400 tokenów i wynik był przycięty. Nie wykonywano nowej ekstrakcji LLM, więc jej koszt wyniósł zero tokenów; nie jest to pomiar kosztu całej sesji.

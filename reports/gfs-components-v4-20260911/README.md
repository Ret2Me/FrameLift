# Obsługa dwóch składników GFS: wdrożenie 11 września 2026

## Wynik

Poprawka została zaimplementowana, przeszła **177 testów** i została uruchomiona na rzeczywistym API. Dwa pierwsze procesy po wdrożeniu zakończyły się kodem 0, prawidłowo zachowując i klasyfikując kolejne odpowiedzi o niedostępności prognozy. Kolejka nie wymaga już ręcznego odblokowania dla tego rozpoznanego komunikatu.

Stan po drugim procesie, **00:15:32 UTC**: **235 kompletnych pakietów, 6 pakietów z udokumentowaną niedostępnością, 1132 pakiety w niezmienionym pełnym planie**. Pozostałe 891 nie są jeszcze ani kompletnie pozyskane, ani sklasyfikowane jako niedostępne. Dwa nowe zapytania nie przyniosły nowych wartości pogody. Nie wykonano nowego treningu ani pomiaru jakości predykcji.

Timer v4 jest włączony i sprawdza kolejkę co pięć minut. Po zakończeniu procesu `MainPID=0` oznacza oczekiwanie na kolejny termin, nie stale pracujący trening. Timer v3 wyłączono przed aktywacją poprawki; jego pliki i protokół pozostały bez zmian. Wszystkie operacje nadal korzystają z oryginalnych limitów i wspólnej blokady. Nie usunięto danych innych badań ani danych projektu.

## Przyczyna i zakres poprawki

Wersja v3 rozpoznawała tylko dokładną odpowiedź `modelRunUnavailable` dla `ncep_gfs013`. Zaobserwowana odpowiedź na niezmienione żądanie `gfs_global` wskazała także `ncep_gfs025`. Opublikowany [kontroler Open-Meteo](https://github.com/open-meteo/open-meteo/blob/main/Sources/App/Controllers/ForecastapiController.swift), odczytany 10 września 2026, wiąże `gfs_global` z grupą zawierającą GFS013 jako składnik podstawowy i GFS025 jako uzupełniający. Odpowiedzi naszego API są z tym zgodne; dokładnej rewizji produkcyjnego serwera ani trwałości braku nie ustalono.

Nowy, osobno zarejestrowany moduł tymczasowo rozszerza wyłącznie rozpoznawacz tego konkretnego błędu na dwa wymienione identyfikatory. Oryginalny rozpoznawacz jest przywracany także po wyjątku. Nie zmieniono zarejestrowanych plików v3, parsera, modelu żądania, inicjalizacji, lokalizacji, adresu API ani reguł cech. Inne błędy nadal zatrzymują kolejkę.

Każda klasyfikacja wymaga zgodności pełnego oryginalnego żądania, klucza pakietu, URL, HTTP 200, czasu inicjalizacji, skrótu surowej odpowiedzi, flag błędu i chronologii. Niedostępność jednego punktu nie jest automatycznie przypisywana innym zapytaniom z tą samą inicjalizacją. Zachowane nieudane zapytania nie są zaliczane jako pobrana pogoda ani jako nieudane odbiory satelitarne.

Wypróżnienie kolejki z brakami nadal **nie spełnia** warunku pełnego pozyskania. Zarejestrowany eksperyment wpływu pogody pozostaje zablokowany do spełnienia swojego pełnego audytu. Ewentualne badanie z jawnie brakującymi cechami wymaga osobnego protokołu, zachowania całej populacji i podania braków, a nie cichego usunięcia trudniejszych obserwacji.

## Rejestracja i aktywacja

Aneks ma skrót `0bffbf42321592924c32cb772efa32e6182ae5ff0ffef449f0ec21720f220d75`. Wiąże dokładną, wcześniejszą odpowiedź GFS025, stan zatrzymanej kolejki, wszystkie 940 plików 235 kompletnych pakietów, dziennik limitów oraz kod i testy obu warstw. Rejestracja nie zmieniła stanu ani nie wykonała HTTP.

Aktywację zakończono o **00:13:51.679217 UTC**, również bez HTTP i bez zmiany zachowanej pogody lub wcześniejszych rezerwacji limitów. Osobno zapisano potwierdzenie aktywacji. Zarchiwizowany błąd GFS025 został wtedy czwartym udokumentowanym brakiem. Nie pobierano go ponownie.

Kopia zainstalowanej usługi i timera ma dokładnie te same bajty co źródła objęte aneksem. Sprawdzenie jednostek systemd przeszło. Limity zasobów usługi: jedna jednostka CPU, 2 GB pamięci, niski priorytet procesu, maksymalnie 45 minut na cykl; limity HTTP nie zostały podniesione.

## Testy i rzeczywiste procesy

- Wstępne 56 testów: wszystkie przeszły w 14,31 s.
- Końcowe 177 testów: wszystkie przeszły w 25,04 s. To 30 nowych przypadków i 147 wcześniejszych regresji, nie dwa rozłączne zestawy wyników naukowych.
- Kontrola importów: 28 modułów z właściwego zamrożonego środowiska, zero modułów spoza niego, niezmieniona tożsamość źródeł.
- Testy obejmują oba identyfikatory, błędne czasy i skróty, inne modele i URL, integralność danych, zmiany po rejestracji, wymaganie aktywacji, nieprawidłowe potwierdzenie, wspólną blokadę i zachowanie oczekiwania na API. Test kontynuacji sprawdza rzeczywisty parser na syntetycznej odpowiedzi i dowodzi, że żądany jest kolejny pierwotny pakiet; nie mierzy skuteczności odbioru.
- PID 189570, 00:14:12–00:14:49: nowa rzeczywista odpowiedź GFS025, zachowana o 00:14:39.899061; 235 kompletnych pakietów i 5 braków po zakończeniu.
- PID 190009, 00:15:00–00:15:32: kolejne rzeczywiste zapytanie; 235 kompletnych pakietów i 6 braków po zakończeniu. `attention_required=false`, `complete=false`.

Pola `reason` i `failure_details` w stanie są odziedziczoną diagnostyką poprzedniego zatrzymania; kontroler v3 zachowuje je także po postępie. Aktualną bramkę określają `status`, `attention_required`, `complete`, czas aktualizacji i wynik konkretnego procesu. Sam pozostawiony napis `ValueError` nie oznacza nowej awarii.

## Związek z poprawą precyzji

Ten etap umożliwia dalsze gromadzenie wiarygodnej pogody, ale **nie dowodzi**, że pogoda zwiększy skuteczność modelu. Dotychczasowy rzeczywisty wynik rozwojowy pochodzi z dopasowania znaczenia historii do ocenianego wyniku: precyzja pozytywnych wskazań wzrosła z 37,62% do 70,31% na tym samym sierpniowym podzbiorze 2031 obserwacji. Metoda przeoczyła jednak więcej sukcesów i nie rozwiązała braku historii nowych stacji. Nie jest to wynik całej sieci ani potwierdzenie poprawnego dekodowania CRC.

Szczegóły, źródła, implementacja i ograniczenia są w [raporcie badawczym](/home/ubuntu/telemetry-yield/reports/conditional-target-alignment-v1-20260910/deep-research.md) oraz [wcześniejszym przeglądzie 15 źródeł](/home/ubuntu/telemetry-yield/reports/reception-revision-v1-20260910/deep-research.md). Nowego wariantu nie podmieniono w zamrożonej kampanii.

Miesięczna kampania i odrębny test nowych stacji nadal mają rozpocząć gromadzenie prognoz 13 września. Nie ma jeszcze ich przyszłych wyników. Kampania działa w trybie zapisu prognoz, nie zleca własnych odbiorów. Pomiar rzeczywistego zysku z wykonania planu wymaga stacji udostępnionych przez operatora, jego kalendarza oraz upoważnienia do zlecania obserwacji; sam gotowy importer kalendarza nie dostarcza tych informacji.

## Pliki dowodowe

- [Implementacja v4](/home/ubuntu/telemetry-yield/work/operations/gfs_components_queue_v4.py), [testy v4](/home/ubuntu/telemetry-yield/work/operations/test_gfs_components_queue_v4.py).
- [177 testów](/home/ubuntu/telemetry-yield/reports/gfs-components-v4-release-20260911.xml), [kontrola środowiska](/home/ubuntu/telemetry-yield/reports/gfs-components-v4-release-20260911-runtime.json).
- [Zarejestrowany aneks](/home/ubuntu/telemetry-yield/work/single-run-weather-v1/supervisor-v3/gfs-components-v4-amendment.json), [potwierdzenie aktywacji](/home/ubuntu/telemetry-yield/work/single-run-weather-v1/supervisor-v3/gfs-components-v4-activation.json).
- [Pierwszy zakończony cykl](/home/ubuntu/telemetry-yield/work/single-run-weather-v1/supervisor-v3/runs/20260911T001415388537Z-87edd2a9/result.json), [drugi zakończony cykl](/home/ubuntu/telemetry-yield/work/single-run-weather-v1/supervisor-v3/runs/20260911T001503544396Z-94b36692/result.json).

Raport opisuje nowy etap i zastępuje informację o braku implementacji aneksu w poprzednim, zachowanym raporcie v3. Nie zmienia historycznych dowodów ani warunków publikacyjnych.

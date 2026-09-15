# Pełna kohorta pogody i izolowanie niedostępnych przebiegów

## Wynik etapu

Zaimplementowano, przetestowano i uruchomiono osobną kolejkę pozyskiwania danych pogodowych. Jej celem jest kontynuowanie pozostałych, pierwotnie zaplanowanych zapytań, gdy konkretny przebieg modelu jest niedostępny. **Pełna kohorta pozostaje niezmieniona: 1132 pakiety i wszystkie pierwotne obserwacje.** Brakujące pakiety nadal należą do kohorty i nie są zaliczane do danych pozyskanych.

Protokół zarejestrowano 10 września 2026 o 23:47:15.068117 UTC. Jego suma to `d75af351a071634505a23e018804818869d33ab81aabb093e53c59d7024e24c0`. Rejestracja ponownie sprawdziła wszystkie 235 kompletnych pakietów objętych niezależnym audytem oraz surową odpowiedź powodującą zatrzymanie poprzedniego kolektora.

Końcowy zestaw przeszedł **147 testów w 14,31 s**: 26 dotyczących nowej kolejki, 22 procedury jednorazowego odblokowania, 23 oryginalnego kolektora i parsera oraz 76 wcześniejszych kontrolerów limitów i ponowień. Kontrola importów potwierdziła 28 modułów projektu z zamrożonego środowiska, bez zmiany jego tożsamości. Wcześniejszy zestaw 140 testów również przeszedł; nie są to dodatkowe, rozłączne 140 przypadków.

## Co dokładnie pozostało niezmienione

Kolejka wykorzystuje ten sam kod pobierania i parsowania, pełny plan, model żądania `gfs_global`, adres API, dokładny czas inicjalizacji i wszystkie oryginalne punkty lokalizacji. Nie przełącza modelu, konta, hosta ani czasu prognozy. Współdzieli z poprzednim kontrolerem **ten sam dziennik zużycia limitów i blokadę procesu**. Wcześniejsze opłaty za nieudane próby nie zostały wyzerowane.

Lokalne limity pozostają 300 jednostek na minutę, 3000 na godzinę, 7500 na dobę i 150 000 na 30 dni; minimalny odstęp to 6,1 s, a cykl ma budżet 500 lokalizacji. Są to zachowawcze lokalne oszacowania, nie odczyt rzeczywistego rozliczenia dostawcy ani pełna wiedza o ruchu innych badań z tego samego serwera.

O 23:46:49 UTC dziennik zawierał 241 rezerwacji, z czego lokalnie oszacowane zużycie wynosiło 1578 jednostek w ostatniej godzinie i 4082 w ostatniej dobie. Nie podwyższono limitów w celu obejścia niedostępności przebiegu. HTTP 200 z tekstem błędu nie staje się sukcesem pozyskania danych.

## Kolejki operacyjne i zachowanie po przerwaniu

Każdy cykl tworzy nowy, niezmienny plan operacyjny zawierający pozostałe dokładne zapytania w kolejności pierwotnego pełnego planu. Pomija w kolejce tylko pakiety już poprawnie zarchiwizowane i osobno zapisane konkretne odpowiedzi o niedostępności. Nie filtruje obserwacji po etykietach, powodzeniu odbioru, satelicie ani trudności prognozy.

Pełna populacja docelowa pozostaje w oryginalnym planie o rozmiarze 57 MB. Kolejka wiąże go skrótem i zapisuje liczbę wszystkich jego rekordów docelowych, zamiast kopiować te same rekordy w każdym cyklu. Pierwszy plan operacyjny miał około 3,30 MB. Jest to ograniczenie duplikowania metadanych, nie zmniejszenie kohorty badawczej.

Surowe odpowiedzi i ich potwierdzenia zostają w katalogu danego cyklu. Do oryginalnego archiwum trafiają wyłącznie kompletne pakiety, które ponownie przeszły niezmieniony parser i kontrole zgodności. Publikacja następuje przez przygotowany katalog i atomowe przeniesienie pod dokładny klucz pakietu. Istniejącego, odmiennego pakietu nie wolno nadpisać.

Przed nowym HTTP kontroler sprawdza i kończy publikację wcześniej kompletnie zapisanych odpowiedzi. Częściowe, ale zgodne pliki publikacji są wznawiane z tych samych bajtów. Dowiązania, zmienione plany, błędne skróty, niezgodny model lub czas oraz niepełna odpowiedź inna niż rozpoznany komunikat niedostępności zatrzymują pracę. Nie są pretekstem do pobrania nowszej, korzystniejszej odpowiedzi w miejsce zachowanej.

Wypróżnienie kolejki z pozostawionymi niedostępnymi pakietami oznacza `queue_exhausted_with_missing_runs`, **nie ukończenie pełnego pozyskiwania**. Oryginalny pełny audyt i zarejestrowany test wpływu pogody nadal wymagają pełnego archiwum. Ten wariant nie obniża ich warunków i nie rozpoczyna treningu na dogodnym prefiksie.

## Rzeczywiste uruchomienie i obecne zatrzymanie

Włączono timer `telemetry-yield-single-run-weather-v3.timer`, a poprzedni timer v2 wyłączono. Zachowano jego pliki i historię. Nowy kontroler używa stanu w `work/single-run-weather-v1/supervisor-v3`; stary plik stanu v1/v2 nie jest źródłem informacji o postępie nowej kolejki.

Pierwszy rzeczywisty proces, PID 179749, działał 23:47:49–23:48:27 UTC. Kolejny, PID 180093, działał 23:49:24–23:50:01. Wraz z poprzednio zachowanym błędem wyodrębniono trzy dokładne odpowiedzi `ncep_gfs013` dla inicjalizacji GFS **10 czerwca 2026, 18:00 UTC**. Są to wszystkie trzy zaplanowane zapytania dotyczące tej inicjalizacji; każde ma własną rzeczywistą odpowiedź i nie zostało zaliczone jako pozyskana pogoda. Liczba kompletnych pakietów nadal wynosiła 235.

Trzeci proces, PID 180335, działał 23:50:58–23:51:21 UTC. Został zatrzymany przez walidację kolejnego błędu: HTTP 200 znów zawierał `modelRunUnavailable`, ale tym razem dla **`ncep_gfs025`, inicjalizacja 11 czerwca 2026, 00:00 UTC**. Zarejestrowany rozpoznawacz v3 dopuszczał tylko wcześniej zaobserwowany `ncep_gfs013`, więc nie sklasyfikował automatycznie tej innej odpowiedzi jako znanego braku. Stan v3 to obecnie `attention_required`, z 235 kompletnymi pakietami i trzema zarejestrowanymi brakami. Nie należy mówić, że pobieranie lub trening obecnie trwa.

Odpowiedź odebrano o 23:51:20.621719 UTC. Ma 155 bajtów i sumę `27b1294a741889647640eb616809551ea94341553aa5b8a7ffd9ab67f7a6d341`. Zachowano ją wraz z identyczną definicją oryginalnego żądania i błędem parsera w katalogu `runs/20260910T235100191149Z-a1e8dc20/collection/0c14544c60abd9b506577eedb8f79aeddacc0c8796faf5b1134e4f5f9c44f836`.

W opublikowanym [kodzie kontrolera Open-Meteo](https://github.com/open-meteo/open-meteo/blob/main/Sources/App/Controllers/ForecastapiController.swift) `gfsGlobalDerivationGroup` łączy GFS013 jako podstawowy składnik i GFS025 jako uzupełniający; żądanie `gfs_global` korzysta z tej grupy. Jest to zgodne z zaobserwowaną odpowiedzią dla naszego niezmienionego żądania. Nie ustalono dokładnej rewizji kodu wdrożonej na serwerze dostawcy ani tego, czy brak przebiegu jest trwały. Źródło odczytano 10 września 2026; istotne fragmenty w wersji tekstowej to linie 1197–1201 i 1227–1233.

Następny bezpieczny krok to **osobny, jawny aneks do rozpoznawania obu zaobserwowanych składników GFS**, ze sprawdzeniem dokładnego modelu żądania, inicjalizacji, bajtów odpowiedzi i sum plików. Nie należy zmieniać zamrożonych plików v3 ani ogólnie ignorować błędów JSON. Aneks nie został jeszcze zaimplementowany ani uruchomiony w tym etapie. Oryginalne 235 pakietów, trzy rozpoznane braki i nową niesklasyfikowaną odpowiedź zachowano.

## Dowody

- [Protokół kolejki](/home/ubuntu/telemetry-yield/work/single-run-weather-v1/supervisor-v3/protocol.json), [implementacja](/home/ubuntu/telemetry-yield/work/operations/single_run_weather_queue_v3.py).
- [147 testów](/home/ubuntu/telemetry-yield/reports/single-run-weather-queue-v3-release-tests-20260910.xml), [potwierdzenie właściwego środowiska](/home/ubuntu/telemetry-yield/reports/single-run-weather-queue-v3-release-tests-20260910-runtime.json).
- [Ostatni zakończony cykl](/home/ubuntu/telemetry-yield/work/single-run-weather-v1/supervisor-v3/runs/20260910T235100191149Z-a1e8dc20/result.json), [dokładna nowa odpowiedź dostawcy](/home/ubuntu/telemetry-yield/work/single-run-weather-v1/supervisor-v3/runs/20260910T235100191149Z-a1e8dc20/collection/0c14544c60abd9b506577eedb8f79aeddacc0c8796faf5b1134e4f5f9c44f836/response.json).
- [Niezależny audyt 235 pakietów](/home/ubuntu/telemetry-yield/reports/single-run-weather-v1-235-batch-audit-20260910.json), [gotowy importer przyszłych kalendarzy operatorów](/home/ubuntu/telemetry-yield/reports/operator-calendar-intake-v1-20260910/README.md).

Nowe czynności dotyczą wiarygodnego pozyskiwania i przygotowania wykonania harmonogramu. Nie zmieniły modelu prognoz odbioru, nie dały nowego pomiaru precyzji i nie zakończyły miesięcznej walidacji. Wszystkie pierwotne wymagania publikacyjne pozostają w mocy.

# Audyt pogody na pełnej kohorcie: 11 września 2026

## Wynik i zakres

Nowy audyt odtworzył dokładnie pierwotny plan z **47 039 obserwacji, 50 satelitów i 532 stacji**. Zapisano kompletną tabelę **141 117 par obserwacja–wyprzedzenie**, po jednej dla prognozy godzinę, siedem dni i trzydzieści dni przed każdym przelotem. Obserwacje bez pogody pozostały w tabeli. To kompletność populacji, **nie twierdzenie, że cała pogoda została pobrana**.

Stan pod blokadą kolektora, zapisany w audycie o **01:09:52.228214 UTC**:

| Rozstrzygnięcie pierwotnego zapytania | Pakiety |
|---|---:|
| Poprawnie pobrane i ponownie zweryfikowane | 320 |
| Niedostępność udokumentowana odpowiedzią dostawcy | 10 |
| Bez ostatecznego rozstrzygnięcia | 802 |
| Pełny, niezmieniony plan | 1132 |

„Bez rozstrzygnięcia” nie oznacza automatycznie „nigdy nie próbowano pobrać”. Obejmuje także zapytania bez poprawnego pakietu i bez rozpoznanej odpowiedzi o niedostępności. Nieudana próba połączenia nie jest dowodem braku danych u dostawcy. Nieznane lub niespójne częściowe odpowiedzi powodują błąd audytu, a nie ciche przypisanie do dowolnej z powyższych kategorii.

W czasie tego etapu rzeczywisty kolektor zwiększył liczbę kompletnych pakietów z 235 do 320, czyli o 85. Po wykonaniu audytu pobieranie może postępować dalej; powyższe liczby opisują konkretny, niezmienny zapis kontrolny.

## Co faktycznie jest dostępne

| Wyprzedzenie | Obserwacje dopasowane do poprawnego pakietu | Obserwacje z pięcioma wartościami pogody | Cała populacja |
|---|---:|---:|---:|
| 1 godzina | 6492 | 6360 | 47 039 |
| 7 dni | 7399 | 7399 | 47 039 |
| 30 dni | 0 | 0 | 47 039 |

Liczb z różnych wyprzedzeń nie wolno sumować jako liczby unikalnych przelotów. Dla godziny 132 dopasowane obserwacje mają poprawnie zarchiwizowane rekordy, ale wszystkie pięć wartości jest pustych. Zachowano te braki. Dostępne wartości obejmują łącznie **45 satelitów i 263 stacje**; pozostałych obiektów nie usunięto z badania.

Dla 21 801 obserwacji prognozy godzinne i siedmiodniowe wypadają przed początkiem obsługiwanego archiwum. Brakuje też odpowiednio 593 i 2172 wcześniejszych, kwalifikowanych lokalizacji stacji. Dziesięć udokumentowanych niedostępnych pakietów dotyczy 366 godzinnych prognoz obserwacji. Każda przyczyna ma osobny status, zamiast wspólnej nieopisanej wartości pustej.

Prognozy na 30 dni pozostają jawnie nieobsługiwane przez ten zasób: wszystkie 47 039 takich wierszy są zachowane bez wymyślonej pogody. Nie użyto późniejszych pomiarów ani reanalizy jako zastępstwa prognozy, którą rzekomo znano miesiąc wcześniej.

Wszystkie archiwalne prognozy pobrano po historycznych terminach ich użycia. **Zero rekordów ma dowód faktycznego lokalnego przechwycenia przed historycznym terminem predykcji.** Odtwarzanie historyczne nadal opiera się na jawnych założeniach: 12 godzin dostępności publikacji GFS i 24 godziny dostępności wcześniejszych metadanych stacji. Integralność odpowiedzi i dopasowanie zapytania są sprawdzone; założenie historycznej dostępności nie staje się przez to rzeczywistym dawnym potwierdzeniem odbioru.

## Ważne rozdzielenie zbioru uczącego i oceny

W czerwcu pierwotna populacja liczy 7134 obserwacje. Wszystkie czerwcowe zapytania mają już rozstrzygnięcie w tym zapisie:

- 1 godzina: 6492 dopasowane do poprawnych pakietów, 366 z udokumentowaną niedostępnością, 276 bez wcześniejszej kwalifikowanej lokalizacji.
- 7 dni: 6064 dopasowane do poprawnych pakietów, 1070 bez wcześniejszej kwalifikowanej lokalizacji.

Sierpień liczy 18 104 obserwacje i **nie jest jeszcze gotowy do pełnej oceny pogody**. Przy godzinie 17 787 zapytań obserwacji jest nierozstrzygniętych; przy siedmiu dniach 1335 obserwacji ma już poprawnie dopasowaną prognozę, a 15 667 pozostaje nierozstrzygniętych. Pozostałe przypadki dotyczą wcześniejszej lokalizacji.

To pozwala rozważyć osobno zarejestrowane dopasowanie parametrów na całym czerwcu równolegle do dalszego pobierania sierpnia. Takiego nowego treningu **nie wykonano w tym etapie**. Nie wolno oceniać wariantów tylko na obecnie pobranym fragmencie sierpnia ani przenosić wyników wcześniejszych badań na nową analizę pogody.

## Implementacja i sprawdzenia

Audyt korzysta wyłącznie z identyfikatorów, terminów i metadanych lokalizacji przy odtwarzaniu planu. Nie używa etykiet odbioru ani istniejących kolumn pogodowych w starej bazie. Ponownie uruchamia niezmieniony planista pogody i porównuje całe definicje celów, zapytań, punktów i liczników z pierwotnym planem. Zmiana samej liczby rekordów, usunięcie trudniejszych przypadków lub podmiana późniejszej lokalizacji nie przejdą tej kontroli.

Poprawne pakiety przechodzą niezmieniony parser surowej odpowiedzi, kontrolę jednostek, skrótów, dokładnego zapytania i czasu. Odpowiedzi o niedostępności są sprawdzane przez zarejestrowane reguły v5. Sprawdzane są również zachowane plany kolejek, ich powiązania z odpowiedziami i zgodność kopii pakietów z opublikowanym archiwum. Niezakończona publikacja pakietu nie jest zamieniana na brak danych.

Eksport zachowuje osobno przyczynę braku, rzeczywisty czas pobrania i status założonej dostępności historycznej. Ciśnienie przelicza istniejący, zweryfikowany adapter z hPa na kPa. Puste wartości pozostają puste. Tabela nie zawiera etykiet sukcesu ani prognoz modelu odbioru.

Zapis pakietu audytowego kończy się niezmiennym potwierdzeniem obejmującym skróty raportu i całej tabeli. Czytnik wymaga jawnego skrótu tego potwierdzenia, sprawdza pliki wejściowe, tożsamości każdego wiersza, pełną populację i spójność rozstrzygnięć. Żądanie odczytu z warunkiem ukończenia wszystkich rozstrzygnięć zostało odrzucone na rzeczywistym obecnym eksporcie — zgodnie z oczekiwaniem, ponieważ pozostają 802 nierozstrzygnięte zapytania.

Audyt oczekiwał na oryginalną blokadę kolektora, z limitem oczekiwania do dziesięciu minut, i sprawdzał archiwum dopiero po jej przejęciu. Nie zatrzymywał procesu pobierania ani nie zmieniał jego timerów, stanu, rezerwacji API czy archiwalnych odpowiedzi. Wygenerował wyłącznie własny raport i tabelę. Brak blokady przed upływem limitu oczekiwania oznaczałby niezakończony audyt, nie awarię kolektora.

**Końcowo przeszło 299 testów w 45,60 s**: nowe przypadki audytu i oczekiwania na blokadę oraz regresje kolektora, rozpoznawania błędów, limitów i analizy pogodowej. Kontrola importów potwierdziła 30 modułów z właściwego zamrożonego środowiska, zero spoza niego i brak zmiany tożsamości źródeł. Poprzednie udane próby 49 i 293 testów zostały zachowane; nie są dodatkowymi rozłącznymi pomiarami skuteczności.

Na rzeczywistym eksporcie ponownie wykonano czytnik, sprawdzając **1706 powiązanych plików wejściowych** oraz 141 117 wierszy. Tabela zajmuje 58 436 514 bajtów. Suma potwierdzenia to `d8fa850df9fd835e443eff3a0fd4c4ed9ac1a4b914fb2a6f9670267662a826e6`.

## Czego ten wynik nie dowodzi

Nie wykonano treningu, nie zmierzono nowej dokładności, nie zastąpiono zamrożonego modelu i nie zakończono miesięcznej kampanii. Rozstrzygnięcie wszystkich zapytań z udokumentowanymi brakami będzie inną własnością niż poprawne pobranie wszystkich pakietów. Nowy audyt **nie zmienia** wcześniejszej bramki wymagającej 1132 poprawnych pakietów i nie upoważnia sam do uruchomienia analizy. Wariant wykorzystujący opcjonalną pogodę i udokumentowane braki wymaga osobnego jawnego protokołu oraz zachowania pełnego, identycznego zbioru oceny metod.

Nie jest to dowód niezależnej prawdy meteorologicznej, korzyści z pogody, poprawnej demodulacji CRC ani większej liczby unikalnych próbek z wykonanego planu. Cel publikacyjny i wszystkie jego przyszłe warunki pozostają otwarte.

## Pliki

- [Implementacja audytu](/home/ubuntu/telemetry-yield/work/operations/weather_disposition_audit_v1.py), [testy](/home/ubuntu/telemetry-yield/work/operations/test_weather_disposition_audit_v1.py).
- [Raport pełnej kohorty](/home/ubuntu/telemetry-yield/reports/weather-disposition-audit-v1-20260911/snapshot/audit.json), [tabela wszystkich obserwacji i wyprzedzeń](/home/ubuntu/telemetry-yield/reports/weather-disposition-audit-v1-20260911/snapshot/target-weather.jsonl), [potwierdzenie zapisu](/home/ubuntu/telemetry-yield/reports/weather-disposition-audit-v1-20260911/snapshot/receipt.json).
- [Ponowny odczyt i zestawienia miesięczne](/home/ubuntu/telemetry-yield/reports/weather-disposition-audit-v1-20260911/consumer-verification.json).
- [299 testów](/home/ubuntu/telemetry-yield/reports/weather-disposition-audit-v1-release-r2-20260911.xml), [weryfikacja środowiska](/home/ubuntu/telemetry-yield/reports/weather-disposition-audit-v1-release-r2-20260911-runtime.json).

Graphify skierowało kontrolę na powiązania kohorty, prognoz pogodowych i bramek walidacji. Zapytanie `weather missing cohort completeness receipt` odnalazło 232 węzły, z których pokazano 39; 193 ucięto limitem. Nowy audyt sprawdzono bezpośrednio w kodzie i na danych — nie jest jeszcze odzwierciedlony w dawnym grafie. Nie wykonywano nowej ekstrakcji LLM: zero tokenów ekstrakcji nie oznacza zerowego kosztu całej sesji.

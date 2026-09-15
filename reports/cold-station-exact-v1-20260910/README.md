# Prospektywna ocena prognoz na nieznanych stacjach

## Wynik wdrożenia

Zaimplementowano, przetestowano, zarejestrowano i włączono automatyczne porównanie predyktorów na **25 stacjach nieobecnych we wszystkich 47 039 rekordach uczących**. Zachowano te same 50 satelitów i pełny okres 13 września–14 października 2026. To osobne doświadczenie: nie zmieniono zamrożonego modelu, konfiguracji ani wcześniejszych prognoz głównej kampanii.

Protokół porównania zapisano 10 września o **23:11:18.168716 UTC**, po wcześniejszej rejestracji populacji i przed jej przyszłymi wynikami. Suma protokołu: `6842b119350bf7f7af6c4346b75e11663b9884f94dae48cfbfcd002583e880d3`. Wiąże 23 pliki źródłowe, testy i definicje usług oraz dokładne sumy modelu, całej historii, rejestracji populacji i wcześniejszego wyboru kandydata.

Usługa rzeczywiście wykonała kontrolny start 10 września o 23:14:00 UTC, PID 171485, i zakończyła się o 23:14:04 kodem 0. Poprawnie rozpoznała okres przed rozpoczęciem doświadczenia: **zero zapytań HTTP**. Timer jest włączony i oczekuje na **13 września 2026, 04:45 UTC**. Następne nominalne cykle odbywają się o 10:45, 16:45 i 22:45 UTC. Po zakończeniu okresu obserwacji pozostaje zbieranie dojrzałych ocen; timer działa do 22 października, a protokół przewiduje zamknięcie 23 października.

Pierwszy raport utworzono bez dostępu do sieci. Zawiera zero prognoz, zero wyników i **14 niespełnionych warunków jakości**. Nie jest nowym pomiarem trafności. Nie zadeklarowano gotowości publikacyjnej, potwierdzenia CRC ani przyczynowego zwiększenia liczby próbek.

## Związek z metodą wybraną w przeglądzie badań

Najbardziej obiecujący dotychczasowy wariant nie jest większą siecią neuronową. Dostosowuje historię do dokładnej definicji ocenianego wyniku: obecności artefaktu demodulacji w przelotach z niezależnie potwierdzonym sygnałem. Wykorzystuje ostatnie kwalifikowane odbiory pary satelita–stacja oraz ostrożne oszacowania z szerszej historii, kiedy danych lokalnych jest mało.

Szczegółowy [raport przeglądu i eksperymentu](/home/ubuntu/telemetry-yield/reports/conditional-target-alignment-v1-20260910/deep-research.md) opisuje literaturę dotyczącą planowania satelitarnego, modelowania łącza, selekcji etykiet i kalibracji. Wcześniejszy [szerszy przegląd 15 źródeł](/home/ubuntu/telemetry-yield/reports/reception-revision-v1-20260910/deep-research.md) obejmuje pozostałe badane kierunki. Niniejsze wdrożenie realizuje następny krok z tych raportów: zapis prognoz przed przyszłymi przelotami i oddzielną ocenę przenoszenia na nieznane stacje.

Wynik rozwojowy dla 2031 rzeczywistych, sierpniowych obserwacji pozostaje następujący:

| Miara przy progu 50% | Dotychczasowa historia łącza | Wybrana historia zgodna z celem |
|---|---:|---:|
| Poprawność odpowiedzi „tak/nie” | 62,53% | 84,15% |
| Udane wśród wskazań „tak” | 37,62% | 70,31% |
| Wykryta część wszystkich sukcesów | 82,56% | 60,04% |
| Średni błąd kwadratowy prawdopodobieństwa | 0,216711 | 0,106192 |

Są to wcześniej uzyskane liczby, **nie wyniki nowo uruchomionej kampanii**. Nowa metoda rzadziej obiecuje sukces bez pokrycia, ale przy tym progu pomija więcej sukcesów. Archiwum było wcześniej oglądane badawczo. Wynik nie dotyczy bezwarunkowej obecności sygnału, poprawności ramek CRC, dowolnego nowego satelity ani wykonania całego planu miesięcznego.

W dotychczasowym odtworzeniu z całkowicie wyłączoną historią stacji kandydat przy progu 50% nie wskazywał żadnego sukcesu. Sama poprawność 75,73% odpowiadała wówczas przewadze klasy ujemnej. To istotne ograniczenie, którego nie należy ukrywać średnią dla znanych par. Dlatego nie zastąpiono automatycznie modelu głównej kampanii nowym kandydatem.

## Jak działa nowe porównanie

Każdy cykl najpierw odczytuje rzeczywistą, kompletną zamrożoną historię i model. Faktyczny czas tego odczytu poprzedza pobranie danych przyszłych przelotów. Następnie anonimowo pobiera aktualne metadane każdej z 25 stacji, ponownie sprawdza dostępność i wyszukuje dokładne przyszłe zadania SatNOGS dla wszystkich 50 satelitów. Nie wybiera stacji ani zadań na podstawie dotychczasowej aktywności lub znanych wyników.

Geometria wynika z TLE dla dokładnego okna czasowego oryginalnego zadania. Publikowane zakresy częstotliwości anteny są wykorzystywane, a brakujące opcjonalne dane pozostają brakujące. Dwie stacje bez opublikowanego zakresu antenowego nie zostały wykluczone. Nie wymyślono zysku anteny, mocy, szumów ani pogody i nie użyto późniejszej pogody historycznej jako cechy rzekomo znanej wcześniej.

Przed każdym przelotem zapisywane są prognozy modelu i historycznych punktów odniesienia oraz oddzielny wynik nowego kandydata. Każdy kompletny zapis wiąże dokładne źródło HTTP, tożsamość obserwacji, wersję porównania i rzeczywisty czas utrwalenia. Warunkiem oceny jest co najmniej godzina wyprzedzenia wspólnego zapisu porównywanych metod.

Wybierana jest najwcześniejsza kwalifikowana prognoza danego identyfikatora, zanim sprawdzimy dostępność jego ocen. Zmiana godzin, nadajnika lub pozostałych wymaganych elementów tożsamości nie pozwala później podmienić jej na wygodniejszy przykład. Dla obu wyników kontrolowane jest zerowe wsparcie historii stacji, pary i łącza; wyniki nowych stacji nie są dopisywane do treningu w trakcie tego doświadczenia.

Oceny pobierane są po 24, 72 i 168 godzinach od końca obserwacji. Pierwsze dwa terminy są analizą pośrednią, a siedem dni stanowi wynik główny. Pierwsza poprawnie zarchiwizowana odpowiedź dla identyfikatora i etapu pozostaje wiążąca także wtedy, gdy jest pusta lub wynik jest nieznany. Brak oceny nie staje się porażką i nie jest zastępowany nowszą korzystniejszą odpowiedzią.

Raport osobno ocenia obecność sygnału i warunkowy artefakt demodulacji. Pokazuje też znane i nieznane satelity; wszystkie stacje w tej populacji mają być nieznane modelowi. Wymagania liczebności, dodatnich i ujemnych wyników, pokrycia grup i czasu nie zostały obniżone. Niepewność jest liczona przez wspólne losowanie całych dni, z dodatkowymi analizami według satelity i stacji, dopiero przy wystarczającej liczbie grup. Przedziały pozostają opisowe, bez korekty całego wcześniejszego procesu selekcji badawczej.

## Odporność i weryfikacja

Surowa treść HTTP, nagłówki, adres i czasy odbioru są zapisywane łącznie jako atomowy plik. Awaria dalszej części cyklu nie unieważnia wcześniej kompletnie zapisanych prognoz. Po przerwaniu oceny można dokończyć z tej samej zarchiwizowanej odpowiedzi bez ponownego pobierania etykiet. Rozróżniono pełne dni zbierania od samych deklaracji programu: pełny dzień musi mieć sprawdzone surowe odpowiedzi wszystkich wymaganych stacji i stron.

Końcowy zestaw przeszedł **237 testów w 161,18 s**, bez błędów i pominięć. Kontrola importów potwierdziła 37 modułów projektu z właściwego zamrożonego środowiska, bez modułów spoza niego i bez zmiany jego tożsamości. Zestaw obejmuje rzeczywisty model i pełne 47 039 wierszy historii: dla każdej z 25 zarejestrowanych stacji lokalne wsparcie pozostaje zerowe, a prognoza działa przy brakujących opcjonalnych danych RF. Ten ostatni przypadek jest testem interfejsu na zadanych cechach, a nie nowym rzeczywistym przelotem.

Pozostałe testy obejmują rzeczywistą geometrię TLE w kontrolowanych przykładach, zmianę tożsamości, duplikaty, niedojrzałe oceny, brakujące wyniki, późne zapisy, przerwane cykle, niekompletne odpowiedzi, zmianę plików po utrwaleniu i próbę obniżenia wymagań jakości. Test z kolizją nazw raportów ujawnił problem, który poprawiono przed końcowym przebiegiem i rejestracją. Dawnego nieudanego przebiegu nie usunięto.

Graphify posłużył do odnalezienia powiązań `PredictionRecord`, historii, prospektywnego zapisu i gotowości oceny. Wykorzystano istniejący graf i słowa `prospective commit receipt exact history observation station prediction`; nie przebudowano grafu ani nie wykonano nowej ekstrakcji LLM. Jej koszt wynosił zero tokenów, nie jest to koszt całej pracy. Ostateczne decyzje oparto na rzeczywistych źródłach, testach i danych.

## Pogoda: stan niezależnego toru

Niezależny audyt potwierdził **235 z 1132** kompletnych pakietów, odpowiadających 3868 parom lokalizacja–uruchomienie modelu. Dla wyprzedzenia godziny pozyskano powiązania z 4881 obserwacjami, z czego 4749 ma wszystkie pięć wartości. Dla siedmiu dni jest to 6064 obserwacji z kompletem pięciu wartości. Nadal obowiązują jawne założenia o historycznym czasie publikacji prognozy i dostępności metadanych lokalizacji. Dane te nie zostały jeszcze użyte do uruchomienia zarejestrowanego pełnego testu pogody.

O 23:02:05.925721 UTC dostawca zwrócił HTTP 200, ale zamiast JSON podał `modelRunUnavailable` dla GFS z 10 czerwca 2026 o 18:00 UTC. Odpowiedź miała 155 bajtów. To ustalona przyczyna błędu parsera; nie ustalono, czy niedostępność przebiegu jest tymczasowa. [Dokumentacja Single Runs API](https://open-meteo.com/en/docs/single-runs-api) deklaruje archiwum większości modeli od 2 kwietnia 2026, ale ta ogólna deklaracja nie potwierdza dostępności konkretnego przebiegu.

Po audycie wykonano jednorazowe odblokowanie tego błędu dostępności. Cały katalog nieudanej odpowiedzi przeniesiono do [zachowanego archiwum błędu](/home/ubuntu/telemetry-yield/work/single-run-weather-v1/supervisor-v1/unavailable-recovery-v1/e42ee9d15f2c4d30d11562de6789e9cf1bb9ece6d5f7df6f64f548f3c1b0ef2a/failed-response/response.json), bez kasowania jakichkolwiek danych. Wszystkie poprawne pakiety i zapisy naliczonych limitów zachowano i ponownie sprawdzono. Pełny plan, parser, adresy, model GFS i główna kampania pozostają niezmienione.

Ponowne pobranie jest zablokowane co najmniej do **23:32:05.925721 UTC**; przy timerze co pięć minut pierwsza nominalna możliwość to 23:35. Nie jest to dowód powodzenia ponowienia. Ta sama procedura nie odblokuje drugi raz powtórzonego błędu dostępności dla tego pakietu. Zwykłe reguły ponawiania błędów połączenia pozostają takie jak wcześniej. Nie zastosowano innego modelu, nie usunięto niewygodnej części kohorty i nie uznano braków za kompletne dane.

Procedura odblokowania przeszła osobny końcowy zestaw **98 testów w 8,19 s**, obejmujący również istniejące kontrolery limitów i ponowień. Sprawdza integralność poprawnych pakietów, zachowanie opłat za nieudaną próbę, dłuższe oczekiwanie, blokadę równoległych procesów i zachowanie dowodów przy przerwaniu. Nie modyfikowano zamrożonych źródeł kontrolera ani protokołu pełnego testu pogody.

## Dowody i granice zakończenia

- [Protokół porównania](/home/ubuntu/telemetry-yield/work/cold-station-exact-v1/protocol.json), [implementacja](/home/ubuntu/telemetry-yield/work/operations/cold_station_exact_v1.py), [atomowy zapis HTTP](/home/ubuntu/telemetry-yield/work/operations/cold_station_http_v1.py).
- [237 testów](/home/ubuntu/telemetry-yield/reports/cold-station-exact-v1-release-tests-20260910.xml), [kontrola zamrożonego środowiska](/home/ubuntu/telemetry-yield/reports/cold-station-exact-v1-release-tests-20260910-runtime.json).
- [Pierwszy pusty raport](/home/ubuntu/telemetry-yield/work/cold-station-exact-v1/reports/20260910T231239025347Z-000000/report.json), [zarejestrowana populacja i jej niezależny audyt](/home/ubuntu/telemetry-yield/reports/cold-station-cohort-v1-20260910/README.md).
- [Audyt 235 pakietów pogody](/home/ubuntu/telemetry-yield/reports/single-run-weather-v1-235-batch-audit-20260910.json), [zapis jednorazowego odblokowania](/home/ubuntu/telemetry-yield/work/single-run-weather-v1/supervisor-v1/unavailable-recovery-v1/e42ee9d15f2c4d30d11562de6789e9cf1bb9ece6d5f7df6f64f548f3c1b0ef2a/intent.json), [98 testów](/home/ubuntu/telemetry-yield/reports/recover-unavailable-weather-v1-release-tests-20260910.xml).

Wdrożenie nie jest zakończonym miesięcznym doświadczeniem. Naturalnie planowane obserwacje SatNOGS pozwolą ocenić predyktor, ale nie zmierzą przyczynowego zysku z wykonania naszego harmonogramu. Do tego nadal potrzebne są rzeczywiste kalendarze i uprawnienia operatorów oraz niezależna weryfikacja poprawnych, unikatowych danych. Brak prognozy pogody na 30 dni nie zostaje zastąpiony późniejszą wiedzą o faktycznej pogodzie. Pełna publikowalność i przewaga planera pozostają niepotwierdzone.

Ten raport aktualizuje krok „wymagany następny etap” z historycznego raportu rejestracji kohorty: kolektor jest już wdrożony. Tamtego związanego sumą kontrolną raportu celowo nie zmieniono.

# Pobieranie pogody: przerwy zależne od rzeczywistych postępów

## Wynik i wdrożenie

Zmieniono nadzorcę pobierania, aby pojedyncze, rozdzielone udanymi pobraniami błędy nie wydłużały przerwy tak jak ciągła awaria. Poprawka została zarejestrowana **10 września 2026 o 21:28:19 UTC**, przetestowana i uruchomiona jako usługa o **21:29:49–21:29:51 UTC**. Uruchomienie poprawnie zakończyło się bez HTTP, respektując trwającą przerwę do **22:08:41 UTC**.

Nowy timer `telemetry-yield-single-run-weather-v2.timer` jest włączony i sprawdza gotowość co pięć minut. Pierwsza próba po obecnej przerwie może wypaść około **22:10 UTC**, zamiast 22:35 przy poprzednim timerze. To termin najwcześniejszego dozwolonego sprawdzenia, nie gwarancja udanego pobrania. Opóźnienie timera, limity lub kolejna awaria mogą go przesunąć.

Stary timer został wyłączony. `systemctl` usunął jego dowiązania z konfiguracji użytkownika, ale oryginalne pliki jednostek i kod v1 pozostały zachowane. Nie usunięto danych badawczych. Obie wersje wykorzystują ten sam plik blokady i ten sam trwały rejestr limitów; nie utworzono nowego, pustego budżetu zapytań.

## Przyczyna zbędnego narastania przerw

Poprzednia wersja zerowała licznik błędów dopiero po całym udanym cyklu. W danych z dziennika widać:

| Cykl | Poprawne paczki na końcu | Nowe paczki | Wynik końcowy | Przerwa |
|---|---:|---:|---|---:|
| 19:35 | 65 | 30 względem wcześniejszego zbioru | Sukces | Bez awaryjnej przerwy |
| 20:05 | 89 | 24 | Błąd otwarcia połączenia | 30 minut |
| 21:05 | 111 | 22 | Błąd otwarcia połączenia | 60 minut |

Mimo 22 kompletnych nowych paczek trzeci cykl potraktowano jak drugą kolejną awarię. Przy dalszych częściowo udanych cyklach przerwa mogła narastać aż do sześciu godzin.

Oba zapisane błędy to `URLError` bez kodu HTTP, po około **46,23 s** od rezerwacji zapytania. Czas jest zgodny z okolicami ustawionego limitu 45 s, lecz stary dziennik nie zapisał wewnętrznej przyczyny. Nie można rozstrzygnąć z tych danych, czy zawiodło zestawienie połączenia, TLS, DNS lub inny etap. Nie przypisujemy tych błędów limitowi serwera bez odpowiedzi 429.

## Nowe zasady

Nowy licznik zeruje poprzednią serię błędów dopiero po znalezieniu **nowych, kompletnych i zweryfikowanych paczek**. Sprawdza zgodność z planem oraz oryginalny zapis odpowiedzi i wynik parsera. Sam plik o nazwie `receipt.json`, nagłówki 200 lub niepełny zapis nie stanowią udanego pobrania.

Jeżeli po rzeczywistym postępie wystąpi kolejny błąd przejściowy, zaczyna się od pierwszej przerwy 30-minutowej. Gdy nie ma postępu, dotychczasowe wykładnicze wydłużanie nadal działa. Błąd 429 nadal wymusza co najmniej godzinę, a dłuższy `Retry-After` pozostaje wiążący. Błędy integralności i nietymczasowe błędy HTTP nadal zatrzymują automatyczne próby i wymagają przeglądu.

Zachowano:

- wszystkie wcześniejsze rezerwacje zapytań, także nieudane;
- odstęp co najmniej 6,1 s i lokalne limity 300/min, 3000/h, 7500/dobę, 150 000/30 dni;
- limit 500 lokalizacji na cykl, zakaz dodatkowych hostów i uwierzytelnionych zapytań;
- ten sam skład kohorty, URL-e prognoz, model GFS, parser, znaczniki czasu i format zapisów;
- blokadę pojedynczego procesu oraz pełny audyt przed eksperymentem wpływu pogody.

Limity liczone są w lokalnie szacowanych jednostkach kosztu, nie w niezależnie potwierdzonych jednostkach rozliczeniowych operatora. Inny ruch do Open-Meteo na tym serwerze nie jest w pełni znany. Częstsze sprawdzanie timera jest lokalne; nie daje zgody na pominięcie przerwy ani limitów.

Nowe logi zapisują klasy zagnieżdżonych wyjątków i dostępne kody liczbowe, rozróżniając błąd otwarcia od błędu czytania odpowiedzi. Nie zapisują swobodnego tekstu błędu, adresów ani nagłówków. Następna faktyczna awaria powinna dostarczyć dokładniejszej diagnozy; nie odtworzy to brakujących szczegółów dawnych awarii.

## Testy i sprawdzenie wdrożenia

**125 testów przeszło w 16,09 s**, w tym 35 nowych testów i 90 istniejących testów nadzorcy, parsera oraz eksperymentu pogodowego. Zestaw potwierdził załadowanie 30 modułów wyłącznie z zamrożonego środowiska. Wcześniejszy przebieg 114 testów był podzbiorem, nie dodatkową populacją.

Próba integracyjna użyła niezmienionego kolektora i parsera: pierwsza zasymulowana odpowiedź została zapisana i zweryfikowana, drugie połączenie zakończyło się zasymulowanym przekroczeniem czasu. Licznik przeszedł z 3 do 1, zachowano obie rezerwacje zapytań i poprawną paczkę. To test zachowania programu, nie pomiar przepustowości zewnętrznego API.

Bezpośrednia kontrola wdrożenia o **21:31:05 UTC** potwierdziła niezmieniony stan przerwy, **114 zachowanych rezerwacji o szacowanym koszcie 1969 jednostek**, brak nowych zdarzeń HTTP po rejestracji poprawki i **111 zapisanych paczek**. Nowy timer był aktywny, a obie usługi pobierające nie miały działającego procesu. Nie pozostawiono dwóch aktywnych timerów pobierania.

Oryginalny manifest v1 przeszedł kontrolę wszystkich 12 plików. Nowy manifest obejmuje 12 plików, w tym jednostki faktycznie zainstalowane w systemd, kod, testy, rejestr zmiany i wcześniejszy audyt. Rejestr zmiany wiąże nowy kod z oryginalnym protokołem; nie nadpisano zamrożonego wykonawcy v1.

## Stan badań

Zbiór pozostaje na poziomie **111/1132 paczek**, czyli **1815 par lokalizacja–uruchomienie modelu**. Bieżąca poprawka nie pobrała nowych danych podczas obowiązującej przerwy i nie udowodniła jeszcze przyspieszenia całego pobierania w warunkach rzeczywistych. Usuwa wykazaną w kodzie przyczynę zbędnego narastania opóźnień i opóźnienie wynikające ze zbyt rzadkiego sprawdzania gotowości.

Eksperyment pogodowy o **21:20 UTC** poprawnie zwrócił `awaiting_complete_archive` i `training_started=false`. Ścieżka oczekiwanego audytu końcowego nie zmieniła się, więc po zgromadzeniu całego zbioru może ruszyć wcześniej zarejestrowane porównanie. Nadal nie uczymy na przypadkowym pierwszym fragmencie pobierania i nie zmieniamy populacji pod korzystniejszy wynik.

Sumy głównej konfiguracji, rejestru miesięcznej kampanii, planu pozyskania pogody i protokołu porównania pozostają niezmienione. Nie uzyskano nowych rezultatów odbioru, nie poprawiono deklarowanego procentu trafności i nie zakończono miesięcznej walidacji.

## Dowody

- [Nowy nadzorca](/home/ubuntu/telemetry-yield/work/operations/single_run_weather_supervisor_v2.py), [testy](/home/ubuntu/telemetry-yield/work/operations/test_single_run_weather_supervisor_v2.py), [125 zaliczonych testów](/home/ubuntu/telemetry-yield/reports/single-run-weather-supervisor-v2-release-tests-20260910.xml).
- [Zarejestrowana zmiana operacyjna](/home/ubuntu/telemetry-yield/work/single-run-weather-v1/supervisor-v1/progress-retry-v2-amendment.json), [bezpośrednia kontrola wdrożenia](/home/ubuntu/telemetry-yield/reports/single-run-weather-supervisor-v2-20260910/deployment-verification.json), [sumy kontrolne](/home/ubuntu/telemetry-yield/reports/single-run-weather-supervisor-v2-20260910/tooling.sha256).
- [Audyt aktualnych 111 paczek](/home/ubuntu/telemetry-yield/reports/single-run-weather-v1-111-batch-audit-20260910.json) i [zarejestrowane porównanie wpływu pogody](/home/ubuntu/telemetry-yield/reports/single-run-weather-ablation-v1-20260910/README.md).

Graphify wskazał istniejące mechanizmy limitowania zapytań i walidacji pogody. Aktualną przyczynę ustalono bezpośrednio w kodzie nadzorcy i jego dzienniku, nie na podstawie samego grafu. Zapytanie używało siedmiu słów z grafu: `weather`, `request`, `retry`, `backoff`, `failure`, `quota`, `receipt`; przy budżecie około 1300 tokenów pokazało 34 z 305 znalezionych węzłów. Nie przeprowadzano nowej ekstrakcji LLM, więc jej koszt wyniósł zero tokenów; nie jest to koszt całej sesji.

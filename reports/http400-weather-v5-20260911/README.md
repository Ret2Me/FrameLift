# HTTP 400 z archiwum pogody: diagnoza i poprawka v5

## Wynik etapu

Zdiagnozowano kolejne zatrzymanie kolektora i wdrożono osobną, przetestowaną obsługę dokładnej odpowiedzi HTTP 400 oznaczającej niedostępną inicjalizację GFS. **Nie zmieniono modelu odbioru, pełnej kohorty, parsera poprawnej pogody ani wcześniejszych zarejestrowanych źródeł.** Ten etap nie daje nowego wyniku precyzji.

Końcowy zestaw przeszedł **231 testów w 44,60 s**, w tym 33 nowe testy kolejki v5, 21 testów jednorazowej diagnostyki i 177 wcześniejszych regresji. Sprawdzono również pełny przepływ przez niezmieniony kolektor i rzeczywisty lokalny mechanizm limitów, z kontrolowaną odpowiedzią HTTP w teście. Zaimportowano 28 modułów z właściwego zamrożonego środowiska; żaden nie pochodził spoza niego, a tożsamość środowiska pozostała niezmieniona.

Wcześniejsze próby są zachowane: diagnostyka przeszła 198 testów; wstępne v5 przeszło 53. Pierwsza próba rozszerzonego zestawu miała 230 sukcesów i jeden błąd przygotowania nowej próbki testowej: funkcja tworząca godzinę dostała wartość 24. Naprawiono wyłącznie konstrukcję tej dodatkowej lokalizacji i powtórzono cały zestaw. Nie ukryto nieudanej próby ani nie pominięto testu.

## Co faktycznie zwróciło API

Proces v4 uruchomiony o **00:20:00 UTC**, PID 191826, zakończył się 00:20:47. W rzeczywistym zapytaniu zarezerwowano pięć jednostek lokalnego limitu o 00:20:30.094391, a serwer zwrócił HTTP 400. Stan kolejki został prawidłowo zatrzymany: `attention_required=true`, 235 kompletnych pakietów i sześć wcześniej udokumentowanych braków. Kod starszego kolektora nie zachowywał treści wyjątku HTTPError, więc pierwotnej odpowiedzi nie dało się odczytać po zakończeniu procesu.

Nie potraktowano samego kodu 400 jako dowodu niedostępnej pogody. Utworzono jednorazową, osobno testowaną procedurę diagnostyczną. Pod oryginalną blokadą i z tym samym dziennikiem limitów wykonała **jedno nowe zapytanie do dokładnie tego samego adresu**, bez odblokowania kolejki. Nie wolno uruchomić jej ponownie dla tej samej rezerwacji pod innym katalogiem.

O **00:28:29.698555 UTC** odebrano nową odpowiedź HTTP 400, 110 bajtów. Jej treść mówiła o niedostępności `ncep_gfs025` dla `2026-06-11T00:00Z`. Suma surowych bajtów to `bf6954ac36f22e58183ed006b25553774e47a42d96ed7b3bb5d63f1c78fa9e51`. Odpowiedź zachowano jako [response.bin](/home/ubuntu/telemetry-yield/work/single-run-weather-v1/supervisor-v3/http400-diagnostics/73acec4a6ef04f1bb0656c3027feb0ae/response.bin), razem z intencją zapytania i potwierdzeniem rzeczywistego czasu odczytu. **Nie przypisano jej wstecznie do godziny pierwotnej awarii.** Obie próby pozostały rozliczone w dzienniku limitów.

Mamy zatem dowód dwóch formatów błędu dla naszego niezmienionego żądania `gfs_global`: tekstowy błąd wewnątrz HTTP 200 oraz obiekt JSON `error=true` w HTTP 400. Nie ustalono, czy niedostępność tych inicjalizacji jest trwała ani dlaczego serwer wybiera konkretny format.

## Zachowanie v5

Nowa warstwa zapisuje pełną odpowiedź HTTP 400 przed jej klasyfikacją. Warunkiem uznania jej za udokumentowany brak jest dokładny komunikat, jeden z dwóch zaobserwowanych składników GFS, zgodna inicjalizacja, oryginalne żądanie, URL, skrót treści, chronologia i komplet plików. Odrzucane są dodatkowe lub powtórzone klucze JSON, inny model, inny termin, nieprawidłowe flagi, zmienione bajty oraz dowiązania zamiast zwykłych plików.

Inny HTTP 400 nadal wymaga interwencji, ale jego treść jest już zachowana. Odpowiedź z `Retry-After` nie jest automatycznie pomijana przez nową regułę. Inne błędy HTTP, w tym 429 i 503, przechodzą przez dotychczasową obsługę oczekiwania i ponowień. Przekroczenie limitu wielkości odpowiedzi lub błąd przechwycenia treści zatrzymuje operację; nie powoduje niekontrolowanej pętli żądań.

Pakiet niedostępnej prognozy **nigdy nie otrzymuje potwierdzenia poprawnie pobranej pogody** i nie staje się negatywną etykietą odbioru satelitarnego. Kolejka może przejść do kolejnego oryginalnego zapytania, ale nadal zachowuje wszystkie 1132 pakiety i wszystkie 47 039 obserwacji w pełnym planie. Wyczerpanie kolejki z brakami nie spełnia bramki pełnego pozyskania ani nie uruchamia zarejestrowanego treningu pogodowego.

Do nowej ewidencji braków włączono zachowaną odpowiedź diagnostyczną. Powstała jej jawnie powiązana kopia w formacie pakietu błędu, z tymi samymi surowymi bajtami i rzeczywistym czasem 00:28:29. Nie wykonano trzeciego zapytania dla tego pakietu.

## Wdrożenie i jego granice

Protokół v5 ma skrót `39787e2b3adb6d198de1d6643e1e4df25c362f8c0952b1a50a761898dfcd5557`. Wiąże kod, testy, jednostki uruchomieniowe, wcześniejszy aneks v4, diagnostykę, stan przed aktywacją, zachowane dane i dziennik limitów. Rejestracja i aktywacja nie wykonały HTTP.

Aktywację zakończono o **00:41:14.215827 UTC**: 235 kompletnych pakietów i siedem udokumentowanych braków. Usługa v5 została uruchomiona o **00:41:28 UTC**, PID 199481. Zainstalowane jednostki mają dokładnie te same bajty co źródła objęte protokołem i przeszły sprawdzenie systemd. Timer v4 wyłączono przed aktywacją; timer v5 sprawdza kolejkę co pięć minut. Limity API i zasobów pozostały niezmienione.

Pierwszy proces zakończył się o **00:42:14 UTC** z kodem 0. Kolejne oryginalne zapytanie, dla inicjalizacji **11 czerwca, 06:00 UTC**, otrzymało o 00:42:00.417764 HTTP 200 z tekstowym błędem GFS013. Nowa warstwa zachowała zgodność z wcześniejszą obsługą tego formatu. Wynik cyklu to **235 kompletnych pakietów i osiem udokumentowanych braków**, `attention_required=false`, `complete=false`. Nowych wartości pogody nadal nie uzyskano; 889 pakietów pozostawało bez rozstrzygnięcia. Najbliższy termin timera odczytany o 00:43:47 to 00:45:00 UTC.

To rzeczywisty test kontynuacji kolejki i zgodności z błędem HTTP 200. Obsługę nowego HTTP 400 zweryfikowano na rzeczywistej zachowanej diagnostyce oraz w pełnym kontrolowanym teście kolektora; pierwszy proces v5 nie otrzymał jeszcze nowego HTTP 400. Nie wolno na podstawie aktywnego timera twierdzić, że trwa trening albo że wzrosła dokładność.

Drugi rzeczywisty proces v5, PID 200375, działał **00:45:00–00:45:38 UTC** i również zakończył się kodem 0. Stan po nim: **235 kompletnych pakietów i dziewięć udokumentowanych braków**, bez blokady wymagającej interwencji i bez uznania całego pozyskiwania za zakończone. Nadal nie przybyło poprawnych wartości pogody. Próba końcowej weryfikacji trafiła wcześniej na zajętą blokadę tego procesu i zakończyła się bez zapisu raportu; kontrolę ponowiono dopiero po potwierdzonym zakończeniu procesu, nie uruchamiano przez to ponownie pobierania.

Zapowiedziane rozszerzenie audytu brakujących cech odłożono po pojawieniu się rzeczywistego błędu kolektora. Nie twierdzimy, że taki nowy audyt lub osobny eksperyment dopuszczający udokumentowane braki został już zaimplementowany. To pozostaje kolejnym krokiem po zabezpieczeniu pozyskiwania danych. Oryginalna pełna bramka 1132 poprawnych pakietów jest nadal obowiązująca dla wcześniej zarejestrowanego eksperymentu.

Główna kampania, jej konfiguracja i księga prognoz pozostały bez zmian. Nadal brakuje przyszłych wyników miesięcznej walidacji oraz udostępnionych przez operatora stacji i kalendarzy do pomiaru rzeczywistego zysku z wykonania planu. Cel publikacyjny pozostaje niezakończony.

## Dowody

- [Kod v5](/home/ubuntu/telemetry-yield/work/operations/http400_weather_queue_v5.py), [testy v5](/home/ubuntu/telemetry-yield/work/operations/test_http400_weather_queue_v5.py).
- [Jednorazowa diagnostyka](/home/ubuntu/telemetry-yield/work/operations/diagnose_weather_http400_v1.py), [jej wynik na rzeczywistym API](/home/ubuntu/telemetry-yield/work/single-run-weather-v1/supervisor-v3/http400-diagnostics/73acec4a6ef04f1bb0656c3027feb0ae/result.json).
- [231 testów — końcowa próba](/home/ubuntu/telemetry-yield/reports/http400-queue-v5-release-r2-20260911.xml), [potwierdzenie właściwego środowiska](/home/ubuntu/telemetry-yield/reports/http400-queue-v5-release-r2-20260911-runtime.json).
- [Protokół](/home/ubuntu/telemetry-yield/work/single-run-weather-v1/supervisor-v3/http400-v5/protocol.json), [aktywacja](/home/ubuntu/telemetry-yield/work/single-run-weather-v1/supervisor-v3/http400-v5/activation.json).
- [Pierwszy zakończony cykl v5](/home/ubuntu/telemetry-yield/work/single-run-weather-v1/supervisor-v3/runs/20260911T004133404246Z-13422078/result.json).

Graphify wykorzystano do wyboru miejsc kontroli powiązań kohorty, pogody i bramek walidacji. Zapytanie `weather missing acquisition cohort availability ablation` znalazło 303 węzły; pokazano 42, a 261 ucięto limitem. Nowe moduły operacyjne sprawdzono bezpośrednio — istniejący graf nie jest dowodem ich aktualnej implementacji. Nie wykonywano nowej ekstrakcji LLM; koszt ekstrakcji wynosił zero tokenów, co nie oznacza zerowego kosztu całej sesji.

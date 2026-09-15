# Naprawa pobierania pogody — 12 września 2026

## Przyczyna i poprawka

O 01:14 UTC dostawca zakończył odpowiedź HTTP 200 tekstem
`Unexpected error while streaming data: timeoutReached`, zamiast JSON-em pogody.
Parser prawidłowo odrzucił tę odpowiedź. Następnie odzyskiwanie kolejki próbowało
sklasyfikować ją jako trwały brak modelu; nierozpoznany komunikat zatrzymał
kolektor z ogólnym `ValueError`. Działający harmonogram nie oznaczał pobierania.

Nowy moduł rozpoznaje wyłącznie ten dokładny komunikat, sprawdza tożsamość
zapytania i zapisane bajty, zachowuje cały błędny pakiet z potwierdzeniem
integralności i przekazuje błąd do istniejącego mechanizmu opóźnionych ponowień.
Ponawiane jest to samo zapytanie. Timeout nie staje się ani pobraną pogodą,
ani trwałą niedostępnością. Inne błędy parsowania nadal wymagają inspekcji.
Stan wymagający interwencji powoduje teraz również kod zakończenia usługi 2.

Nie zmieniono parsera, pełnego planu 1132 zapytań, limitów i dziennika rezerwacji,
wcześniejszych modeli ani kodu oceny sierpnia. Zarejestrowane wcześniejsze pliki
pozostały niezmienione. Usługa korzysta z poprawki przez osobny plik konfiguracji
uzupełniającej (`10-stream-timeout-v6.conf`), zachowując swój dotychczasowy timer.

## Weryfikacja

- Pierwsza seria: 81 testów przeszło.
- Końcowa seria: **158 testów przeszło**, bez błędów i pominięć; 62,86 s.
- Zaimportowano 30 modułów właściwego zamrożonego środowiska, zero spoza niego.
- Sprawdzono prawdziwą 53-bajtową błędną odpowiedź oraz pełny przebieg parsera
  i pobierania na kontrolowanych odpowiedziach HTTP: timeout, opóźnienie,
  ponowienie tego samego zapytania i poprawna odpowiedź.
- Sprawdzono odrzucanie zmienionych danych, nieznanych błędów, niezgodnych
  tożsamości i dowiązań; także wznowienie zachowania pakietu po przerwanym zapisie.
- Przed aktywacją sprawdzono 2904 pliki istniejących 726 kompletnych pakietów,
  1469 zapisów dziennika limitów, 33 źródła zarejestrowanej oceny sierpnia
  i 46 plików wynikowych treningu czerwcowego. Pozostały niezmienione.

Stan 721 był nieaktualny: pięć dodatkowych pakietów było już przeniesionych do
archiwum, a pięć następnych pozostawało w przerwanym cyklu. Po odzyskaniu i pełnej
kontroli licznik wyniósł **731 kompletnych, 10 niedostępnych, 391 nierozstrzygniętych**.
Nie były to jeszcze nowe pobrania wykonane przez poprawioną usługę.

Aktywacja zakończyła się o **15:37:37 UTC**, bez zapytań HTTP. Pierwotny błędny
pakiet przeniesiono w całości do `stream-timeout-v6/timeouts/…/failed-response`;
niczego nie usunięto. Protokół naprawy:
`872760e711b544cab2db046a3bddaa5447d3e33be2d762f10088c2235526669a`.
Rzeczywistą usługę uruchomiono o **15:37:57 UTC**, PID 4022948; timer przywrócono.

O **15:40:21 UTC** kolektor poprawnie pobrał dokładnie ten sam pakiet, który
wcześniej zatrzymał kolejkę (klucz `942ba7f4…`). O **15:41:21 UTC** odczytano
i ponownie sprawdzono niezmienionym czytnikiem **10 nowych kompletnych pakietów**
z rzeczywistymi odpowiedziami API, w tym ten wcześniej nieudany. Ich zapytania
zgadzały się z planem, a czasy pobrania następowały po aktywacji poprawki.
Są zapisane w kolekcji bieżącego cyklu; globalny licznik jest aktualizowany
dopiero po jego rozliczeniu. Proces nadal pracował podczas tego sprawdzenia.
To potwierdzenie rzeczywistego wznowienia, nie tylko aktywnego harmonogramu.

[Zapis kontroli plików, testów i nowych odpowiedzi](/home/ubuntu/telemetry-yield/reports/stream-timeout-weather-v6-20260912/verification.json).

## Artefakty i ograniczenia

- [Implementacja](/home/ubuntu/telemetry-yield/work/operations/stream_timeout_weather_v6.py).
- [Testy](/home/ubuntu/telemetry-yield/work/operations/test_stream_timeout_weather_v6.py).
- [Wynik 158 testów](/home/ubuntu/telemetry-yield/reports/stream-timeout-weather-v6-release-20260912.xml).
- [Protokół naprawy](/home/ubuntu/telemetry-yield/work/single-run-weather-v1/supervisor-v3/stream-timeout-v6/protocol.json).

To naprawa operacyjna, nie nowy wynik skuteczności modelu. Pełna ocena sierpnia
nadal wymaga rozstrzygnięcia wszystkich pierwotnych zapytań.

Graphify: zapytanie `weather parse response recovery receipt validate` zwróciło
635 węzłów, pokazano 43, 592 ucięto limitem. Stary graf nie obejmował tego kodu
operacyjnego; przyczynę i poprawkę sprawdzono bezpośrednio w kodzie i zachowanej
odpowiedzi. Nie wykonywano nowej ekstrakcji LLM; nie jest to twierdzenie o zerowym
koszcie całej sesji.

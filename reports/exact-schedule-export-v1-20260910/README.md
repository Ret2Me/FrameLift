# Zgodność ocenionego planu z eksportem do SatNOGS

## Wynik

Na rzeczywistym miesięcznym planie obejmującym **12 208 odbiorów, 50 satelitów i 12 stacji** stary format eksportu zmienia co najmniej jedną granicę każdego okna. Przyczyną jest obcięcie ułamków sekund. Nowy, osobny eksport zachowuje wszystkie granice bez zmian. Nie wykazano, że usunięcie tych rozbieżności zwiększa trafność prognoz lub liczbę odebranych ramek.

Plan został utrwalony 10 września 2026 przed okresem wykonania 13 września–14 października. Audyt zakończył się 10 września o 15:00:37 UTC. Sprawdzono łańcuch sum kontrolnych rejestru, zgodność pliku z zapisem zatwierdzenia planu oraz niezmienność zamrożonego środowiska i wejść kampanii.

| Własność | Wynik lokalnego audytu |
|---|---:|
| Liczba odbiorów | 12 208 |
| Zmieniony początek w starym formacie | 12 206 |
| Zmieniony koniec w starym formacie | 12 205 |
| Zmieniona dowolna granica w starym formacie | 12 208 |
| Największe przesunięcie granicy wstecz | 0,984375 s |
| Największa bezwzględna zmiana długości | 0,968750 s |
| Okna o niepoprawnej długości po starym formatowaniu | 0 |
| Zmieniona granica w nowym eksporcie | 0 |
| Czas budowy nowego eksportu na tej maszynie | 2,217 s |
| Zlecenia wysłane do SatNOGS | 0 |

Małe przesunięcie nie oznacza automatycznie zmiany wyniku odbioru. Jest jednak realną różnicą między przedziałem ocenionym przez model a przedziałem opisanym w żądaniu. Zachowanie tożsamości przedziału usuwa tę niejednoznaczność. Osobnym, większym problemem pozostaje przypisywanie wyników naturalnych obserwacji do tylko częściowo pokrywających się okien planu; tego mechanizmu w zamrożonej kampanii nie podmieniono.

## Implementacja i testy

[Nowy moduł](/home/ubuntu/telemetry-yield/work/operations/exact_schedule_export_v1.py) tworzy lokalny dokument w schemacie `satnogs-network-exact-schedule-export-v2`. Nie ma transportu HTTP, dostępu do tokenu ani funkcji wysyłania zleceń. Dotychczasowy klient odrzuca ten nowy schemat, co sprawdzono testem. Nie jest to wdrożenie nowego nadawcy zleceń do działającej kampanii.

Czasy są normalizowane do UTC bez utraty mikrosekund. Częstotliwość nie jest po cichu zaokrąglana; wartość niecałkowita wymaga jawnej decyzji i ponownego przygotowania planu. Sprawdzane są identyfikatory, zakresy prawdopodobieństw, powiązanie z konkretną wersją planu i brak nakładania się odbiorów tej samej stacji. Brak opcjonalnej częstotliwości pozostaje brakiem, a nie wymyśloną wartością.

Osobna funkcja wiąże dostarczone bajty odpowiedzi z dokładnym oknem, stacją, satelitą, nadajnikiem i żądaną częstotliwością. Sama zgodność liczby zwróconych ID nie wystarcza. Niepoprawna lub niejednoznaczna odpowiedź wymaga sprawdzenia stanu serwera, a nie automatycznego ponowienia zlecenia. Zmiana TLE przez serwer zostaje wykryta i oznaczona jako wymagająca nowej prognozy przed przelotem. Potwierdzenie utworzenia zlecenia nie jest potwierdzeniem wykonania odbioru.

Zestaw nowych testów liczy **39 zaliczeń**. Łącznie z istniejącymi testami planowania i klienta zleceń uzyskano **71 zaliczeń w 0,90 s**. Kontrola importów potwierdziła 34 moduły projektu pochodzące wyłącznie z zamrożonego środowiska; jego tożsamość nie uległa zmianie. Wcześniejszy przebieg 37 testów i późniejszy przebieg 39 testów zachowano; nie należy sumować tych powtarzanych uruchomień jako różnych testów.

[Skrypt pełnego audytu](/home/ubuntu/telemetry-yield/work/operations/audit_exact_schedule_export_v1.py) porównuje nowy eksport z rzeczywistą funkcją formatowania starego adaptera. Nie uruchamia całego starego eksportera na 12 tysiącach pozycji, ponieważ ten wielokrotnie przelicza sumę kontrolną całego planu. Nowy eksporter oblicza ją raz. Podany czas jest pomiarem nowej implementacji, nie kontrolowanym pomiarem przyspieszenia względem starej.

Dla wszystkich 12 208 pozycji przeprowadzono także **syntetyczne** potwierdzenie zwrotne z TLE zachowanym w planie i sztucznymi ID. Sprawdza ono skalę oraz logikę wiązania odpowiedzi, nie zachowanie serwera. Pliki mają jawny prefiks `synthetic`; nie są zapisywane jako rzeczywiste obserwacje ani etykiety kampanii.

## Kontrakt API i ograniczenia

W oficjalnym kodzie SatNOGS Network 1.132 pola wejściowe `NewObservationSerializer` dopuszczają zapis sekund z częścią ułamkową i bez niej. Metoda `to_representation` tej klasy obcina ułamki, ale dotyczy reprezentacji wyjściowej. W sprawdzonej ścieżce tworzenia obserwacji odpowiedź jest budowana za pomocą innej klasy, `ObservationSerializer`. Nie wolno utożsamiać tego obcięcia z regułą parsowania żądania.[^1][^2]

`NewObservationListSerializer` przekazuje sparsowane czasy do `create_new_observation`, a ta funkcja przekazuje `start` i `end` do modelu obserwacji bez jawnego zaokrąglania. Jednocześnie sprawdza już istniejące zlecenia, widoczność i zakres częstotliwości stacji oraz korzysta z własnego zestawu TLE.[^1][^3]

To analiza oznaczonej wersji źródeł, nie test zapisu do produkcyjnej bazy, odczytu z działającego API ani dokładności sprzętowego uruchomienia. Nie sprawdzono przez POST, czy serwer przyjmie cały miesięczny harmonogram, jakie ograniczenia czasowe i uprawnienia zastosuje ani czy wszystkie stacje będą dostępne. Nie ma potwierdzenia, że operator kontroluje 12 stacji zawartych w tym planie. Eksport pozostaje materiałem lokalnym.

Przed rzeczywistym wykonaniem potrzebne są: wskazana i autoryzowana stacja, aktualne blokery i istniejące zlecenia, kontrola bieżącego kontraktu API oraz bezpieczna obsługa potwierdzeń. Jeśli wykonawca wymaga całych sekund, trzeba przed oceną modelu wyznaczyć takie okna i ponownie sprawdzić geometrię oraz ograniczenia. Nie wystarczy zaokrąglić czasy na końcu i zachować wcześniejsze prognozy.

## Znaczenie dla badań nad precyzją

[Raport przeglądu metod](/home/ubuntu/telemetry-yield/reports/reception-revision-v1-20260910/deep-research.md) i [uzupełnienie literatury oraz pilotaż](/home/ubuntu/telemetry-yield/reports/exact-job-v1-20260910/research-update.md) pozostają podstawą wniosków predykcyjnych. Testowane warianty adaptacyjnej historii nie wykazały jeszcze jednoznacznej przewagi nad najmocniejszą wcześniejszą metodą. Nie zmieniono zarejestrowanego modelu na podstawie drobnej poprawy w oglądanym wcześniej panelu rozwojowym.

Obecny audyt podnosi zgodność techniczną i wiarygodność późniejszego pomiaru. Nie dostarcza nowego procentu poprawności i nie stanowi dowodu zwiększenia miesięcznego uzysku. Główna kampania nadal działa w trybie obserwacyjnym, bez wysyłania zleceń. Osobny pilotaż dwóch dokładnych przyszłych ID ma zaplanowaną ocenę na 11 września o 22:30 UTC; jego mała liczebność służy sprawdzeniu integracji, nie dowodzeniu przewagi naukowej.

## Materiały kontrolne

- [Pełny wynik audytu](/home/ubuntu/telemetry-yield/reports/exact-schedule-export-v1-20260910/audit.json) zawiera sumy kontrolne planu, kodu, wejść kampanii i wygenerowanych plików.
- [Eksport lokalny](/home/ubuntu/telemetry-yield/reports/exact-schedule-export-v1-20260910/monthly-dry-export.json) nie został wysłany do sieci.
- [Testy regresji i kontrola importów](/home/ubuntu/telemetry-yield/reports/exact-schedule-export-v1-20260910/test-import-guard-regression.json), [wynik JUnit](/home/ubuntu/telemetry-yield/reports/exact-schedule-export-v1-regression-tests-20260910.xml).

## Źródła

[^1]: Libre Space Foundation, [SatNOGS Network: serializers.py, wersja 1.132](https://gitlab.com/librespacefoundation/satnogs/satnogs-network/-/blob/1.132/network/api/serializers.py), klasy ObservationSerializer, NewObservationListSerializer i NewObservationSerializer. Kopia pobrana 10 września 2026: `satnogs-serializers-1.132.py.txt` w katalogu raportu.
[^2]: Libre Space Foundation, [SatNOGS Network: views.py, wersja 1.132](https://gitlab.com/librespacefoundation/satnogs/satnogs-network/-/blob/1.132/network/api/views.py), ObservationView.create. Kopia: `/home/ubuntu/telemetry-yield/reports/exact-job-v1-20260910/primary-api-sources/views-1.132.py.txt`.
[^3]: Libre Space Foundation, [SatNOGS Network: scheduling.py, wersja 1.132](https://gitlab.com/librespacefoundation/satnogs/satnogs-network/-/blob/1.132/network/base/scheduling.py), create_new_observation. Kopia pobrana 10 września 2026: `satnogs-scheduling-1.132.py.txt` w katalogu raportu.

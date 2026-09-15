# Kontrola zgodności okien w ocenie prospektywnej

## Decyzja przed rozpoczęciem kampanii

W ocenie trafności prognozy dla konkretnego okna wyniki pochodzące z innego okna nie są traktowane jako bezpośrednie etykiety tego przelotu. Nawet duże nakładanie się przedziałów nie dowodzi, że binarny wynik całej obserwacji odnosi się do zaplanowanego wycinka. Rozróżnienie obowiązuje przed startem kampanii 13 września 2026 i przed uzyskaniem jej pierwszych wyników.

Dodano osobną, obowiązkową przy interpretowaniu wyników kontrolę. Zachowano zarejestrowany plan eksperymentu, model, rejestr oraz dotychczasowe raporty. Nowy raport nie przelicza ani nie nadpisuje ich liczników; pokazuje ocenę dokładnie dopasowanego podzbioru obok dotychczasowego wyniku opartego na nakładaniu się okien.

Kod zamrożonego `readiness.py` weryfikuje źródło etykiety, stację, satelitę, nadajnik i koniec rzeczywistej obserwacji. Nie wymaga jednak, aby obie granice rzeczywistego okna były identyczne z granicami prognozowanego okna. Wcześniejszy audyt przedziałów ujawnia tę różnicę, lecz sam w sobie nie dodaje jej do bramek zaliczenia. Nowa kontrola zamyka tę lukę w interpretacji gotowości.

## Wymagania nowej kontroli

Oceniany podzbiór obejmuje wyłącznie odbiory o identycznym początku i końcu w UTC, z prognozą zapisaną przed oboma początkami. Oba końce muszą już minąć, okno musi mieścić się w zarejestrowanej kampanii, a stacja i satelita należeć do jej listy. Etykieta nieznana nie jest porażką; warunkowy wynik demodulacji wymaga potwierdzonego sygnału.

Zachowano liczebności wynikające z konfiguracji: co najmniej 300 znanych wyników sygnału, 100 warunkowych wyników demodulacji, 20 satelitów i pięć stacji w ocenionej próbie sygnału. Wymagane są też obie klasy: co najmniej 30 obecności i 30 braków sygnału oraz 15 pozytywnych i 15 negatywnych warunkowych wyników demodulacji. Różnorodność i liczebność są liczone od nowa dla dokładnie dopasowanego podzbioru, nie przejmowane z szerszego zbioru częściowych dopasowań.

Zaliczenie wymaga ponadto dotychczasowych bramek kampanii i technicznego audytu, a niezależnie sprawdzany jest rzeczywisty koniec kampanii. Kontrola nie skraca wymaganych 30 dni ani nie omija wymagania 25 dni zapisywania planów. Niezaliczone potwierdzenia dotyczące udostępnienia materiałów pozostają niezależnymi, niezaliczonymi wymaganiami.

Nowy raport podaje poprawność decyzji przy progu 50%, precyzję wskazań pozytywnych, czułość i średni błąd prawdopodobieństwa. Dla pustego zbioru wartości tych miar są puste, a nie równe 100%. Widoczna jest liczba wszystkich uzgodnionych wyników, liczba dokładnych dopasowań, ich udział i powody wyłączenia pozostałych.

## Powiązanie z dowodami

Kontrola czyta zabezpieczone sumami kontrolnymi raporty istniejącego audytu danych i audytu przedziałów. Wymaga zgodności z bieżącym rejestrem, konfiguracją i zamrożonym środowiskiem. Sprawdza wersje kodu obu audytów, pełne pokrycie zdarzeń wyniku i ponownie weryfikuje surowe odpowiedzi użyte jako źródło. Raport przyszły lub starszy niż dwie godziny jest odrzucany; odrzucane są również zmienione sumy kontrolne i przekierowania ścieżek.

Surowe czasy o rozdzielczości dokładniejszej niż mikrosekundy nie mogą zostać niejawnie obcięte przy parsowaniu. Zmiana wejść w trakcie oceny uniemożliwia opublikowanie aktualizacji. Awaria audytu nie zamienia się w zielony wynik.

## Testy i wdrożenie

23 nowe testy obejmują m.in. pozornie zaliczony raport bazowy przy 300 częściowych dopasowaniach, nakładanie się przez jedną sekundę, 299 zamiast wymaganych 300 dokładnych wyników, brak różnorodności stacji/satelitów, brak jednej klasy, nieznane etykiety, duplikaty ID, spóźnione prognozy i niespójne raporty. Wszystkie przeszły. Łącznie z istniejącymi testami audytów i eksportu uzyskano **96 zaliczeń w 0,59 s**. To testy oprogramowania na syntetycznych przypadkach, nie 96 nowych odbiorów.

Kontrola importów potwierdziła 31 modułów projektu z zamrożonego środowiska `runtime-memo-v1`, którego tożsamość pozostała niezmieniona. Końcowa implementacja, jej zależności, jednostki usługi i wyniki regresji są objęte plikiem `tooling.sha256`, sprawdzanym przed każdym uruchomieniem usługi. Zachowano pierwszy przebieg 23 testów i pierwszy ręczny raport; końcowa wersja obejmuje dodatkowo kontrolę wersji audytu danych i ograniczenie komunikatów.

Usługa `telemetry-yield-exact-slot-claim-v1.service` działa po ukończeniu dwóch istniejących audytów, które uruchamia jako zależności. Pierwsze automatyczne uruchomienie 10 września zakończyło się poprawnie o **15:15:48 UTC**. Timer jest aktywny i oczekuje na kolejne uruchomienie po około godzinie. Zmodyfikowano tylko lokalne ustawienia nowej usługi/timera; dotychczasowych jednostek nie podmieniono. Usługa ma limit jednego CPU, 2 GB pamięci i 10 minut działania. Nie wykonuje żądań do SatNOGS ani nie otwiera tokenu.

Komunikat pojawia się przy zmianie bramek, stanu gotowości lub przekroczeniu kolejnego progu 100 dokładnych dopasowań. Raporty są zapisywane niezależnie od komunikatów. Obecny wynik to zero wyników prospektywnych, puste miary trafności i brak zaliczenia — zgodnie z faktem, że kampania jeszcze się nie rozpoczęła.

## Granice wniosków

Pole `exact_window_predictor_validation_ready` dotyczy jakości oceny predyktora tylko na dokładnie dopasowanym podzbiorze. Nie jest potwierdzeniem przewagi nowej metody ani wykonania miesięcznego planu. Dobór przez naturalny harmonogram SatNOGS pozostaje źródłem selekcji; identyczne okno w API nie jest niezależnym dowodem pracy odbiornika przez cały przedział.

Kontrola nie uznaje rzeczywistego wzrostu liczby unikalnych, poprawnie zdekodowanych próbek za wykazany. Nie zastępuje też brakujących pomiarów anteny, weryfikacji CRC ani porównania wykonanego planera z metodami odniesienia. Cały cel badawczy pozostaje nieukończony. Sam upływ miesiąca nie wystarczy, jeśli wymagany dokładnie dopasowany podzbiór nie powstanie.

## Artefakty

- [Implementacja](/home/ubuntu/telemetry-yield/work/operations/exact_slot_publication_gate_v1.py), [testy](/home/ubuntu/telemetry-yield/work/operations/test_exact_slot_publication_gate_v1.py).
- [Kontrola 96 testów i importów](/home/ubuntu/telemetry-yield/reports/exact-slot-claim-gate-v1-20260910/test-import-guard-regression.json), [sumy kontrolne wdrożenia](/home/ubuntu/telemetry-yield/reports/exact-slot-claim-gate-v1-20260910/tooling.sha256).
- [Pierwszy automatyczny raport końcowej wersji](/home/ubuntu/telemetry-yield/reports/observation-planning-prospective-v4h/exact-slot-claim-audits/20260910T151547209379Z/report.json).
- [Wskaźnik najnowszego raportu](/home/ubuntu/telemetry-yield/reports/observation-planning-prospective-v4h/exact-slot-claim-latest.json) zawiera ścieżkę i sumę kontrolną; nie jest samym raportem.

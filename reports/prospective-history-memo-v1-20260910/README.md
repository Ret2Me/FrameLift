# Przyspieszenie historii bez zmiany prognoz

10 września 2026. To walidacja zgodności obliczeń, nie eksperyment poprawności przewidywania odbiorów.

## Uzasadnienie

Pierwszy plan kampanii v4h został zapisany 10 września o 13:20:53 UTC. Wybrał 12 202 obserwacje z 39 841 możliwości, obejmując wszystkie 50 satelitów i 12 aktualnie dostępnych stacji. Solver zwrócił `optimal` i zerową lukę optymalności. Oznacza to optimum względem zapisanych prognoz, funkcji celu i ograniczeń, a nie gwarancję maksymalnego rzeczywistego odbioru.

Od rozpoczęcia planowania do zapisu minęło 4401,94 s, czyli około 73 min. Kod rezerwuje godzinę przed pierwszą obserwacją. Dzisiejszy plan zaczyna się dopiero 13 września, więc został zapisany z wyprzedzeniem. Podczas właściwej kampanii podobna zwłoka może jednak doprowadzić do odrzucenia planu zawierającego już rozpoczęty przelot. Kontroli rzeczywistego czasu zapisu nie osłabiono.

Graphify wskazał zależności między zamrożonym estymatorem, historią a wykonaniem planu; bezpośrednie sprawdzenie kodu wykazało ponowne zliczanie tej samej historii dla każdej możliwości odbioru. Adapter zapamiętuje wyłącznie statystyki tej samej, niezmiennej partii historii. Pełna prognoza, geometria i parametry nadajnika są nadal obliczane przez oryginalną funkcję i oryginalny model. Nie ma ponownego uczenia ani globalnej podmiany funkcji.

## Test na rzeczywistym modelu

Sprawdzono 500 zapytań: pierwszą historyczną obserwację każdego z 50 satelitów, z dziesięcioma wariantami długości obserwacji i prawdopodobieństwa nadawania. Historia zawiera 27 292 kwalifikujące się rekordy; nie jest to liczba przykładów treningowych każdego z dwóch modeli. Nie oceniano etykiet ani trafności klasyfikacji.

| Wariant | Czas 500 zapytań | Zgodność wszystkich pól wyniku |
|---|---:|---|
| Oryginalny | 40,156 s | Punkt odniesienia |
| Adapter, początkowo pusta pamięć | 5,437 s | Dokładna, 500/500 |
| Adapter, powtórzenie zapytań | 0,147 s | Dokładna, 500/500 |

Pierwsze wykonanie było 7,39 razy szybsze. Powtórzenie korzysta z uprzednio obliczonych statystyk i nie reprezentuje kosztu pierwszego przeliczenia miesiąca. Kolejność pomiarów była ustalona, bez wielokrotnych powtórzeń i analizy zmienności obciążenia serwera. Nie są to gwarancje czasu działania na innym sprzęcie.

Pierwsza próba przygotowania testu zatrzymała się na niepoprawnej, nie­dodatniej wartości szybkości transmisji, zanim powstały prognozy lub protokół. W poprawionej konfiguracji taka wartość jest traktowana jako brak danych; historyczny zbiór pozostaje niezmieniony. Ta normalizacja dotyczy wyłącznie zestawu zapytań testu szybkości.

Wszystkie 12 testów przeszło również z importami z izolowanego runtime v4h. Obejmują zgodność pełnych wyników, zmianę geometrii, brakujące wartości, duplikaty, zachowanie historycznej semantyki wag, zmianę partii danych, ochronę przed modyfikacją zwróconego słownika, limit pamięci oraz współbieżne odczyty. Nie są to 12 nowych wyników terenowych.

## Granica wdrożenia

Adapter jest zaimplementowany jako jawnie wybierany wariant w `work/operations/frozen_history_memo.py`. **Nie jest aktywny w kampanii.** Zmiana zamrożonego programu wymaga osobno zapisanej poprawki wersji obliczeniowej i testów powiązanych z jej kodem. Nie wolno po cichu zmienić lub ponownie zapieczętować istniejącego runtime.

Odrębne odtwarzanie pełnego miesiąca zakończyło się poprawnie i zapisało wynik w `reports/prospective-month-memo-replay-v1-20260910`. Wykorzystało zapisane TLE, prognozę pogody i parametry stacji; przed obliczeniem potwierdzono identyczny odcisk wejść planera. Nie pobrano nowych danych, nie zapisano zdarzeń kampanii i nie wysłano obserwacji do SatNOGS.

Obliczenie geometrii, prognoz i harmonogramu trwało **909,480 s — 15 min 9 s**. Pełny dokument JSON planu jest dokładnie identyczny z oryginałem: 39 841 możliwości, 12 202 przypisania, wszystkie zapisane cechy, prawdopodobieństwa, wartość funkcji celu i wynik optymalizacji. Nie ma różniących się pól. Zarejestrowano 476 pierwszych przeliczeń statystyk oraz 79 206 odczytów z pamięci. Dane wejściowe, zamrożony kod źródłowy i dziennik kampanii pozostały niezmienione.

Czas odtworzenia nie obejmuje pobierania danych, przygotowania modelu ani serializacji artefaktów. Oryginalne 4401,94 s obejmuje pobranie TLE i zapis planu. Nie przedstawiamy ich ilorazu jako kontrolowanego pomiaru przyspieszenia całej usługi. Oryginalne prognozy niewybranych możliwości nie były indywidualnie zapisane — pełna zgodność dokumentu planu nie jest porównaniem każdej z tych niezapisanych prognoz.

Żadnego z powyższych wyników nie należy opisywać jako wzrostu precyzji predykcji. Zysk polega na skróceniu kosztu obliczeń przy zachowaniu tych samych odpowiedzi. Badania nowych metod predykcji i ich ograniczenia opisuje osobny raport `reports/reception-revision-v1-20260910/deep-research.md`.

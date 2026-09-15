# Wynik wdrożenia lokalnego: wybór metody, nie obowiązkowe AI

Etap `labels-25000` zakończył się 8 września 2026 o 23:34:54 UTC.
Wybrany zestaw jest dostępny przez `current.json` i polecenie `predict`.
To wdrożenie badawcze/lokalne; nie zmieniono zleceń ani konfiguracji stacji SatNOGS.

## Co rzeczywiście wybrano

- **Sygnał:** 75% prognozy nowego modelu + 25% prognozy historii ostatnich 10 odbiorów.
- **Plik po demodulacji, warunkowo:** sama historia ostatnich 10 odbiorów,
  z łagodnym powrotem do statystyk satelity i stacji przy małej próbie.

Na każdym kolejnym ukończonym progu danych usługa może ponowić to samo,
zamrożone porównanie. Nie zakłada, że bardziej złożona metoda powinna wygrać.

## Wyniki na tych samych danych kontrolnych

| Zadanie | Poprzedni AI v2 | Nowy samodzielny model | Wybrany wariant v3 | Sama historia |
| --- | ---: | ---: | ---: | ---: |
| Sygnał, 1099 przelotów | 85,81% | 85,35% | **85,90%** | 85,17% |
| Plik demodulacji, 810 przelotów | 76,05% | **81,11%** | **80,00%** | 80,00% |

To trafność decyzji przy progu 0,5 na czerwcowym panelu znanych grup. Modele
oceniane na tym panelu dopasowano wyłącznie do wcześniejszych danych.
Późniejszy pełny model badawczy sygnału nie jest źródłem tych wyników.

Dlaczego nie wybrano 81,11% dla dekodowania? Celem jest dobre oszacowanie
prawdopodobieństwa do planowania, nie wyłącznie trafność po przekroczeniu 50%.
Samodzielny model miał nieco większy średni kwadrat błędu prawdopodobieństw niż
historia: 0,138692 wobec 0,136504. Wybrane wcześniej na marcu połączenie 50/50
uzyskało 80,62% i mniejszy błąd 0,134447, lecz przedział porównania obejmował
brak poprawy. Zgodnie z zapisanym przed dopasowaniem warunkiem pozostawiono
historię, zamiast po obejrzeniu wyników zmienić kryterium.

Dla sygnału połączenie 75/25 zmniejszyło błąd prawdopodobieństw z 0,106204
do 0,098373 względem historii, czyli o około 7,4%. Przeszło wszystkie warunki
dopuszczenia wariantu. Nie oznacza to wzrostu trafności o 7,4 punktu procentowego.

Przedziały porównania Brier, obliczone przez resampling 14 dni:

- Sygnał, połączenie minus historia: -0,007831; przedział 95%
  [-0,014494; -0,000894].
- Demodulacja, połączenie 50/50 minus historia: -0,002057; przedział 95%
  [-0,005614; 0,002078].

Są to analizy rozwojowe. Marzec służy wyborowi kandydata, a wcześniej oglądany
czerwiec jest kontrolą dopuszczenia, **nie końcowym testem**. Zależności między
stacjami i satelitami mogą pozostać również między dniami. Nie wykazano jeszcze
poprawy liczby rzeczywiście odebranych pakietów w działającej stacji.

## Dane i kontrole

- Ta sama kohorta co w poprzednim etapie: 87 464 unikalne wiersze, 1079 satelitów
  i 512 stacji; 38 011 etykiet sygnału i 25 806 etykiet warunkowej demodulacji.
- Czerwcowe modele miały odpowiednio 33 577 i 22 864 wcześniejsze etykiety.
  Ich identyfikatory i sumy kontrolne dokładnie odpowiadały treningom v2.
- 23 kandydatów; każda z 15 starych reguł odtworzyła wcześniejsze prognozy.
- Audyt sprawdził 87 464 wiersze, 4265 kontekstów predykcji, ograniczenia dat
  i grup, sumy kontrolne oraz ponowne wczytanie zapisanego modelu.
- 83 testy przeszły przed zamrożeniem: nowe funkcje i regresje dotychczasowej
  historii, pobierania, treningu, TLE, blokad i planera.
- Domyślny wybrany model przeszedł rzeczywiste lokalne wywołanie na istniejącej
  bazie historii. `synthetic-pass-smoke-request.json` jest **sztucznym zapytaniem
  testowym**, nie potwierdzonym przelotem ISS. Jego wynik dowodzi działania
  wczytania modelu i ścieżki wnioskowania bez opcjonalnych parametrów, a nie
  jakości prognozy. Trafnie zgłosił brak historii tej pary i stare dane odbiorów.

## Co działa w tle i jak używać

`telemetry-yield-adaptive-method-v3.timer` sprawdza nowe ukończone progi co
30 minut. Pobieranie oraz oryginalny trening v2 pozostają osobnymi procesami.
Nowa usługa ma własną blokadę, limit 3 GiB pamięci i dwóch CPU. Nie wykonuje
nowych zapytań do SatNOGS i nie modyfikuje poprzednich wyników.

Instrukcje wywołania prognozy i podłączenia do `DynamicObservationPlanner`
są w [dokumentacji](../../docs/adaptive-reception-v3.md). Adapter zachowuje
geometrię TLE, okna nadawcze, wymagania sprzętowe i blokady. Miesięczne prognozy
wymagają jawnego oznaczenia jako wstępne, poza sprawdzonym horyzontem 2–26 h.

Ważne ograniczenia: plik demodulacji nie potwierdza poprawnych pakietów;
nowy wariant nie dopasowuje jeszcze pogody, mocy ani zysku anteny;
przedziały w planie są konserwatywnymi granicami, nie skalibrowaną precyzją;
potrzebne są świeża historia i oddzielna ocena prospektywna. Odłożonych testów
sierpniowych i zarezerwowanych grup nie otwarto.

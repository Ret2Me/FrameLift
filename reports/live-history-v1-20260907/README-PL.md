# Odświeżana historia odbiorów — 7 września 2026

## Co wdrożono

- Osobną bazę historii z dwiema datami: zakończenia odbioru oraz dostępności
  wyniku. Korekty są wersjonowane; powtórne pobranie nie zwiększa liczby
  obserwacji, a nieznany lub wycofany wynik nie staje się porażką.
- Historię satelity, stacji, nadajnika i ich kombinacji, okna 1/7/30/90/180
  dni, ostatnie 10/50 odbiorów, trendy oraz porównania z innymi stacjami
  i satelitami. Podobieństwo geometrii oznacza tutaj przedział elewacji
  co 15 stopni, nie pełne podobieństwo warunków propagacyjnych.
- Lokalny interfejs predykcji korzystający z bieżącej migawki bazy,
  bez treningu przy każdym zapytaniu. Domyślny wariant dla każdego zadania
  wybierany jest według marcowego porównania znanych grup, nie czerwca.
  Nie jest to zwalidowany wybór dla obiektów jednocześnie nowych po obu stronach.
- Synchronizację co 5 minut z zatwierdzonymi stronami kolektora SatNOGS,
  bez dodatkowych żądań API i bez sterowania stacjami.
- Kontynuację treningów przy kolejnych 5000, 20 000 i 100 000 nowych
  kwalifikujących się rekordów z kompletnych dni. Wspólna blokada zapobiega
  uruchamianiu tego treningu równocześnie z dotychczasowym treningiem sieciowym.

Synchronizacja obejmuje obecnie pobierane ARCHIWUM sprzed sierpnia, z ochroną
zarezerwowanych grup. Świeży czas pobrania nie oznacza świeżego odbioru.
To nie jest jeszcze podłączenie odbiorów z ostatniej doby ani zmiana
zamrożonego planera V4. Baza potrafi obsłużyć późniejsze zdarzenia z jawnie
zapisanym czasem dostępności, ale podłączenie takiego źródła wymaga osobnego
zakresu, który nie odsłoni zarezerwowanego testu. Nie zmieniono historycznych
zbiorów uczących, testów, harmonogramu kampanii ani modeli V4.

## Pierwsze wyniki: kontrolowane porównanie

32 201 kwalifikujących się rekordów, 60 satelitów, 431 stacji.
15 832 etykiety sygnału i 6279 etykiet warunkowego pliku demodulacji.
Wykonano 56 dopasowań porównawczych oraz 4 końcowe dopasowania badawcze.
28 bloków walidacji; 54 297 zapisów przewidywań obejmuje powtórzenia tych
samych obserwacji między wariantami, a nie tyle niezależnych przykładów.

Poniższa tabela izoluje wpływ odświeżania: STAŁE współczynniki i ten sam
sposób kodowania wejścia, zmienia się tylko historia dostępna przy zapytaniu.
Trafność decyzji przy z góry określonym progu 50%, na czerwcowej części
rozwojowej. Nie jest to porównanie z wcześniejszym testem sierpniowym.

| Zadanie i sytuacja | Obserwacje | Stała historia | Odświeżana historia |
|---|---:|---:|---:|
| Sygnał, znane satelity/stacje | 1099 | 79,0% | 85,0% |
| Sygnał, nowe satelity zbierające historię | 2115 | 40,3% | 70,7% |
| Sygnał, nowe stacje zbierające historię | 2115 | 64,2% | 76,5% |
| Plik demodulacji przy obecnym sygnale, znane grupy | 810 | 72,3% | 77,9% |
| Plik demodulacji, nowe satelity zbierające historię | 1640 | 49,5% | 68,0% |
| Plik demodulacji, nowe stacje zbierające historię | 1640 | 56,6% | 70,8% |

Dla znanych grup błąd prawdopodobieństw zmalał z 0,1407 do 0,1130 dla sygnału
oraz z 0,1938 do 0,1516 dla warunkowego pliku demodulacji. Mniej znaczy lepiej.
Reguła przewidywania zawsze klasy większościowej daje odpowiednio 61,1%
i 66,9% trafności na tych panelach.

## Nie wybieramy najlepszego wyniku po fakcie

Trzeci wariant dodaje krótkoterminowe trendy. Nie wygrywa wszędzie.
Reguła wyboru została zapisana przed nową serią: wariant i próg dobieramy
na marcu, później sprawdzamy czerwiec, bez ponownego wyboru.

| Zadanie i sytuacja | Wariant wybrany na marcu | Czerwiec: trafność przy progu 50% |
|---|---|---:|
| Sygnał, znane grupy | Odświeżany z krótkimi trendami | 80,7% |
| Sygnał, nowe satelity | Odświeżany, standardowy | 70,7% |
| Sygnał, nowe stacje | Odświeżany z krótkimi trendami | 79,0% |
| Plik demodulacji, znane grupy | Odświeżany, standardowy | 77,9% |
| Plik demodulacji, nowe satelity | Odświeżany z krótkimi trendami | 69,0% |
| Plik demodulacji, nowe stacje | Odświeżany z krótkimi trendami | 71,6% |

Dlatego 85,0% z kontrolowanego porównania sygnału NIE jest wynikiem wariantu
automatycznie wybranego na marcu. Wynik tego wyboru to 80,7% przy stałym
progu 50% albo 81,2% przy progu dobranym na marcu. Przedział niepewności
poprawy błędu dla tego wybranego wariantu nieznacznie obejmuje brak poprawy.
Pełne wyniki obu progów, czułość, fałszywe alarmy i rozrzut oszacowań są
w `local/results.json`; nie sprowadzamy oceny do jednej trafności.

## Co wyniki rzeczywiście znaczą

- Jest to retrospektywny eksperyment ROZWOJOWY. Marzec i czerwiec były już
  oglądane we wcześniejszych badaniach; nie są nowym niezależnym testem końcowym.
- Brakuje historycznego czasu opublikowania/oceny każdego odbioru. Przyjęto
  jawnie 24 godziny opóźnienia. Testy symulują tę dostępność, nie potwierdzają
  rzeczywistych opóźnień SatNOGS. Integracja lokalna używa rzeczywistego czasu
  otrzymania odpowiedzi i nie podszywa się pod to założenie.
- Po zakończeniu wcześniejszego odbioru jego etykieta może wejść do kontekstu
  późniejszej predykcji. Nie wchodzi do własnej predykcji, wcześniejszych
  predykcji ani do zmiany współczynników modelu w danym miesiącu.
- Nowy satelita/stacja może w trakcie sprawdzanego okresu zebrać historię.
  To adaptacja na podstawie pierwszych odbiorów, NIE jakość pierwszej
  predykcji dla całkowicie nieznanego obiektu.
- Sprawdzono codzienne aktualizacje z wyprzedzeniem 2–26 godzin, nie gotowy
  plan całego miesiąca bez późniejszych aktualizacji.
- Warunkowy plik demodulacji nie oznacza potwierdzenia dekodowania z CRC.
- Kontrola odtwarza konteksty, etykiety i podziały oraz sprawdza sumy plików.
  Nie stanowi niezależnego ponownego dopasowania wszystkich modeli.

## Użycie interfejsu badawczego

Parametry orbitalne w przykładzie są SYNTETYCZNE, nie opisują wyliczonego
przelotu. Dla rzeczywistej prognozy należy podać geometrię wyliczoną z TLE
i czas przyszłego przelotu. Parametry radia/nadajnika mogą być pominięte.

```bash
rtk proxy .venv/bin/python work/operations/live_history_service.py predict \
  --request reports/live-history-v1-20260907/example-synthetic-request.json \
  --output reports/live-history-v1-20260907/example-synthetic-prediction.json
```

Wynik ma oznaczenie `research_only`, oddzielne prawdopodobieństwo sygnału
i warunkowego pliku demodulacji, liczbę pasujących odbiorów, ich świeżość
oraz sumy kontrolne kontekstu i modelu. Nie ma automatycznego wdrożenia
do zamrożonego planera ani wysyłania zleceń do SatNOGS.

## Bieżące pliki

- `live-store-progress.json`: synchronizacja bazy historii.
- `local/progress.json`: etap badania.
- `local/results.json`: wszystkie warianty i metryki.
- `local/integrity-audit.json`: wynik kontroli pochodzenia i kontekstów.
- `continuation-policy.json`: zasady kolejnych treningów.

Mapę Graphify wykorzystano do odnalezienia istniejących interfejsów historii
i oddzielenia nowego eksperymentu od zamrożonego modelu. Zasada rozdzielania
czasu zdarzenia i dostępności jest również opisana w
[dokumentacji Feast](https://raw.githubusercontent.com/feast-dev/feast/master/docs/getting-started/concepts/point-in-time-joins.md).

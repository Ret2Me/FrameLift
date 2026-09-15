# Eksploracyjny raport następcy modelu — 4 września 2026

## Najważniejszy wynik

Na zamrożonej, częściowej kopii danych nowy model istotnie lepiej przewiduje przyszłe przeloty **znanych** satelitów i stacji niż dotychczasowa regresja logistyczna. Nie jest to jeszcze wynik publikacyjny ani podstawa do wdrożenia: sierpniowe wyniki zostały już obejrzane, kopia zawiera tylko 11 z planowanych 50 satelitów, a pełna miesięczna kampania prospektywna jeszcze się nie zakończyła.

| Zadanie | Stary model: trafność | Nowy model: trafność | Stary Brier | Nowy Brier | Zmiana Brier |
|---|---:|---:|---:|---:|---:|
| Pojawienie się sygnału | 70,13% | **73,35%** | 0,2062 | **0,1832** | **−11,1%** |
| Dekodowanie pod warunkiem sygnału | 72,04% | **77,42%** | 0,1939 | **0,1588** | **−18,1%** |

Test obejmuje 2293 sierpniowe obserwacje dla sygnału (989 pozytywnych) i 279 dla dekodowania (84 pozytywne). Większościowe modele zerowe osiągnęłyby odpowiednio 56,87% i 69,89% trafności.

Nowy model sygnału ma AUROC 0,8008 wobec 0,8053 starego modelu, więc poprawa nie pochodzi z lepszego uporządkowania wszystkich przelotów, lecz przede wszystkim z lepszych wartości prawdopodobieństwa i progu decyzji. Dla dekodowania AUROC wzrosło z 0,7455 do 0,8079. Błąd kalibracji ECE spadł z 0,1573 do 0,0679 dla sygnału i z 0,1615 do 0,0627 dla dekodowania.

## Niepewność wyniku

W dodatkowym, jawnie post-hoc bootstrapie grupowanym po parze satelita–stacja różnica Brier „nowy minus stary” wyniosła:

- sygnał: −0,02294; eksploracyjny przedział 95% od −0,04724 do −0,00096; 98,0% replikacji przemawiało za nowym modelem;
- dekodowanie: −0,03513; przedział 95% od −0,08159 do +0,00342; 95,95% replikacji przemawiało za nowym modelem, ale przedział nadal przecina zero z powodu małej próby.

Te przedziały nie są wynikiem potwierdzającym, ponieważ zostały policzone po obejrzeniu zagregowanych wyników następcy. Służą do zaprojektowania następnego, zamrożonego eksperymentu.

## Co faktycznie poprawiło model

Największy zysk dała historia krocząca z ostatnich 30, 90 i 180 dni, liczona wyłącznie z obserwacji zakończonych przed planowanym przelotem. W porównaniu tego samego algorytmu i tych samych parametrów:

- dla sygnału Brier na czerwcowej walidacji zmieniło się z 0,1620 (cechy operacyjne) przez 0,1622 (pogoda i kalendarz) do **0,1519** po dodaniu historii kroczącej;
- dla dekodowania: 0,1667 → 0,1693 → **0,1489**.

Sama historyczna pogoda naziemna i Kp nie poprawiły jeszcze wyniku na częściowej kohorcie. Nie oznacza to, że są bezużyteczne; ich przyrost trzeba ponownie ocenić na wszystkich 50 satelitach. Fizycznych parametrów anten nie użyto retrospektywnie, ponieważ SatNOGS nie udostępnia wiarygodnych, obowiązujących w czasie obserwacji archiwalnych konfiguracji anten. Użycie dzisiejszej konfiguracji do dawnych obserwacji byłoby przeciekiem danych. Parametry anten są przewidziane dla kampanii prospektywnej.

Kalibracja Platta uczona tylko na czerwcu pogorszyła sygnał. Osobny eksperyment z trzema kolejnymi oknami czasowymi wybrał dla obu zadań brak dodatkowej kalibracji. Oznacza to, że surowe prawdopodobieństwa mocniejszego modelu są obecnie bezpieczniejszym wyborem.

## Nowe satelity i nowe stacje

Pełny model drzewiasty dobrze działa na przyszłych przelotach znanych obiektów, ale początkowo pogarszał wynik dla obiektów całkowicie nowych. Powodem było nadmierne poleganie na identyfikatorach oraz historii konkretnego satelity lub stacji.

Dodano tryb „cold start”, który usuwa niedostępne informacje o nowej grupie:

- dla nowego satelity usuwa jego identyfikator oraz historię satelity, nadajnika i pary satelita–stacja, zachowując geometrię, modulację, częstotliwość, pogodę i historię znanej stacji;
- dla nowej stacji usuwa identyfikator i historię stacji/pary, zachowując informacje o satelicie i nadajniku;
- prostszy model operacyjny jest zawsze kandydatem awaryjnym, więc wewnętrzna walidacja może odrzucić zbyt ryzykowny model złożony.

Rezultat częściowy:

| Zewnętrzny przypadek | Liczba testów | Wynik pełnego następcy | Wynik po „cold start” | Prosty model | Ocena bramki |
|---|---:|---:|---:|---:|---|
| Sygnał, nowy satelita | 104 | 0,2214 | **0,1898** | 0,1911 | za mało grup: 1 zamiast ≥5 |
| Sygnał, nowe stacje | 398 | 0,2235 | **0,2082** | 0,2082 | przechodzi bramkę częściową; wybrano bezpieczny model prosty |
| Dekodowanie, nowy satelita | 18 | 0,1544 | **0,1402** | 0,2282 | nieważne do wnioskowania: 0 sukcesów i za mało danych |
| Dekodowanie, nowe stacje | 29 | 0,2495 | **0,1715** | 0,1973 | o 1 próbkę poniżej minimum i tylko 4 sukcesy |

Wartości w trzech ostatnich kolumnach to Brier — mniej znaczy lepiej. Dla jedynego obecnie nowego satelity model ma wysoką trafność 82,69%, ale AUROC tylko 0,4089 i wykrywa 1 z 18 pozytywnych przypadków. Sama trafność byłaby więc myląca; problem nowych satelitów pozostaje otwarty do czasu zebrania pełnej zewnętrznej kohorty.

## Kontrole jakości

- Audyt głównego następcy przeszedł 11/11 kontroli.
- Audyt trybu „cold start” przeszedł 25/25 kontroli.
- Kontrfaktyczne odwrócenie wszystkich sierpniowych etykiet nie zmieniło ani jednej historii wejściowej modelu; etykiety testowe nie przeciekają do cech.
- Protokoły, skrypty, dane, zależności i predykcje są związane sumami SHA-256.
- Wszystkie wyniki następcy mają wymuszone flagi `confirmatory_eligible=false`, `publication_eligible=false` i `deployment_eligible=false`.

## Co dzieje się dalej

Główny kolektor SatNOGS nadal działa. O 16:15 UTC miał 27 562 pobrane rekordy i 124 pozostałe grupy zapytań; monitor operacyjny nie zgłaszał błędów. Po ukończeniu zamrożonego pipeline'u automatycznie uruchomi się osobny zestaw badań następcy na kompletnej kohorcie. Nie zmieni on modelu aktywnej kampanii prospektywnej.

Do wyniku nadającego się do publikacji nadal wymagane są wszystkie trzy warunki:

1. pełna kohorta 50 satelitów wraz z zaplanowanymi zewnętrznymi satelitami i stacjami;
2. brak przecinania zera przez wcześniej zadeklarowane przedziały poprawy na głównych testach grupowych;
3. co najmniej miesięczna, prospektywna praca planera z realnymi blokerami, prognozą pogody i konfiguracjami anten.

## Artefakty

- `successor-benchmark-v1/protocol.json` i `results.json` — wybór modelu oraz test czasowy;
- `successor-benchmark-v1/audit.json` — audyt integralności i dodatkowa niepewność;
- `successor-calibration-v1/results.json` — kalibracja wielookresowa;
- `successor-external-v1/results.json` — pierwsza diagnoza nowych grup;
- `group-robust-successor-v1/results.json` i `audit.json` — wariant „cold start” i jego audyt.

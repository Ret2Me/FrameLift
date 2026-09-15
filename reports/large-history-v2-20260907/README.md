# Równoległe badania: większy trening AI i proste reguły

Stan wdrożenia: 7 września 2026, około 19:44 UTC. To badanie rozwojowe, nie wdrożenie na stacjach.

## Co działa

- Pobieranie Network jest uruchomione w tle. Zakres rozszerzono z 77 do 943 całych dni UTC: 2024-01-01–2026-07-31. Zachowano stare rekordy, kursory, przerwy po błędach i wszystkie ograniczenia tempa API. Limit ilościowy to teraz 240 000 stron / 30 GB odpowiedzi przed kompresją; nie jest to obietnica liczby unikalnych etykiet.
- Nowy trening wykorzystuje wszystkie kwalifikujące się etykiety, bez losowego ograniczenia próbki. Kompaktowy zapis SQLite jest dopisywany, a macierz 39 cech float32 jest tymczasowo zapisywana na dysku. Dołączane są tylko zakończone dni pobierania. Nie tworzymy już kolejnych pełnych eksportów JSONL co pół godziny.
- Automatyczny harmonogram sprawdza przyrost co 30 minut. Progi: 10 tys., 25 tys., 50 tys., 100 tys., 250 tys., 500 tys. i milion etykiet **dla każdego z dwóch zadań**. Etap używa całego zbioru dostępnego po przekroczeniu progu, nie dokładnie N przykładów. Jeśli przyrost przekroczy kilka progów naraz, wybierany jest najwyższy; rozpoczęty etap jest najpierw kończony.
- Równoległy eksperyment porównał 15 prostych reguł z dotychczasowym AI przy identycznej historii i przelotach. Te same rodziny reguł są oceniane przy kolejnych etapach nowego treningu.
- Poprzednie modele, zamrożone eksperymenty i eksporty zachowano. Zastąpione timery `telemetry-yield-network-training.timer` i `telemetry-yield-live-history-training.timer` wyłączono. Synchronizacja historii i istniejące kampanie prospektywne pozostały bez zmian.

## Rzeczywiste dane i wyniki pierwszego etapu

Zamrożona kohorta ma 60 227 unikalnych wierszy, 889 satelitów i 481 stacji. Końcowe modele badawcze wykorzystały wszystkie 26 607 etykiet sygnału oraz 16 602 etykiety warunkowej demodulacji. Brak oceny nie jest traktowany jako nieudany odbiór.

Modele oceniane na czerwcu były dopasowane wyłącznie do wcześniejszych danych: odpowiednio 22 173 i 13 660 etykiet. Późniejszy pełny trening badawczy nie jest źródłem raportowanych wyników czerwcowych.

| Czerwcowy panel znanych grup | Nowy, kompaktowy AI | Prosta historia | Liczba ocenionych przelotów |
| --- | ---: | ---: | ---: |
| Pojawienie się sygnału | 85,53% | 84,99% | 1099 |
| Plik po demodulacji, warunkowo | 75,19% | 80,62% | 810 |

To trafność decyzji przy progu 0,5, nie prawdopodobieństwo poprawności dowolnego pojedynczego zapytania. Regułę wybierano na marcu, nie na czerwcu. Ocena samych prawdopodobieństw wypada na razie lepiej dla reguły w obu zadaniach; w przypadku sygnału różnica nie jest rozstrzygająca w zastosowanym resamplingu dni. Wyniki AI nie uzasadniają automatycznego porzucenia prostych metod.

Oddzielne porównanie ze starym AI na jego identycznych kontekstach dało dla znanych grup: sygnał 84,44% AI / 84,99% reguła, demodulacja 77,78% AI / 80,62% reguła. Na grupach nowych, ale otrzymujących późniejszą historię, wynik bywa korzystniejszy dla AI. Szczegóły znajdują się w `../history-baselines-v1-20260907/network-20000/results.json`.

## Co rzeczywiście zweryfikowano

- 32 testy oraz 10 subtestów: normalizacja granic, chronologia, niezależne porównanie liczników z prostym przeliczeniem na małych danych, duplikaty, brakujące dane, kompletność użycia etykiet, ograniczenia API i odtwarzanie przerwanej zmiany protokołu bez utraty kursorów.
- Audyt aktualnego etapu sprawdził 60 227 wierszy, 6 zbiorów dopasowania i 4265 kontekstów predykcji; odtworzył reguły, sprawdził zgodność paneli oraz ponowne wczytanie modeli. To audyt strukturalny ze współdzieloną implementacją historii, nie niezależny ponowny trening AI.
- Test obciążeniowy przetworzył **milion syntetycznych wierszy**: budowanie cech 92,9 s, pełny trening 180 iteracji 26,4 s, łącznie 120,5 s; szczyt RSS około 2,35 GiB. Nie dodano tych wierszy do prawdziwego zbioru i nie liczono na nich skuteczności odbiorów. Usunięto wyłącznie wygenerowane, tymczasowe macierze tego testu.
- Pierwszy start zatrzymała zbyt restrykcyjna kontrola etykiet. Przed jakimkolwiek dopasowaniem skorygowano ją zgodnie z istniejącą, zamrożoną definicją: plik demodulacji może być dodatnim dowodem przy nieocenionym niezależnie waterfallu. Nie dopisano etykiety sygnału. Zachowano poprzedni kod, protokół i zapis poprawki w `preflight-label-validation-amendment/`.

## Ograniczenia i interpretacja

- Milion rzeczywistych etykiet nie został jeszcze pobrany. Przy rozszerzeniu archiwum miało 109 582 surowe unikalne rekordy, ale wiele miało nieznany wynik, należało do chronionych grup lub nieukończonych dni pobierania.
- Na dysku pozostaje około 6,6 GB; procesy zatrzymują przyrost przy 4 GB wolnego miejsca. Do docelowego wielomilionowego archiwum prawdopodobnie potrzebna będzie dodatkowa przestrzeń. Nie usunięto danych użytkownika.
- Nowy AI ma inną, oszczędniejszą reprezentację: geometria, kalendarz, modulacja i historia. Nie jest to kontrolowany eksperyment zmieniający wyłącznie wielkość danych. Nie wykorzystuje pełnego zestawu pogodowo-antenowego wcześniejszych wariantów i nie zastępuje ich jako model produkcyjny.
- Marcowe i czerwcowe panele są już danymi rozwojowymi. Sierpień i zewnętrznie zarezerwowane grupy pozostają wyłączone; potrzebny będzie osobny, jednorazowy protokół oceny wybranego wariantu na końcowym teście.
- Historia zakłada dostępność wyniku po 24 godzinach. Nie zweryfikowano rzeczywistych historycznych czasów publikacji wszystkich ocen. Prognozy dotyczą 2–26 godzin naprzód, nie nieruchomego planu na cały miesiąc.
- Warunkowa demodulacja oznacza obecność pliku, a nie potwierdzone poprawne pakiety z CRC. Nie wykazano jeszcze wzrostu liczby odebranych próbek w działającej stacji.

## Utrzymanie

`telemetry-yield-network-scale.service` pobiera dane; `telemetry-yield-large-history-v2.timer` wyzwala kolejne etapy; usługa treningowa po zakończeniu wykonuje audyt. Zakończenie dopasowania oznacza oczekiwanie na nowy próg danych, nie nieprzerwany trening.

Najważniejsze pliki: `cohort-progress.json`, `progress.json`, `labels-10000/results.json`, `labels-10000/integrity-audit.json`, `synthetic-capacity-1000000.json`. Historyczne `failure.json` opisuje nieudany pierwszy start; nowszy stan i audyt potwierdzają jego naprawę.

Przed edycją zamrożonych skryptów trzeba zapisać nowy wariant/protokół lub udokumentowaną poprawkę. Nie zmieniać po cichu sum kontrolnych dotychczasowych badań. Mapę Graphify wykorzystano do znalezienia zależności historii i pobierania oraz odizolowania nowych prac od zamrożonych eksperymentów.

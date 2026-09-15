# Miesięczna ocena obu wyników i grup nieobecnych w historii

## Cel rozszerzenia

Istniejące miesięczne porównanie zachowuje prognozy i dane pozwalające ocenić zarówno obecność sygnału, jak i wynik demodulacji przy potwierdzonym sygnale. Jego raport zbiorczy skupiał się jednak na drugim wyniku. Nowy, osobny moduł podsumowuje oba zadania na tej samej, z góry wybranej populacji dokładnych obserwacji.

Rozszerzenie nie dopasowuje modelu, nie pobiera danych, nie zleca obserwacji i nie zmienia istniejących prognoz ani protokołu kampanii. Nie wykorzystuje częściowo nakładających się okien jako zamiennika wyniku dokładnie przewidywanego przelotu.

## Co będzie oceniane

- **Sygnał:** niezależna ocena obrazu widma w SatNOGS. Cztery istniejące metody: zamrożony model, historia konkretnego połączenia, historia ostatnich odbiorów oraz średnia globalna.
- **Demodulacja przy potwierdzonym sygnale:** obecność artefaktu demodulacji. Te same cztery metody i wariant historii ograniczonej do niezależnie ocenionych odbiorów. Artefakt nie jest dowodem poprawnego CRC ani liczby nowych próbek telemetrii.

Raport podaje trafność decyzji przy progu 0,5, odsetek poprawnych wskazań dodatnich, czułość i średni kwadrat błędu prawdopodobieństwa. Porównania metod używają tych samych etykietowanych przypadków. Podstawowe różnice błędu to model minus historia połączenia dla sygnału oraz nowa historia minus dotychczasowa historia połączenia dla demodulacji.

Przedziały niepewności wykorzystują istniejącą metodę grupowanego losowania: 2000 powtórzeń, ziarno 42 i co najmniej 10 grup. Grupowanie po dniu jest główne; grupowanie po satelicie i stacji stanowi analizę pomocniczą. To przedziały opisowe, bez poprawki na wielokrotne porównania. Nie ustanawiają automatycznie istotności ani zwycięzcy.

## Znane i nieznane grupy

Każdy wynik będzie pokazany dla całej próby oraz osobno dla satelitów i stacji obecnych lub nieobecnych w całym zamrożonym zbiorze 47 039 obserwacji. Ta definicja jest bardziej ostrożna niż brak etykiet w pojedynczym zadaniu predykcyjnym.

W wybranej populacji jest **8 satelitów nieobecnych w tej historii i zero takich stacji**. Z tego eksperymentu może więc powstać prospektywna ocena nowych satelitów, o ile rzeczywiście pojawią się odpowiednio ocenione obserwacje. Nie można obiecać analogicznej oceny nowych stacji przy obecnej, niezmienionej populacji. Raport pustej grupy ma zerową liczebność, brak wartości trafności i jawny brak podstaw do walidacji. Nie zastępuje wcześniejszych oddzielnych testów z wyłączaniem stacji z uczenia.

Nie rozszerzono ani nie wymieniono stacji po obejrzeniu wyników. Zachowano wszystkie 50 satelitów i 13 identyfikatorów stacji istniejącego protokołu.

## Wybór prognozy i etykiety

Dla każdego identyfikatora obserwacji wybierany jest najwcześniejszy kwalifikowany zapis nowego wariantu historycznego, zanim raport sprawdzi dostępność wyników. Wspólny wybór dotyczy obu zadań. Późniejsza prognoza nie zastępuje wcześniejszej dlatego, że ma już etykietę lub lepszy wynik.

Oceny po 24, 72 i 168 godzinach są oddzielne; główny jest etap tygodniowy. Przed agregacją istniejący walidator ponownie wyprowadza etykiety z zachowanych odpowiedzi API i sprawdza zgodność z zapisanymi wynikami. Nowa kontrola sprawdza ponadto dokładny zestaw identyfikatorów w zapytaniu oraz rzeczywisty czas otrzymania odpowiedzi dla każdego wybranego identyfikatora — **także wtedy, gdy odpowiedź była pusta**. Samo umieszczenie pliku w katalogu etapu tygodniowego nie dowodzi tygodniowego odstępu.

Brak obserwacji, brak oceny, zmiana jej tożsamości lub okna nie są porażką odbioru. Brak dodatnio potwierdzonego sygnału oznacza, że warunkowa ocena demodulacji nie ma zastosowania, a nie że nieznaną etykietę wolno zastąpić zerem. Raport rozdziela te powody ubytku danych.

## Kompletność i granice wniosków

Raport wymaga osobno co najmniej 300 ocen sygnału i 100 ocen warunkowej demodulacji, odpowiedniej liczby dodatnich i ujemnych wyników oraz różnorodności satelitów i stacji w obu zadaniach. Zachowuje też 30-dniowy czas, koniec całej kampanii, 25 dni zbierania i kompletność końcowych pobrań dla wybranych prognoz.

Spełnienie tych liczników nie ogłasza gotowości całego projektu do publikacji. Naturalny wybór obserwacji przez użytkowników SatNOGS i dostępność ręcznych ocen ograniczają możliwość uogólnienia. Raport nie dowodzi wykonania naszego harmonogramu, przyczynowego wzrostu uzysku ani poprawności zdekodowanej telemetrii. Nie produkuje także nowego wyniku skuteczności, dopóki nie ma przyszłych etykiet.

## Stan wykonania

Implementacja jest zarejestrowana i wdrożona. Końcowy zestaw przeszedł **108 testów bez błędów i pominięć**: 44 nowego raportera, 12 kontrolera miesięcznego, 5 pilota nowej historii, 12 porównania parowanego i 35 walidatora dokładnych obserwacji. Kontrola importów potwierdziła, że wszystkie 37 załadowanych modułów projektu pochodziło z zamrożonego środowiska, którego tożsamość nie zmieniła się podczas testów. Są to testy oprogramowania, nie 108 nowych obserwacji satelitarnych.

Protokół zapisano **10 września 2026 o 21:56:35 UTC**, przed początkiem kampanii. Pierwsze rzeczywiste uruchomienie zainstalowanej usługi zakończyło się poprawnie o 21:58:35 UTC. Wygenerowało i zapieczętowało raport z zerową liczbą prognoz i ocen — zgodnie ze stanem przed rozpoczęciem kampanii. Wszystkie 14 warunków liczebności i czasu pozostaje niespełnionych; raport nie deklaruje gotowości publikacyjnej ani nowej skuteczności. Niezależna kontrola pakietu i sum źródeł potwierdziła jego integralność.

Timer jest włączony: raport powstaje codziennie o **02:45 UTC od 13 września do 23 października 2026**. Pierwsze zaplanowane wykonanie poprzedza pierwsze zbieranie prognoz o 03:15 UTC, więc również nie będzie jeszcze zawierać ocen. Kolejne raporty analizują ukończone wcześniejsze przebiegi; ostatnie terminy służą zebraniu opóźnionych ocen po końcu okresu 13 września–14 października. Harmonogram nie przyspiesza rzeczywistego upływu miesiąca ani dostępności etykiet.

Usługa nie wykonuje zapytań HTTP i ma dostęp tylko do lokalnych gniazd. Nie uruchamia treningu ani obserwacji. Zamrożone konfiguracja kampanii, dziennik prognoz i protokół istniejącego porównania pozostały niezmienione.

Dowody: [końcowe testy](/home/ubuntu/telemetry-yield/reports/monthly-endpoint-report-v1-release-tests-20260910.xml), [kontrola środowiska](/home/ubuntu/telemetry-yield/reports/monthly-endpoint-report-v1-release-tests-20260910-runtime.json), [zarejestrowany protokół](/home/ubuntu/telemetry-yield/work/monthly-endpoint-report-v1/protocol.json), [pierwszy raport z usługi](/home/ubuntu/telemetry-yield/work/monthly-endpoint-report-v1/reports/20260910T215831638894Z/report.json), [weryfikacja wdrożenia](/home/ubuntu/telemetry-yield/reports/monthly-endpoint-report-v1-20260910/deployment-verification.json) i [sumy kontrolne wdrożonych plików](/home/ubuntu/telemetry-yield/reports/monthly-endpoint-report-v1-20260910/tooling.sha256).

Kod: [moduł raportowania](/home/ubuntu/telemetry-yield/work/operations/monthly_endpoint_report_v1.py) i [testy](/home/ubuntu/telemetry-yield/work/operations/test_monthly_endpoint_report_v1.py). Istniejące [porównanie miesięczne](/home/ubuntu/telemetry-yield/reports/monthly-exact-comparison-v1-20260910/README.md) pozostaje osobnym, niezmienionym doświadczeniem.

Graphify wskazał `PredictionRecord`, `prospective.py`, `readiness.py` i `reconcile_shadow_network`, co pomogło oddzielić dopasowania nakładających się okien w starym raporcie od ścisłego porównania dokładnych obserwacji. Bieżący kod i dzienniki sprawdzono bezpośrednio. Zapytanie miało budżet około 1500 tokenów i pokazało 44 z 585 znalezionych węzłów; nie wykonano nowej ekstrakcji LLM, więc jej koszt wyniósł zero tokenów. Nie jest to koszt całej sesji.

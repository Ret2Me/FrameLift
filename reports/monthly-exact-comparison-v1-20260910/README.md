# Miesięczne prospektywne porównanie prognoz dla dokładnych obserwacji

Zarejestrowano i uruchomiono harmonogram osobnego porównania zamrożonego modelu, dotychczasowych reguł historycznych i nowego wariantu z historią zgodną z ocenianą etykietą. To rozszerzenie walidacji predyktora obok głównej kampanii planera, a nie jej zastąpienie. Nie rozpoczęła się jeszcze właściwa akwizycja miesięczna i nie ma nowych wyników skuteczności.

## Zakres i terminy

[Protokół](/home/ubuntu/telemetry-yield/work/monthly-exact-comparison-v1/protocol.json) zapisano 10 września 2026 o 18:09:30.921083 UTC, przed rozpoczęciem doświadczenia. Zachowano wszystkie 50 satelitów i 13 identyfikatorów stacji z konfiguracji głównej kampanii, a także ten sam okres: od 13 września 00:00 UTC do 14 października 00:00 UTC, 31 dni.

Pierwszy cykl jest zaplanowany na 13 września o 03:15 UTC. Kolejne przypadają codziennie o 09:15, 15:15 i 21:15 UTC oraz ponownie o 03:15. Każdy cykl odczytuje aktualne stacje i przyszłe obserwacje na 24 godziny naprzód. Stacje odłączone, niedostępne lub w trybie testowym są odrzucane przez istniejący walidator. Włączenie 13 identyfikatorów do protokołu nie oznacza, że wszystkie będą dostępne ani że uzyskamy przykłady dla każdego z 50 satelitów.

Po końcu kampanii nie zbiera się nowych prognoz. Harmonogram pozostaje dostępny do 22 października włącznie na późne wyniki i wznowienia; program nie rozpoczyna cyklu od 23 października 00:00 UTC. Wyniki są pobierane w etapach po co najmniej 24, 72 i 168 godzinach od najpóźniejszego końca prognozowanego przelotu w danej partii. Ostatni etap jest głównym porównaniem, wcześniejsze mają znaczenie pomocnicze. Nie są to gwarancje, że każda ocena SatNOGS będzie już ostateczna.

## Co jest porównywane

Metody to `frozen_logit`, `link_last10_2`, `last10_2`, `global` i nowy `reviewed_last10`. Nowy wariant został wybrany na czerwcowych danych rozwojowych, a nie na tych przyszłych wynikach. Nie ma automatycznego dopasowania, kalibracji ani promowania zwycięzcy w trakcie kampanii.

Wersja ta korzysta ze stałego archiwum 47 039 wierszy. Źródłowe bajty historii są wspólne; dotychczasowe metody zachowują szerokie etykiety artefaktów, a nowy wariant wykorzystuje 5570 kwalifikowanych wyników z niezależnie potwierdzonym sygnałem. Wyniki kampanii nie są dopisywane do historii w tej wersji. Jest to kontrolowany test zmiany kwalifikacji historii, nie test adaptacji na bieżących etykietach.

Nowy wynik ma jawne znaczenie: artefakt demodulacji przy niezależnie ocenionym sygnale. Nie jest bezwarunkowym prawdopodobieństwem skutecznego odbioru, testem poprawności CRC ani liczbą nowych próbek telemetrii. Odrębnego przewidywania obecności sygnału nie zmieniono. Nie przenosimy tu wyniku 84,1% z archiwum jako obietnicy przyszłej skuteczności.

## Wejścia i czas prognozowania

Istniejący kolektor odczytuje przyszłe obserwacje anonimowo z SatNOGS. Archiwizuje odpowiedź, TLE i profil stacji, przelicza geometrię dla dokładnego okna obserwacji i używa najnowszego przechwyconego pliku prognozy środowiska głównej kampanii. Plik starszy niż 30 godzin jest odrzucany przed rozpoczęciem akwizycji.

Zmiany TLE lub profilu stacji mogą pojawić się w kolejnych odczytach. Nowy predyktor historyczny nie używa pogody ani parametrów RF; ich obecność w wejściu modelu odniesienia nie oznacza wykazanego wkładu tych cech do poprawy. Zysk anteny i temperatura szumowa nadal nie są wymyślane na podstawie samego typu anteny.

Wszystkie metody zachowują tę samą tożsamość obserwacji, stacji, satelity, nadajnika i dokładne okno czasowe. Prognoza nowego wariantu otrzymuje rzeczywisty późniejszy czas zapisu. Nie jest cofana do czasu wcześniejszej prognozy modelu. Minimalne wyprzedzenie godziny jest sprawdzane ponownie po zapisaniu nowych prognoz; opóźnione zapisy są wyłączane ze wspólnego porównania.

Ta partia nowych prognoz powstaje dopiero po ukończeniu odczytu stacji przez nadrzędny kolektor. Część bardzo bliskich przelotów może przez to utracić wymagane wyprzedzenie, choć model odniesienia został zapisany wcześniej. Wyłączenie jest wyłącznie czasowe, nie zależy od procentu ani wyniku odbioru. Zarejestrowane ograniczenie należy uwzględnić przy interpretacji pokrycia.

## Powtarzające się obserwacje i późne wyniki

Cykle sześciogodzinne mają nakładające się horyzonty. Jedna obserwacja może więc dostać kilka prognoz. Główne porównanie wybiera najwcześniejszy kwalifikowany zapis nowego wariantu dla danego identyfikatora, zanim sprawdzi dostępność etykiety. Późniejsza prognoza nie zastępuje wcześniejszej dlatego, że ma znany lub korzystniejszy wynik.

Każdy etap oceny zachowuje surowe odpowiedzi oraz ich sumy kontrolne. Istniejący walidator ponownie wyprowadza etykiety z odpowiedzi, sprawdza źródło, czas odbioru oraz dokładną zgodność obserwacji. Wynik zebrany po 24 godzinach nie może być zaliczony do etapu tygodniowego tylko przez zmianę nazwy katalogu: raport sprawdza rzeczywisty znacznik czasu otrzymania etykiety.

Brak oceny, usunięcie zadania, zmienione okno lub niezgodna obserwacja nie są etykietą porażki. Etapy 24/72/168 godzin są raportowane osobno. Każda metoda w danym porównaniu jest oceniana na tej samej liście znanych etykiet.

## Wznowienie i zasoby

Cały cykl jest chroniony blokadą procesu. Ponowne uruchomienie w tym samym sześciogodzinnym przedziale nie tworzy kolejnej ukończonej partii. Częściowe, nieukończone próby pozostają na dysku; następny cykl może wykonać nową próbę z nowym rzeczywistym czasem. Nie są nadpisywane ani zaliczane jako ukończone prognozy.

Jeśli pobranie i weryfikacja wyników zakończyły się, lecz awaria nastąpiła przed zapisaniem ostatniego znacznika, program odtwarza zakończenie z kompletnej próby. Nie pobiera ponownie wyniku wyłącznie dlatego, że brakuje znacznika. Ponawianie nie jest uzależnione od etykiety sukces/porażka.

Usługa ma limit jednej jednostki CPU, 2 GiB pamięci, 64 zadań i pięciu godzin na uruchomienie oraz obniżony priorytet. Klient używa współdzielonego ograniczenia anonimowych odczytów do odstępu co najmniej 61 sekund i jednej równoczesnej operacji. Obowiązuje istniejący limit dziesięciu stron odpowiedzi na stację. Przekroczenie limitu oznacza nieukończoną próbę, nie ukryte obcięcie populacji.

Nie używa się tokenu API, nie odczytuje uwierzytelnionego `/jobs/`, nie wysyła zleceń obserwacji ani nie zmienia stacji. Inne badania i główny planer nie zostały zmodyfikowane. W tej turze nie było nowych żądań HTTP; wykonano rejestrację i próbę przed startem.

## Testy i stan wdrożenia

Przeszło 12 przypadków kontrolera miesięcznego, pięć przypadków nowego pilota oraz 30 przypadków wcześniejszego predyktora i adaptera — łącznie 47 w jednym uruchomieniu, 39,38 s. Kontrola importów wykazała 31 modułów projektu wyłącznie z zamrożonego środowiska i jego niezmienność. Raport: [testy](/home/ubuntu/telemetry-yield/reports/monthly-exact-comparison-v1-expanded-tests-20260910.xml), [kontrola środowiska](/home/ubuntu/telemetry-yield/reports/monthly-exact-comparison-v1-expanded-test-guard-20260910.json).

Sprawdzono m.in. brak HTTP przed startem, pojedynczą partię na przedział czasowy, odrzucanie zbyt starych lub przyszłych prognoz pogody, odzyskiwanie ukończonej próby po przerwaniu zapisu, zachowanie częściowych prób, niezmienność plików i brak podmieniania wcześniejszych prognoz na późniejsze z dostępną etykietą.

Harmonogram został zainstalowany i jest aktywny, oczekujący na 13 września 03:15 UTC. Rzeczywiste próbne uruchomienie usługi 10 września o 18:11:41–18:11:44 UTC zakończyło się powodzeniem; zgodnie z protokołem przed startem nie utworzyło partii akwizycji. Nie oznacza to sprawdzenia przyszłej dostępności SatNOGS ani ukończenia kampanii.

Początkowy [raport](/home/ubuntu/telemetry-yield/work/monthly-exact-comparison-v1/reports/20260910T181104152737Z/report.json) zawiera zero ukończonych partii, zero ocenionych wyników i puste miary skuteczności. `tooling.sha256` wiąże wykonywany kod, jednostki i zarejestrowany protokół.

## Granice wyniku publikacyjnego

Pomocnicze progi porównania obejmują 30 dni od początku, zakończenie okresu, co najmniej 25 dni zbierania, 100 warunkowych etykiet, 20 satelitów, pięć stacji i po 15 dodatnich oraz ujemnych wyników. Wymagane jest również pozyskanie końcowego etapu dla wszystkich wybranych prognoz. To progi kompletności tego porównania, nie zastępstwo wszystkich bramek głównej kampanii.

Nawet ich spełnienie nie potwierdzi, że nasz miesięczny harmonogram został wykonany i przyniósł więcej unikalnych danych. Oceniane są obserwacje naturalnie zlecone w SatNOGS. Przyczynowy test uzysku nadal wymaga uprawnienia do sterowania konkretnymi stacjami i informacji o rzeczywistych blokerach. Także przewaga na nieocenionych przelotach, poprawa na nowych stacjach i rzeczywisty wkład danych antenowych pozostają odrębnymi wymaganiami badawczymi.

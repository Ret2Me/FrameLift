# Zamrożona kampania archiwalnych OGG CANVAS — projekt techniczny v1

Stan: projekt po małym pilotażu i przed uruchomieniem kampanii. Dokument nie
stanowi wyniku badania ani polecenia rozpoczęcia pobierania. Kontraktem analizy
jest [plan zamkniętego tygodnia](/home/ubuntu/telemetry-yield/reports/ogg-archive-week-preanalysis-v1.md).
Nie zmieniamy zamrożonego odbiornika ani zamkniętego raportu małej walidacji.

## 1. Dokładna kohorta, bez dobierania sukcesów

Zakres według **początku** obserwacji: `[2026-08-31T00:00:00Z,
2026-09-07T00:00:00Z)`. CANVAS: NORAD 68635, nadajnik
`GCmN6RULea8dAT7Qoat8z2`, zadeklarowane GMSK 9600. Sortowanie malejące po
znormalizowanym czasie początku, następnie po liczbowym ID; najwyżej 100
obserwacji. Wykluczenia: 14366383, 14115025, 14956101, 14936397.

Funkcja wyboru nie używa `status`, `demoddata`, liczby ramek, dostępności
`payload`, waterfall ani wyników odbiornika. Brak OGG nie usuwa ID z kohorty.
Jeżeli tydzień zawiera mniej niż 100 pozycji, nie rozszerzamy dat. Po zamrożeniu
nie zastępujemy pozycji niedostępnych lub trudnych kolejnymi.

Przygotowanie zapisuje surowe odpowiedzi API, URL, czas i nagłówki odpowiedzi,
SHA-256, pełną listę kandydatów i ostateczną kolejność. Nie wolno zakładać, że
posortowanie każdej strony osobno daje globalną kolejność. W v1 najprostsza
bezpieczna metoda to przejście całej dostępnej paginacji nadajnika w tym tygodniu do końca,
w granicach limitu stron i bajtów, a dopiero potem globalne filtrowanie i
sortowanie. Osiągnięcie limitu przed końcem paginacji oznacza niekompletne
przygotowanie, nie zamrożenie pierwszego wygodnego fragmentu.

Oficjalne [filtry API](https://gitlab.com/librespacefoundation/satnogs/satnogs-network/-/raw/master/network/api/filters.py)
definiują `start` jako dolną granicę początku i `start__lt` jako górną
wyłączną granicę początku. `end` dotyczy końca obserwacji, więc nie jest
zamiennikiem. [Widok API](https://gitlab.com/librespacefoundation/satnogs/satnogs-network/-/raw/master/network/api/views.py)
używa kursora i nagłówka Link. Każdy kolejny URL musi zachować dokładny
nadajnik oraz obie granice; limit przygotowania wynosi 300 stron / 128 MiB.
Mimo ograniczenia serwerowego ponownie weryfikujemy metadane lokalnie.
Sto najnowszych obserwacji może obejmować tylko część tygodnia i wielokrotne
odbieranie tych samych transmisji przez różne stacje. Raport musi podać
rzeczywisty zakres dat i stacji, nie nazywać próby pełnym pokryciem tygodnia.

Powtarzające się ID o identycznych metadanych są deduplikowane; sprzeczne
migawki tego samego ID lub cykl paginacji zatrzymują przygotowanie. API jest
żywe: raport opisuje odczytaną migawkę, nie transakcyjny snapshot całej bazy.
Brak znajomości wyników kandydata nie zmienia tego ograniczenia.

## 2. Co można zachować, a czego nie wolno po prostu zapętlić

| Element | Do ponownego użycia | Ryzyko obecnego pilotażu |
|---|---|---|
| `scripts/ogg_positive_pilot.py` | Pobranie metadanych i artefaktów, float32 WAV bez resamplingu | Wymaga dodatnich referencji; istniejący plik jest uznawany za cache bez ponownej walidacji; brak kompletnego wznowienia |
| `validation-inputs/acquire_validation.py` | Jawne identyfikatory nadajnika, zapis API i pochodzenia | Filtr `status=good`, próbki dodatnie i wybór dwóch dat są niedopuszczalne dla nowej kohorty |
| `scripts/ogg_refinement_pilot.py` | Niezmienny odbiornik fast/diverse, 6 s / 3 s, oryginalne FCS, dziennik okien | Jedna próba na nowy plik, brak wznowienia; trzeba sprawdzić błędy okien, a nie sam kod wyjścia |
| `baseline/ogg_baseline_run.py` | gr-satellites 5.9.0, jawne FSK 9600 / G3RUH, brak wysyłania telemetrii | Etykieta reprezentacji nie wymusza trybu; brak KISS może wyglądać jak pusty wynik; niepełny proces nie jest zerem |
| `scripts/ogg_pilot_report.py::compare_one` | Ścisłe porównanie pełnych PDU na identycznym PCM, powtórna CRC | Wymaga obecnego WAV; sumuje unikalność per obserwacja, nie globalną; potrzebny zewnętrzny znacznik kompletności referencji |

Nowy `scripts/ogg_archive_campaign.py` ma orkiestrację, a nie nowy dekoder.
Stare skrypty i źródła pozostają niezmienione. Nie wolno odtwarzać w nowym
runnerze łagodniejszej wersji `compare_one` ani dopisywać FCS do referencji
pozbawionych oryginalnej FCS i nazywać tego jej niezależną walidacją.

## 3. Dwa oddzielne etapy

`prepare` pobiera wyłącznie ograniczone metadane. Przed dekodowaniem zapisuje
niezmienny plan, dobór ID, pełne metadane, kontrakt reprezentacji, limity,
wersje oraz hashe źródeł, interpreterów, modułów i programów zewnętrznych.
Nie pobiera dużych OGG, nie uruchamia demodulatorów i nie ocenia ramek.

`run` wymaga planu i jego zgodnych źródeł. Działa sekwencyjnie, z blokadą
wykluczającą dwa równoczesne koordynatory. Każde ID dostaje własny katalog,
a każda ponowiona próba nowy numer. Nie ma ponownego dobierania kohorty,
optymalizacji parametrów ani ścieżki wysyłającej ramki do sieci.

Przebieg jednej obserwacji:

```text
zamrożone metadane
  → oryginalne OGG + referencje
  → własny tymczasowy WAV + jego SHA-256
  → native fast/diverse oraz komponentowy baseline na tym samym WAV
  → kontrola kompletności i compare_one
  → trwały raport porównania + kandydaci do wtórnego audytu
  → kontrolowane usunięcie tylko własnego WAV
```

Dane referencyjne trafiają wyłącznie do scorerów. Polecenie odbiornika
otrzymuje jedynie ścieżkę WAV i zamrożone parametry. Sam brak parametru z
referencjami nie jest deklaracją kryptograficznie izolowanego ślepego testu.

## 4. Stany, błędy i wznowienie

Stan obserwacji, stan referencji i stan każdego procesu muszą być osobnymi
polami. Przykładowe stany terminalne: `complete`, `ogg_absent_in_snapshot`,
`ogg_unavailable`, `unsupported_audio`, `reference_incomplete`,
`native_failed`, `baseline_failed`, `score_failed`, `interrupted`.
`disk_pause` oraz `source_drift` zatrzymują kampanię; nie zamieniają
pozostałych pozycji na zera. Mianownik zamrożonych ID nigdy się nie zmienia.

Puste, **kompletne** wykonanie to prawdziwe zero. Błąd jednego okna natywnego,
niezerowy kod wyjścia, timeout, ucięty JSON, niepoprawny KISS lub brak
wymaganego artefaktu nie stanowią poprawnego pełnego wykonania. Zachowujemy
ewentualne poprawne ramki z częściowej próby jako diagnostykę, nie mieszamy
ich z głównym porównaniem ukończonych prób.

Wznowienie sprawdza hashe, schematy i kompletny zestaw artefaktów. Istnienie
pliku nie jest potwierdzeniem sukcesu. Nie nadpisujemy poprzednich prób.
Nie uruchamiamy następnego demodulatora, jeżeli zweryfikowany proces starej
próby nadal działa. Crash koordynatora musi zostawić identyfikator procesu,
jego start oraz katalog próby, aby uniknąć równoległego, osieroconego dekodowania.

Stan i raporty są publikowane atomowo po zapisie i `fsync`; proces zapisuje
do własnego katalogu próby. Raport obserwacji jest zatwierdzany dopiero po
sprawdzeniu wyników obu metod i wymaganych zależności. Bezpieczny ponowny
odczyt zatwierdzonego wyniku nie uruchamia ponownie odbiorników.

## 5. Kompletność referencji i definicja przyrostu

Nieobecność PDU w archiwum wolno stwierdzić dopiero po pobraniu **każdego**
obiektu referencyjnego z zamrożonej listy. Błąd pobrania choć jednego obiektu
blokuje liczniki `native_new_vs_archive` i
`native_new_vs_archive_and_baseline`; ich wartość jest niedostępna, nie zero.
Wynik baseline kontra native może być raportowany osobno, jeśli oba procesy
są kompletne. W pierwszym runnerze dopuszczalne jest bardziej konserwatywne
wstrzymanie całego porównania do uzupełnienia referencji.

Pusta lista referencji w kompletnych metadanych oznacza pustą migawkę
archiwalną, ale nie dowód braku transmisji. Brak pola listy, uszkodzona
metadana lub niedostępne obiekty to inny stan. Obiekty takie jak `ffff` są
zachowane i opisane jako niepoprawne strukturalnie, a nie zaliczone jako
telemetria. Każdy oryginalny obiekt pozostaje na dysku.

Dla obserwacji `i`: `A_i` to kompletna migawka poprawnych strukturalnie PDU,
`B_i` to pełne PDU baseline, `N_i` to pełne PDU native po niezależnym
odtworzeniu otrzymanej FCS i ścisłej strukturze. Żadnych porównań prefiksów,
podciągów, przybliżonej zgodności ani domyślnego odcinania nagłówków.

- Główny licznik użytkowy: `N_i − A_i` (`native_new_vs_archive`).
- Równolegle: `N_i − B_i`, `B_i − N_i`, `(N_i − A_i) ∩ B_i` oraz
  `N_i − (A_i ∪ B_i)`.
- Mianowniki: wszystkie wybrane ID, OGG dostępne, obie metody ukończone,
  kompletne referencje, obserwacje z dodatkowymi PDU; braków nie ukrywamy.
- Suma per obserwacja liczy parę `(observation_id, pełne bajty PDU)`.
- Globalne unikalne bajty: suma długości elementów unii pełnych PDU w
  kohorcie. Osobno rozróżniamy `∪(N_i−A_i)` (unikalne PDU dodane lokalnie)
  oraz `(∪N_i)−(∪A_i)` (PDU nieobecne nigdzie w kompletnej migawce kohorty).
  Te zbiory nie są równoważne. Globalna nieobecność w całej kohorcie wymaga
  kompletnych referencji dla całej kohorty, także obserwacji bez OGG.

Nowa ramka jest **kandydatem** do wtórnego audytu: ponowna CRC z oryginalnej
FCS, replay z zachowanego OGG/WAV i ocena źródła. Poprawne adresy ani wybór
obserwacji CANVAS nie dowodzą samodzielnie, że nadajnikiem był CANVAS.
Wyjścia nie są automatycznie przypisywane misji ani wysyłane do baz.

## 6. Sieć, pamięć i około 6 GB wolnego dysku

Odczyt lokalny przy projektowaniu: 6 621 364 224 B wolnego miejsca,
czyli około 6,17 GiB. To migawka, nie rezerwacja zasobów. Sprawdzamy miejsce
przed każdym pobraniem, konwersją i uruchomieniem procesu oraz w trakcie pracy.

- Pobieranie strumieniowe z limitem faktycznie odebranych bajtów; nie ufamy
  wyłącznie `Content-Length`. Ograniczamy liczbę stron, łączną metadanych,
  referencji i OGG; pojedyncze OGG najwyżej 64 MiB / 30 minut.
- Jawna lista hostów HTTPS, kontrola także przekierowań i odnośników
  paginacji, timeouty, ograniczone retry, respektowanie `Retry-After`.
  Publiczne zapytania bez tokenów; jeden aktywny transfer w runnerze.
- Jedna obserwacja i jeden dekoder naraz; brak cache całej kampanii w RAM.
  48 kHz mono float32 zajmuje około 192 kB/s, więc 30 minut to około
  345,6 MB WAV plus mały nagłówek. Brak resamplingu i niejawnego downmixu.
- Rezerwa wolnego miejsca co najmniej 1,5 GiB; docelowy limit zachowanych
  artefaktów kampanii 3 GiB, bufor pojedynczego WAV 512 MiB. Limity logów
  i rezultatów są osobne. Budżet sprawdzamy względem sumy rezerw, nie osobno
  dla kilku procesów, które mogłyby równocześnie zużyć to samo miejsce.
- Cztery znane OGG miały około 3,7–10,3 MB, ale nie zakładamy tego dla
  kohorty. Sto OGG po maksymalnych 64 MiB przekroczyłoby dostępny dysk.
  Przy takim przypadku zatrzymujemy kampanię, zachowując wszystkie ID i
  już pobrane oryginały. Nie usuwamy OGG, żeby sztucznie osiągnąć 100.

## 7. Usuwanie wyłącznie odtwarzalnego własnego WAV

Scorer `compare_one` musi działać **przed** usunięciem WAV. Zatwierdzony
raport przechowuje SHA-256 WAV, rozmiar, liczbę próbek, polecenie konwersji,
tożsamość ffmpeg/libsndfile, SHA-256 OGG i identyfikator własnej generacji.

Kasowanie wolno wykonać tylko dla konkretnego pliku utworzonego przez tę
próbę pod `campaign/observations/<ID>/attempt-NNN/audio.wav` (stała nazwa
wymagana przez niezmieniony `compare_one`). Sprawdzamy zwykły
plik, brak symlinków w ścieżce, właściciela, inode/device i zgodność SHA-256.
Nie akceptujemy dowolnej ścieżki podanej w obcym JSON. Operacja dotyczy
jednego pliku, nigdy katalogu rekurencyjnie. OGG, referencje, manifesty,
surowy KISS, pełne FCS, dzienniki i wyniki pozostają.

Przed usunięciem zapisujemy intencję; po nim potwierdzenie. Crash między
tymi zapisami rozpoznaje się po ważnym porównaniu i braku WAV, bez ponownego
dekodowania. Do wtórnego audytu WAV jest regenerowany z oryginalnego OGG
przy tej samej wersji konwersji; musi mieć ten sam hash. Nie udajemy,
że konwersja stratnego OGG odtwarza oryginalne RF IQ.

## 8. Testy blokujące start i konkretne ryzyka

1. Zmiana `status`, ramek i dostępności OGG nie zmienia wyboru ID; granice
   tygodnia, timezone, cztery wykluczenia, globalne sortowanie i limit 100.
2. Cykl paginacji, pominięty filtr API, sprzeczne powtórzone ID, limit stron
   przed EOF, brak pola referencji i HTTP 429/404/503 dają jawne stany.
3. Połowiczny download, fałszywy `Content-Length`, redirect poza hosty,
   istniejący uszkodzony cache i niezgodny SHA nie przechodzą jako sukces.
4. Przerwanie po każdym przejściu stanu, blokada dwóch koordynatorów,
   częściowy wynik, żyjący stary proces i wznowienie zatwierdzonego wyniku.
5. Jeden brakujący reference blokuje wszystkie twierdzenia o nieobecności
   w archiwum; puste kompletne referencje nie są oznaczonym szumem.
6. Timeout, brak KISS, malformed KISS, błąd jednego okna, zmiana źródeł
   i uszkodzona FCS są błędami, a nie zerowym wynikiem.
7. Identyczne PCM w obu ramionach; pełna FCS zostaje tylko w natywnym
   dowodzie, a jednostka porównania zachowuje wszystkie nagłówki PDU.
8. Powtarzana ramka w dwóch oknach i dwóch obserwacjach sprawdza różnicę
   deduplikacji lokalnej/globalnej oraz różnicę obu globalnych przyrostów.
9. Niski dysk przed/po downloadzie, log przekraczający limit i brak miejsca
   w konwersji pauzują lub kończą próbę jawnie, bez kasowania oryginałów.
10. Podstawiony symlink, obcy WAV, inny inode/SHA, niezapisany scorer oraz
    crash po usunięciu WAV nie prowadzą do skasowania innego pliku.
11. Regeneracja WAV daje identyczny hash; ponowne wykonanie zatwierdzonej
    obserwacji niczego nie pobiera i nie uruchamia ponownie.
12. Test zerowej liczby wybranych obserwacji: poprawny raport pustej
    kohorty, bez `rows[0]`, bez rozszerzania tygodnia i bez dzielenia przez zero.

Przed startem wymagane są nowe testy runnera, niezależny audyt i decyzja
koordynatora. Istniejące 90 syntetycznych okien / 540 s bez fałszywej ramki
to kontrola podstawowa; nie dowodzą niskiego FAR dla stacji. Ta kohorta
retrospektywna jednej misji nie daje automatycznie publikowalności ani
gotowości produkcyjnej, niezależnie od wielkości uzyskanego przyrostu.

## 9. Kwalifikacja implementacji przed niezależną decyzją o starcie

Nowy runner jest zapisany; żadna kampania nie została przez jego autora
uruchomiona. 46 własnych testów offline oraz 9 niezależnych reproduktorów
przeszło; szerszy istotny zestaw osiągnął 129 testów i 42 podtesty bez błędów.
Testy sterownika tworzą jawnie sztuczne artefakty i nie są wynikami naukowymi.

Audyt naprawił cztery błędy istotne dla wyniku: wiązanie `commit.final` z
dokładną ścieżką faktycznie czytanego pliku, zgodność baseline JSON z
oryginalnym KISS w obie strony, zgodność ramek/proweniencji natywnych z
licznikami każdego okna. Oryginalna CRC jest dodatkowo sprawdzana przez
`binascii.crc_hqx` z odbiciem bitów, niezależnie od pętli CRC scorera.

Wykrywanie osieroconych procesów uwzględnia boot ID i minimalny czas
startu przed `Popen`. Nieczytelne środowiska starszych procesów nie blokują
tej kampanii; nowy, wciąż żywy i nieczytelny proces blokuje. Wyścigi wyjścia
do stanu zombie i zniknięcia są ponownie sprawdzane. Znany własny proces
jest zatrzymywany i zbierany również wtedy, kiedy audyt innych potomków
jest zablokowany. Nie jest to dedykowany cgroup ani pełny sandbox systemowy.

Lokalny self-check tożsamości zamroził i ponownie sprawdził 3342 pliki
(około 440 MB): nadmiarowo cały kod projektu i satellites, pliki dystrybucji
NumPy/SciPy/SoundFile oraz moduły i biblioteki współdzielone faktycznie
załadowane przez import środowiska baseline. Obejmuje to `camras_replay.py`
i wszystkie projektowe zależności CCSDS, a nie tylko skrócony wcześniejszy
plan walidacji. Środowisko baseline zgłasza GNU Radio 3.10.12.0 i NumPy
2.5.2. Jest to zapis tożsamości runtime, nie odtworzenie całego systemu OS.

Po każdym ukończonym ID powstaje niezmienny checkpoint w `summaries/`.
Wznowienie domyślnie pomija zweryfikowane ukończone próby, także błędne;
`run --retry-failed` tworzy nową numerowaną próbę, maksymalnie trzy na ID.
Nie zmienia to kohorty. Przerwane, niezatwierdzone próby pozostają na dysku.
`prepare` i `run` są nadal oddzielnymi poleceniami, a start wymaga końcowej
kontroli koordynatora, w tym canary rzeczywistych programów na znanym audio.

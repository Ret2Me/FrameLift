# Bezstratna optymalizacja pełnego dekodera progresywnego — 2026-09-11

Status pomiaru: ukończony. Cztery pary, osiem pełnych uruchomień; wszystkie
deterministyczne wyniki identyczne. Łączny czas ścienny krótszy o 25,57%,
czas CPU o 27,71%; pojedyncze czasy ścienne nie poprawiły się jednak zawsze.

## Zakres

Implementacja istniejącego banku FSK/GMSK 9600 → AX.25 UI z odebranym FCS.
Bez zmiany listy zadań, granic okien, filtrów, rankingu zegara, progów, modeli,
wyboru kotwic, reguł CRC ani arytmetyki f64. Nie dodano odrzucania hipotez,
AI-selekcji fragmentów, fast-math, mniejszej precyzji ani wcześniejszego kończenia.
Wyniki dotyczą ukończonego trybu `full`, nie gwarancji tego samego prefiksu
zadań przy sztywnym limicie czasu w `quick`/`deep`.

Graphify rozpoznano i odczytano jego pamięć: aktualne pytania o Rust są znaną
ślepą ścieżką starego grafu. Zgodnie z tą wskazówką użyto aktualnych źródeł,
bez ponownego wywodzenia zależności ze starego indeksu Pythona.

## Zmiany

1. Cache dokładnego przygotowania okna: dwa frontendy i pełny bank zegara
   są współdzielone przez etapy tego samego uruchomienia. `OnceLock` zapewnia
   pojedyncze przygotowanie dopuszczonego okna. Cache nie zawiera modeli,
   kotwic ani wyników dekodowania; generacje kotwic pozostają rozdzielone.
   Każde użycie sprawdza SHA256 wszystkich bitów PCM, sample rate i baud.
   Filtrowanie nadal zaczyna się od tej samej granicy okna.
2. Do 512 MiB deterministycznych rezerwacji cache, ograniczonych też zapasem
   dotychczasowego oszacowania 6 GiB. Rzeczywiste pojemności buforów są sprawdzane
   przed zatrzymaniem wyniku. Okna bez rezerwacji są przeliczane, nigdy pomijane.
   Cache jest procesowy, nie trwały między restartami. Rezerwacja nie jest
   twardym limitem całkowitego RSS. Obecny wariant nearest kopiuje przygotowane
   próbki do dotychczasowego kontenera; jest to pozostała możliwość optymalizacji.
3. `SequenceWorkspace`: ponowne użycie tablic traceback i decyzji pomiędzy
   kolejnymi hipotezami. Aktywne komórki są nadpisywane; nie są ponownie używane
   stare decyzje. Zachowano interfejs zwracający własny `Vec`, dokładne działania,
   kolejność walidacji oraz rozstrzyganie remisów.
4. Zapis checkpointów/podsumowań przez bufor 64 KiB. Jawne opróżnienie bufora
   poprzedza fsync, atomową publikację i fsync katalogu. Nie usunięto żadnej
   sumy kontrolnej ani gwarancji niezastępowania ukończonych zadań.

Nie zmieniono zamrożonych binariów ani protokołu badania prospektywnego.
Nowy program nie wznawia sesji starego binarium pod inną tożsamością.

## Weryfikacja kodu

- Cache: zgodność dokładnych bitów frontendów i pełnych banków zegara;
  obu części bazowego banku; braku, jednej i wielu kotwic; wariantów blind,
  nearest i multi. Sprawdzono także zmianę PCM (w tym znak zera), baud,
  sample rate, brak kotwic i niepoprawne próbki.
- Workspace: 2520 poprawnych przypadków porównanych z kopią starej implementacji,
  32 błędne wejścia, zatruta pamięć i stabilność przydzielonych buforów.
  11 testów przeszło, mikrobenchmark pozostaje jawnie ignorowany w zwykłym zestawie.
- Zapis: identyczne bajty JSON, brak nadpisania commitów i brak publikacji
  częściowego wyniku przy błędzie serializacji po zapisaniu większej porcji danych.
- Niezależny komparator sesji: 7 testów; sterownik pomiaru: 1 test z mutacjami
  tożsamości, list zadań/ramek i pustego banku. Wszystkie przeszły.
- CLI progressive: 2/2 testy przerwania, wznowienia i zachowania dodatniej kontroli.
- 5/5 testów modułu progressive przeszło, w tym kontrola awarii serializacji.

Pierwszy szeroki przebieg testów (301 pass, 19 fail, 6 ignored) został zakłócony
przebudową uruchomionego pliku testowego przez równoległe cargo. Błędy dotyczyły
ponownego otwierania/uruchamiania własnego pliku (`No such file or directory`).
Ten przebieg nie jest uznany za zaliczony. Powtórzenie korzysta z osobno
skopiowanego, nieprzebudowywanego pliku testowego: **321 pass, 0 fail,
6 jawnie ignored**, 575,34 s (327 testów). Wraz z osobnymi testami komparatora,
sterownika i CLI pokrywa to także regresje poza samym cache.

## Metoda pomiaru

Dwa już wykorzystane rozwojowo nagrania CANVAS: 14967362 (686,039 s) oraz
14967393 (588,356 s). Te same zachowane WAV float32 uzyskane wcześniej z OGG;
bez nowego pobierania, zmiany próbek ani wyboru nowych danych według wyniku.
Dwa powtórzenia, naprzemienne AB/BA: 4 pary i 8 pełnych uruchomień.
Każda wersja ma 2 wątki, CPU 4–5. Host współdzielony; inne zadania i wpływ
wspólnej pamięci/dysku nie są wykluczone. Szerokie testy przypisano do CPU 0–1.

Obie wersje odbiornika zbudowano tym samym Cargo profilem release, Rust 1.98.1,
thin LTO i codegen-units=1. To porównanie zmian implementacji, a nie porównanie
nowych ustawień kompilatora z dawnym pomiarem 328 s na 20 nagraniach.

Pomiar obejmuje cały proces odbiornika; konwersja OGG jest poza nim. Czas ścienny,
CPU użytkownika/systemu i maksymalny RSS są oddzielne. Haszowanie sterownika
i niezależne porównanie są poza pomiarem symetrycznie dla obu wersji.
Komparator sprawdza kompletny bank zadań, ich sumy, odebrany FCS i wszystkie
deterministyczne szczegóły. Pomijane są tylko `elapsed_seconds` i tożsamość sesji.
Różne SHA256 wykonywalnych wersji są jawnie przypięte i niezależnie sprawdzane.
Nie wystarcza taka sama liczba ramek. Błąd/przerwanie nie jest wynikiem zero.

## Ukończony pomiar — wszystkie pary

AB oznacza przed → po, BA po → przed. CPU to użytkownik + system.
W kolumnie zmiany czasu wartość dodatnia oznacza krótsze wykonanie.

| Nagranie / powtórzenie | Kolejność | Czas przed → po [s] | Krócej | CPU przed → po [s] | RSS przed → po [KiB] | Zadania / ramki w każdej wersji |
|---|---|---|---|---|---|---|
| 14967362 / 1 | AB | 450,288 → 272,405 | 39,50% | 457,63 → 363,30 | 32628 → 425288 | 1596 / 4 |
| 14967393 / 1 | BA | 346,517 → 208,067 | 39,95% | 546,74 → 369,96 | 36132 → 435516 | 1372 / 20 |
| 14967362 / 2 | BA | 225,810 → 182,763 | 19,06% | 434,08 → 342,15 | 32496 → 427124 | 1596 / 4 |
| 14967393 / 2 | AB | 296,645 → 318,709 | −7,44% | 551,71 → 363,37 | 34376 → 434048 | 1372 / 20 |

Łączny czas ścienny: **1319,260 → 981,945 s**, czyli **25,57% krócej (1,344×)**.
Łączny czas CPU: **1990,16 → 1438,78 s**, czyli **27,71% mniej**.
To stosunek sum, nie średnia procentów. Maksymalny RSS nowej wersji wyniósł
435516 KiB (425,31 MiB), wobec maksymalnie 36132 KiB (35,29 MiB) starej.
Jest to świadoma wymiana pamięci za uniknięcie powtarzania obliczeń.

W każdej parze niezależne porównanie nie znalazło różnicy w żadnym
deterministycznym szczególe, zadaniu ani odebranej ramce. Nie tylko ich liczba,
ale również zawartość i walidacja FCS pozostały identyczne.
Surowy komparator celowo nadal pokazuje `status: different`, ponieważ domyślnie
wymaga tego samego binarium. Jedyną różnicą tożsamości jest jawnie oczekiwany
SHA256 programu; raport sterownika dopuszcza dokładnie tę przypiętą zmianę.

Niezależny agent powtórzył porównanie bez uruchamiania DSP: raport komparatora
jest identyczny bajtowo, a tożsamości wejścia, programów i pomiarów ponownie
sprawdzone. Audyt: `work/progressive-speed-20260911-v1/r0-i0-independent-audit.md`.
Nie jest to audyt pozostałych, wówczas jeszcze nieukończonych par.

Pierwsza para częściowo nakładała się czasowo z szerokim zestawem testów na
innych vCPU; drugie powtórzenie wykonano już po zakończeniu tego zestawu.
Wpływ współdzielonego dysku/pamięci/hosta nie jest wykluczony.
**Ostatni przebieg nowej wersji trwał o 7,44% dłużej**, mimo niższego czasu CPU.
Nie ustalono osobno przyczyny tego rozjazdu. Wyniki nie dowodzą przyspieszenia
każdego uruchomienia ani gwarantowanych 40%; potrzebne są dalsze powtórzenia
na kontrolowanym hoście do mocnego wniosku o opóźnieniu.

## Tożsamości artefaktów

Katalog: `/home/ubuntu/telemetry-yield/work/progressive-speed-20260911-v1/`.

- Końcowy raport maszynowy: `paired/summary.json`,
  SHA256 `079a8619c5b3f08a55f7c743f92f6d3289c1a3f468f78e72d203395c2f8834d2`.
- Migawka źródeł przed zmianami: `before/source.tar.gz`,
  SHA256 `c16330354aa90a74c13a00f37ca7a1ed12b776b06667000bf36d098c60b5fda8`.
- Porównywane stare binarium: `before/matched-build`,
  `846119f79a918d6a3bc72fd8ec4c1f3966b14299ea8c29f09cd04a05ac015048`.
- Nowe binarium: `after/telemetry-yield-rs`,
  `28800c819c3a7862250a44d717edec8682a02a05f30c7a2cae5ee0aee2d2b9b4`.
- Nowa migawka źródeł: `after/source.tar.gz`,
  `347a86425f965a6f408b735463068b843667a4d9f9bad5eaefe5623ce205c796`.
- Komparator: `after/progressive_compare_sessions`,
  `ed0305fe80e1a1982219a02a72173d930324228d39c5771850766f3313923e10`.
- Sterownik: `after/progressive_speed_bench`,
  `384a2c283ab01c24b601739688a8394f199ba801d9fbd95535819beda4e384bf`.
- Stabilny plik pełnych testów: `tests/lib-tests`,
  `82a310282153f590f3f01151fa791b9fd5e2ec72522701d7928fd896304491d0`.

To kontrola równoważności i kosztu na danych rozwojowych, nie dowód nowego
zysku telemetrii, uniwersalnej bezstratności, gotowości publikacyjnej lub produkcyjnej.
Migawki źródeł nie są kompletnym hermetycznym wydaniem: część historycznych
testów odwołuje się do wzorców i tożsamości źródeł Pythona zachowanych w repozytorium.

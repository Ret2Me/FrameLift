# Przyspieszenie odbiornika bez ograniczania przeszukiwania — 2026-09-10

## Zakres i warunek akceptacji

Optymalizacja implementacji istniejącego odbiornika `decode-adaptive-audio`,
nie nowy algorytm demodulacji. Zachowujemy wszystkie banki zegara, frontendy,
warianty Gardnera, modele, progi jakości, wzmocnienia, stany początkowe NRZI,
reguły HDLC/CRC/UI i niezależną weryfikację ramek używanych do uczenia.
Nie stosujemy pomijania hipotez, wcześniejszego kończenia, niższej precyzji,
fast-math ani naprawiania bitów.

Stara wersja wykonywalna jest zachowana w
`work/lossless-speed-20260910-v1/before/telemetry-yield-rs`, SHA-256
`df4f06e030348ffaefaee8895ea802fd04cb205a116a699cf986b8f7ba6a427c`.
Pierwsza zoptymalizowana wersja (v1): `work/lossless-speed-20260910-v1/after/telemetry-yield-rs`, SHA-256
`4bfb6acba341cde6d449168724289bd05eceb1d30d7da4cca2b1140391cd57c0`.
Migawka nowych źródeł `after/source.tar`:
`f862ae4885c1759671128fb73929b8cd0260ee072d5494613b05bb2bbce3b611`.
Końcowa wersja z buforowanym I/O (v2):
`work/lossless-speed-20260910-v1/after-v2/telemetry-yield-rs`, SHA-256
`acf20e51e8cfb2b4e2264757fe8fe8ef61ae6de3d523ac3da6e02f63fb492a97`.
Migawka `after-v2/source.tar`:
`f92333f19ac57dc1ad7a2639636c3baaa3c31f6112090e1acad41264e3e7f714`.
Pierwsza próba migawki `before/source.tar` zgłosiła brak źle wskazanego pliku
testowego; kompletna migawka sprzed zmian to `before/source-complete.tar`.
Żadna z wcześniejszych kampanii badawczych nie została nadpisana.

## Zmiany i argument równoważności

1. `rust/dsp.rs`: niezmienna pożyczka `ValidatedFrontend` sprawdza skończoność
   próbek raz. Każda hipoteza nadal przechodzi dotychczasowe sprawdzenia zakresu,
   fazy, kroku i przepełnienia. Publiczne `soft_symbols` zachowuje walidację,
   kolejność błędów i arytmetykę; wariant `soft_symbols_into` ponownie wykorzystuje
   alokację, nie zmieniając bufora przy błędzie.
2. `rust/adaptive.rs`: drugi przebieg wykorzystuje dokładnie pierwsze osiem
   elementów tego samego pełnego lokalnego banku zegara z pierwszego przebiegu.
   Bank faz przeniesionej szybkości nadal jest wyliczany w całości; kolejność
   i deduplikacja nie zmieniają się.
3. Opcjonalnie przechowujemy wynik filtrowania tego samego wycinka próbek,
   bez zastępowania filtrowania okien filtrowaniem całego nagrania. Przydział
   jest deterministyczny; dla buforów próbek rezerwujemy najwyżej 256 MiB,
   dodatkowo ograniczone zapasem istniejącego oszacowania 6 GiB. Sprawdzamy
   rzeczywistą pojemność `Vec`. Po wyczerpaniu budżetu filtr jest przeliczany,
   a nie pomijany. Drobne metadane banków są dodatkowym narzutem; ani 256 MiB,
   ani oszacowanie 6 GiB nie są twardym limitem całkowitego RSS procesu.
4. `rust/protocol.rs`: współdzielimy NRZI między trybami, łączymy wewnętrzne
   usuwanie dopełnienia HDLC z pakowaniem bajtów i używamy bufora kandydatów.
   Zmiana początkowego poziomu NRZI wpływa wyłącznie na bit 0, a po liniowym
   deskramblerze G3RUH — na bity 0, 12, 17. Oba pełne skany ramkowania pozostają;
   ten fakt nie jest używany do pomijania granic nagrania ani drugiego stanu.
5. Kolejna wersja (v2), odkryta podczas analizy zmierzonych kosztów systemowych:
   buforowanie odczytu WAV i zapisu JSON w `rust/input.rs` (po 64 KiB).
   Parser Hound, arytmetyka konwersji próbek, kontrole plików regularnych,
   wszystkie limity i ponowne haszowanie wejścia pozostają bez zmian.
   Writer jest jawnie opróżniany przed `fsync`, publikacją bez nadpisania
   i synchronizacją katalogu. Dokładne bajty wyjściowe i zachowanie przy błędzie
   serializacji są przedmiotem osobnych testów regresji.

Graphify wskazał starsze schematy i moduły Pythona, nie aktualny Rust.
Decyzje implementacyjne oparto na bezpośrednim odczycie aktualnych źródeł;
nie przypisujemy grafowi nieistniejących zależności.

## Weryfikacja

Testy różnicowe porównują nową implementację ze skopiowanym kodem sprzed zmian,
nie z drugim wywołaniem nowej implementacji. Obejmują 3696 kombinacji brzegowych
interpolacji, dodatkowe przypadki NaN/Inf, pełne 160 hipotez porównane przez bity
f64, 131071 krótkich strumieni HDLC, 1040 konfiguracji prefiksu/polaryzacji ramek,
kontrole pośrednich bitów NRZI/G3RUH, szum, CRC, długości graniczne i tryby.
Zachowano też 515 wcześniej zamrożonych przypadków protokołu z implementacji
referencyjnej. Test pamięci podręcznej porównuje wszystkie szczegóły prób
uzupełniających z ponownym filtrowaniem; test budżetu sprawdza brak pomijania okien.

`cargo test --lib --bins --examples --test cli_integration` zakończył się kodem 0
zarówno dla v1, jak i po końcowej zmianie I/O w v2. Dla v2 **307 testów przeszło,
0 niepowodzeń, 4 jawnie ignorowane testy/fixture'y** (243 testy biblioteki,
13 CLI, 51 przykładów). Dla v1 było 301 testów, także bez niepowodzeń.
Ostatni test sterownika pomiarowego uruchomiono również osobno po jego
uszczelnieniu: 1/1 PASS.

Nowy `adaptive_pcm_probe --rendering clean-rectangular --threads 2` ukończył
cztery przypadki. Wszystkie ćwiczą modele i transfer, łącznie 960 prób
uzupełniających, z czego 384 w obszarach docelowych. Brak utraconych i fałszywych
ramek; wyniki poszczególnych przypadków są identyczne ze starą próbą v2 po
usunięciu tylko pomiarów czasu (sprawdzone przez `diff` kanonicznego JSON).
To kontrola regresji, nie dowód nowego zysku dekodowania ani kalibracja FAR.
Taką samą kontrolę powtórzono również na zamrożonej wersji v2:
`pcm-smoke-v2/result.json`, ponownie pełna zgodność z pierwotnym wynikiem.

## Wstępny pomiar v1 — nie wynik końcowy

Dwie ukończone pary dały pełną zgodność deterministycznego wyniku:

| Obserwacja | Stara wersja — cały proces | v1 — cały proces | Ramki w obu |
|---|---:|---:|---:|
| 14936407 | 94,352 s | 76,287 s | 20 |
| 14936415 | 61,593 s | 47,668 s | 23 |

Po stwierdzeniu braku buforowania odczytu WAV zatrzymano własną grupę procesów
wstępnego benchmarku, aby włączyć tę dodatkową optymalizację. Wyniki ukończone
i częściowy kolejny przebieg zachowano; `paired/stopped.json` opisuje powód.
Nie osiągnięto pierwotnie zaplanowanych dwóch powtórzeń v1, nie utworzono
pozornego podsumowania sukcesu, a częściowego przebiegu nie liczono jako zera
ramek ani regresji. Te dwa pomiary nie zostaną połączone ze statystyką v2.

Końcowy pomiar obejmuje starą wersję względem v2, w nowym katalogu `paired-v2`.

## Końcowy pomiar v2 — ukończone obie serie

**Wszystkie 8 par (16 procesów) ukończono poprawnie.** W każdej parze
deterministyczny JSON jest identyczny. Dla każdego ID ten sam hash powtarza się
we wszystkich czterech wynikach (dwie wersje × dwa powtórzenia), więc zgodność
obejmuje również powtarzalność między uruchomieniami.

Poniżej średnie dwóch powtórzeń, nie wybrane najlepsze czasy:

| Obserwacja | Stara wersja — cały proces | v2 — cały proces | Ramki w obu |
|---|---:|---:|---:|
| 14936407 | 90,172 s | 64,970 s | 20 |
| 14936415 | 61,998 s | 42,268 s | 23 |
| 14936424 | 76,822 s | 54,310 s | 31 |
| 14936444 | 96,665 s | 66,566 s | 38 |
| Średnia | **81,414 s** | **57,028 s** | — |

Łączny czas procesów: 651,314 → 456,228 s. Oznacza to **29,953% krótszy czas**
albo **1,428× większą szybkość przetwarzania** przy tym samym budżecie czterech
wątków. Nie oznacza to przyspieszenia 2× ani dogonienia gr-satellites.

Same etapy dekodera, bez odczytu i obsługi wyniku: średnio 70,429 → 54,515 s.
Pierwszy przebieg z dopasowaniem modeli: 56,754 → 49,456 s; przebieg
uzupełniający: 13,675 → 5,059 s. Ten ostatni zysk obejmuje uniknięcie ponownego
wyliczania lokalnego banku zegara, nie przyspieszenie samej rekurencji Viterbiego.

Łączny czas CPU: 2304,750 → 1746,820 s (**24,208% mniej**).
Zakres maksymalnego RSS w poszczególnych procesach: 198048–303592 KiB przed
zmianą i 399220–512036 KiB po zmianie; średni przyrost w dopasowanych parach
to **201,409 MiB**. Największy zmierzony proces v2 zużył około 500 MiB.
To oszczędność czasu kosztem pamięci, nie obietnica braku kosztów zasobowych.

W obu wersjach i obu powtórzeniach zachowano **112 par obserwacja–ramka**, czyli
**88 różnych ramek** po deduplikacji całego czteroplikowego zestawu. Zachowane są
także 204 modele i 29424 próby uzupełniające w jednym przejściu przez cztery pliki.
Nie wykryto żadnej różnicy w deterministycznym wyniku, w tym utraty ramki.
Nie jest to nowy eksperyment odzyskiwania dodatkowej telemetrii: skuteczność
pozostaje taka sama, zmienia się koszt wykonania.

Źródło pełnych par i metryk: `work/lossless-speed-20260910-v1/paired-v2/summary.json`,
SHA-256 `b645eafd74584bcb12ee039b33842a1217d00db3a2e66d7fb7b69c641146b67c`.
Każda para ma też osobny plik `r<repeat>-<id>-comparison.json`, oba pełne wyniki,
logi i surowy pomiar `/usr/bin/time`. Zmienność obciążenia VM nie jest wykluczona;
nie podajemy tego wyniku jako uniwersalnego współczynnika dla innych nagrań,
protokołów czy komputerów.

## Protokół pomiaru

`examples/lossless_replay_bench.rs` uruchamia dwie zamrożone wersje kolejno,
naprzemiennie AB/BA. Każda dostaje ten sam wcześniej przekonwertowany WAV,
cztery wątki oraz przypisanie CPU 4–7. Dwa powtórzenia czterech obserwacji
14936407, 14936415, 14936424, 14936444 dają osiem par i szesnaście procesów.

Czas procesu obejmuje start, odczyt PCM, dekodowanie, haszowanie i zapis wyniku;
konwersja OGG pozostaje poza pomiarem. Osobno zapisujemy czasy etapów dekodera,
czas CPU oraz maksymalny RSS z `/usr/bin/time`. To VM z ośmioma vCPU,
`rustc 1.98.1`, profil release z istniejącymi ustawieniami LTO; na hoście działa
inne zadanie użytkownika, więc nie twierdzimy, że sprzęt jest całkowicie izolowany.
Nie zmieniamy ustawień bezpieczeństwa jądra dla niedostępnego `perf`.

Porównanie obejmuje cały deterministyczny JSON wyniku: wejście, konfigurację,
ramki z FCS, kolejność prób, okna, parametry modeli, odrzucenia, pominięcia,
banki docelowe i przypisanie modelu. Usuwamy wyłącznie pola pomiaru czasu
`elapsed_seconds`, `stage_wall_seconds`, `total_wall_seconds`. Kanoniczna
serializacja z `float_roundtrip` zachowuje wartości f64, w tym znak zera.
Sama zgodność liczby ramek nie wystarcza. Sterownik odrzuca pusty manifest,
wejście inne niż WAV, błąd procesu, nieukończony wynik i zmianę tożsamości wejścia.

Skończony zestaw replayów nie dowodzi zgodności dla wszystkich możliwych sygnałów.
Podstawą zachowania wyników jest również powyższy argument równoważności zmian.
Ta praca nie stanowi nowego porównania szybkości z gr-satellites ani DireWolf.

# Sprawdzenie możliwości przyspieszenia — 2026-09-12

Zakres: odczyt kodu, zamrożonych źródeł, istniejących pomiarów i bieżącego
obciążenia. Nie zmieniono odbiornika, limitów ani uruchomionej kampanii. Nie
uruchamiano konkurencyjnego benchmarku DSP na jej współdzielonym hoście.

## Wniosek

Można przyspieszyć program bez zamierzonego ograniczania banku hipotez.
Najbardziej konkretna ścieżka to już przetestowane ponowne używanie przygotowania
okien. Są też dalsze możliwości w aktualnym kodzie, ale ich zyski nie są zmierzone.
Samo zwiększenie liczby wątków na tej samej VM nie tworzy dodatkowych rdzeni.

## Zmierzony stan hosta i bieżącego badania

Około 13:40 UTC: 8 vCPU, około 19 GiB RAM, około 16 GiB dostępnego RAM.
Dwa bieżące interwały vmstat: CPU idle 1% i 0%, user+system 94% i 90%,
steal 4% i 9%, iowait po 1%; brak swap-in/out. To krótka próbka, nie profil
całego badania ani dowód stałego obciążenia. Nie występuje w niej wąskie gardło RAM.

O 13:45:40 UTC ukończone były 21 nagrania z kompletem pięciu ramion.
Średnie czasy procesu, przy równoległych obserwacjach na współdzielonym hoście:

| Ramię | Średni czas procesu [s] | Suma CPU dla 21 procesów [s] |
|---|---:|---:|
| progressive_v3 | 325,363 | 11717,77 |
| innovation_v2 | 142,312 | 5509,68 |
| innovation_v1_no_codec | 133,856 | 5303,40 |
| gr_satellites | 3,892 | 145,05 |
| direwolf | 3,561 | 72,05 |

Konwersja do wspólnego PCM zajmowała średnio 3,723 s; przeciętne wejście miało
486,002 s. To opis kosztów ukończonego prefiksu, nie końcowe porównanie szybkości.
Pięć ramion oznacza pięć eksperymentalnych uruchomień, a nie wymóg uruchamiania
pięciu odbiorników przy każdym użyciu docelowego programu.

Źródła: `work/publication-execution-20260912-v1/historical-comparison-v1/obs-*/result.json`,
pola `decoders.*.process` oraz `conversion_process`. Parametry kampanii pozostają
4 obserwacje równolegle × 2 wątki natywnego DSP.

## Istotna różnica wersji

Kampania korzysta z zamrożonego progressive SHA256
`e59e8dac6991984de1b7b1e7238835dd3ad740150ae5004070c215290ee2fda3`.
Jego `runtime-sources-v1.tar.gz`, odczytany bez rozpakowywania na źródła projektu,
nie zawiera cache `PreparedWindow`/`OnceLock` w `rust/progressive.rs`.

Nowszy wariant z cache był osobno porównany 11 września: dwie stare obserwacje
rozwojowe × dwa powtórzenia, cztery pary. Suma czasu wyniosła 1319,260 → 981,945 s
(25,57% krócej, 1,344×), CPU 1990,16 → 1438,78 s (27,71% mniej).
Wszystkie deterministyczne wyniki były identyczne. Jeden przebieg nowej wersji
trwał jednak 7,44% dłużej. Nie jest to gwarancja przyspieszenia każdego nagrania
ani bezpośredni pomiar względem obecnie działającego binarium.

Źródła: `reports/progressive-speed-20260911.md` i
`work/progressive-speed-20260911-v1/paired/summary.json`.

Ponadto zapis kompilacji zamrożonej biblioteki DSP podaje `opt-level=2`,
`codegen-units=8`, bez flagi LTO. Sam obecny `Cargo.toml` ma profil release
z thin LTO i jednym codegen unit, więc jego odczyt nie wystarcza do ustalenia,
jak zbudowano uruchomiony artefakt. Pomiar alternatywnego sposobu kompilacji
jest osobnym eksperymentem; nie przypisujemy mu niezmierzonego procentu zysku.
Dowód: `work/decoder-runtime-repair-20260911-v1/build-and-tests.json`.

## Dalsze możliwości w aktualnym kodzie

1. **Więcej RAM na dokładne przygotowanie okien.** `rust/progressive.rs:21`
   ustala cache na 512 MiB. Po wyczerpaniu rezerwacji okna są przeliczane.
   Konfigurowalny, większy budżet sumaryczny dla aktywnych procesów może zatrzymać
   więcej wyników filtrów i banków zegara. Nie należy zastępować filtrowania
   poszczególnych okien filtrowaniem całego pliku: to inna operacja brzegowa.
2. **Usunięcie kopii już przygotowanych frontendów.**
   `rust/adaptive.rs:970` zamienia pożyczony frontend na własny kontener przez
   `into_owned()`. Interfejs przyjmujący pożyczkę pozwoli zachować te same próbki
   bez dodatkowego kopiowania. Zysk całości nie jest zmierzony.
3. **Bufory robocze w torze innovations.** `rust/innovation.rs:396` przydziela
   traceback dla każdego wywołania; `rust/innovation_audio.rs:449` używa
   alokującego interfejsu zwykłego detektora mimo istniejącego
   `SequenceWorkspace`. Bufory na pracownika można ponownie używać, zachowując
   kolejność obliczeń i rozstrzyganie remisów.
4. **Wspólne wykonanie dokładnie równoważnych wariantów.** Pętla w
   `rust/innovation_audio.rs:434` osobno wykonuje matched-white i innovations.
   Dla AR(0) bez wag `rust/innovation.rs:359` deleguje innovations do tego samego
   zwykłego detektora. Można policzyć raz i zachować oba wpisy dowodowe. Dotyczy
   tylko sprawdzonego przypadku równoważności, nie dowolnych modeli czy wag.
   Częstość takiego przypadku w całym zbiorze nie została zmierzona.
5. **Osobny pomiar kompilacji produkcyjnej.** Porównać te same zamrożone źródła
   z istniejącym profilem release, oddzielnie od zmian kodu. Nie zakładać, że
   sama wyższa optymalizacja kompilatora zawsze skróci wykonanie.

Checkpointy wykonują również synchronizowany zapis podsumowania pod mutexem
(`rust/progressive.rs:489` i dalej). To kandydat do późniejszego profilowania,
ale obecna próbka obciążenia nie uzasadnia uznania dysku za główną przyczynę.
Nie usuwać trwałości checkpointów, kontroli integralności ani walidacji FCS.

## Warunki bezstratności i kolejność

Najpierw osobny wariant oparty na cache i bufory, potem pomiar A/B na danych
rozwojowych. Sprawdzać pełny deterministyczny wynik, zawartość ramek z FCS,
bank zadań, przypadki błędów oraz przerwanie/wznowienie; nie tylko liczbę ramek.
Nie używać fast-math, zmniejszenia precyzji ani pomijania hipotez jako
"bezstratnej optymalizacji". Równość ukończonego trybu full nie gwarantuje
identycznego prefiksu zadań przy limicie czasu quick/deep.

Obecne badanie powinno dokończyć się na niezmienionych artefaktach. Nie podmieniać
binarium w połowie ani nie współdzielić ukrytego cache między niezależnymi
ramionami pomiaru kosztów. Zmiana wersji wymaga oddzielnie oznaczonego porównania.

Graphify skierował zapytanie do starego kodu Python, nie obecnej implementacji
Rust. Ustalenia powyżej pochodzą z bezpośredniego odczytu źródeł i raportów.

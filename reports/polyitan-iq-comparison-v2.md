# Phase-first v2: przyczyna regresji i test transferowy

## Wynik

Na obserwacji transferowej CANVAS 14366383 sam ulepszony tor natywny odzyskał
**10 zaufanych ramek wobec 6** w torze zgodnym ze źródłami SatNOGS. Nie zgubił
żadnej ramki baseline i dodał cztery. Każda z dziesięciu ramek ma poprawny
CRC-16/X.25, legalną ramkę AX.25 UI `CANVAS` ← `LASP` z PID `F0` oraz jeden
pakiet CCSDS o dokładnej deklarowanej długości.

| Ramię | Zaufane ramki |
|---|---:|
| SatNOGS-compatible baseline | 6 |
| Stary direct phase-first | 5 |
| Nowy conditioned phase | 9 |
| **Standalone native-v2: unia dwóch torów phase** | **10** |
| **Unia native-v2 z baseline** | **10** |

Baseline i native-v2 mają sześć wspólnych payloadów. Native-v2 ma cztery
dodatkowe, baseline nie ma już żadnego payloadu, którego native-v2 nie
odzyskał. Między starym a nowym torem phase są cztery duplikaty; deduplikacja
odbywa się po SHA-256 payloadu dopiero po kontroli protokołu.

To jest wynik `transfer_holdout_not_preregistered`: ustawienia nowego toru i
selektora zamrożono na SONATE-2, a selektor i dekoder nie miały dostępu do
referencyjnych bajtów, hashy ani czasów zdarzeń. Sam plik CANVAS był jednak
wcześniej oglądany podczas wymaganej diagnozy różnicy per-frame, więc nie
nazywamy tego całkowicie ślepym, prerejestrowanym holdoutem.

Audyt provenance sprawdza teraz ID obserwacji fail-closed. Oba artefakty
natywne deklarują i mają `observation_id=14366383`. Historyczny schemat
baseline nie zawiera pola `observation_id`; ten brak jest jawnie zapisany w
audycie jako `absent_in_source_artifact`, zamiast przypisywać mu ID domyślnie.
W artefakcie starego phase-first poprawiono wyłącznie omyłkowo skopiowane ID;
wszystkie pozostałe pola, ramki i wynik 5/9/10 są bez zmian.

## Skąd wzięło się wcześniejsze 16 wobec 22

Dokładny bilans starego toru był następujący:

| Obserwacja | Baseline | Stary phase | Wspólne | Baseline-only | Phase-only |
|---:|---:|---:|---:|---:|---:|
| 14115025 CANVAS | 10 | 11 | 10 | 0 | 1 |
| 14366383 CANVAS | 6 | 5 | 4 | 2 | 1 |
| 4264 SONATE-2 | 6 | 0 | 0 | 6 | 0 |
| **Razem** | **22** | **16** | 14 | **8** | **2** |

Stary phase-first pomijał więc osiem konkretnych ramek baseline, ale znajdował
dwie inne; stąd wynik netto −6. Największy problem nie był w deframerze ani w
CRC, tylko przed slicerem: różnica fazy była liczona przed filtrowaniem
zespolonego IQ. Przy słabszym FSK i małej dewiacji SONATE-2 (2400 Hz) szerokie
pasmo szumu psuło miarę oka, przez co użyteczne fazy zegara wypadały poza
ograniczony bank.

Kontrola rozdziela tę przyczynę od samego zwiększania brute-force: na tych
samych pięciu oknach rozszerzenie starego frontendu do wszystkich 224
hipotez fazy/rate odzyskało tylko 1 z 6 ramek baseline. Nowy frontend z
zaledwie zamrożonym top-32 odzyskał 6 z 6. Poprawa pochodzi więc z kondycjonowania
kanału przed ekstrakcją fazy, nie z większego budżetu wyszukiwania.

## Ogólna poprawka

Nowa gałąź nadal podejmuje decyzję na podstawie fazy, lecz najpierw:

1. szacuje i usuwa offset nośnej;
2. filtruje zespolone IQ w kanale i decymuje do dwóch próbek na symbol;
3. dopiero potem liczy różnicę fazy;
4. sprawdza stały bank 32 hipotez zegara;
5. akceptuje wynik dopiero po CRC i kontroli protokołu.

Nie ma tu dopasowania do bajtów, callsignów ani APID. Automatyczny selektor
używa wyłącznie jednosekundowej energii i koherencji różnicy fazy, z ustalonym
progiem, top-64 i dwusekundowym paddingiem. Na transferze wybrał 207 z 243
możliwych okien dekodera, więc jest celowo bardzo zachowawczy i na razie daje
niewielką oszczędność obliczeń.

## Cztery dodatkowe ramki CANVAS

| SHA-256 payloadu | Źródło w native-v2 | APID | Sekwencja |
|---|---|---:|---:|
| `4275158a2627889b4ae03e802e59ec99479fc885e4580d4adbd00ab74718fd29` | conditioned | 23 | 2179 |
| `aed9f410c5cf85b112905a25047197471afbc9e1568962252d3083567d86cd86` | conditioned | 32 | 2199 |
| `b459c218ada6d574ad015702fc16b1e69b94977c78fe284d45201483ab075cfc` | legacy + conditioned | 23 | 2161 |
| `cc5bbc0daff3340c4dd238c6555818b6594e4460d4cfc00388c7af73facf705e` | conditioned | 23 | 2215 |

Wszystkie cztery mają te same legalne callsigny `CANVAS`/`LASP`, PID `F0` i
poprawny wewnętrzny CCSDS. Jedna ramka baseline pominięta przez conditioned
jest nadal odzyskiwana przez legacy phase, dlatego ich unia jest ważna.

## Koszt

Nowy tor wykonał 6624 hipotezy timingowe: 207 okien × 32. Stary tor wykonał
11 664 hipotezy: 243 okna × 48. Dołączenie nowej gałęzi zwiększa więc liczbę
prób timingowych native-v2 o 56,8% względem starej gałęzi.

Pomiar jednordzeniowy przy `nice 15`, podczas pracy dwóch innych shardów po
cztery rdzenie, trwał 40 min 22,78 s wall clock, zużył 905,22 s user CPU i
63,45 s system CPU; maksymalne RSS wyniosło 159 696 KiB. Wall time jest
zdominowany przez konkurencję o CPU i nie jest czystym benchmarkiem
przepustowości.

## SONATE-2: tylko górna granica diagnostyczna

Na pięciu oknach zlokalizowanych z wcześniejszego strumienia baseline nowa
gałąź odzyskała wszystkie 6 ramek baseline oraz 5 dodatkowych legalnych ramek
AX.25. To ważny dowód przyczyny i potencjału, ale nie wynik benchmarku: wybór
czasów nie był reference-free. Pełnego agregatu native-v2 dla wszystkich
trzech obserwacji dlatego jeszcze nie deklarujemy.

## Artefakty

- `reports/polyitan-iq-comparison-v2.json` — kompletne liczby, hashe i koszt;
- `reports/phase-first-v2-14366383-holdout-audit.json` — ścisły audyt ramek;
- `work/polyitan/phase-parity/canvas-14366383-selector-holdout.json` — wynik
  selektora bez referencji;
- `work/polyitan/phase-parity/canvas-14366383-conditioned-holdout.json` — wynik
  decode-only nowej gałęzi;
- `reports/phase-first-parity-14115025-diagnostic.json`,
  `reports/phase-first-parity-14366383-diagnostic.json` i
  `reports/phase-first-parity-4264-diagnostic.json` — rozliczenie per-frame;
- `work/polyitan/phase-parity/sonate2-prefilter-top32-probe.json` — wyłącznie
  diagnostyczna górna granica SONATE-2.

Starszy raport `reports/polyitan-iq-comparison-v1.json` nie został nadpisany.

Komplet SHA-256 kodu potrzebnego do reprodukcji znajduje się w polu
`plans_and_artifacts.reproduction_dependencies_sha256` raportu JSON oraz w
samym ścisłym audycie. Obejmuje runner i selektor, `clock_recovery`, CRC,
walidację AX.25 i CCSDS oraz pomocniczy moduł CAMRAS. Hash zintegrowanego
pluginu `GenericReceiver` jest zapisany osobno jako
`generic_receiver_plugin_sha256`.

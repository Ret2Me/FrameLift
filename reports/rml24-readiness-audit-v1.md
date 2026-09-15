# Audyt gotowości pełnego benchmarku RML24

## Wynik

Tor jest gotowy do uruchomienia po zakończeniu i promocji obu pobrań do nazw bez `.part`. Pełnego benchmarku nie uruchomiono. Audyt nie otwierał ani nie modyfikował aktywnych plików `.part`.

Wykryto i naprawiono jedno krytyczne ryzyko OOM. Pickle protokołu 4 memoizuje surowe bloki tablic; interpreter zachowywał je wcześniej aż do końca pliku, co dla RML24 mogło zatrzymać w RAM niemal całe 21,676 GB IQ. Teraz duży blok istnieje tylko podczas zapisu jednego sharda, a memo przechowuje sentinel. Dodatkowo obowiązują limity: 64 MiB na tablicę, 64 KiB na pojedynczy zachowany blok `bytes`, 16 MiB łącznie na bloki `bytes` w memo oraz po 250 000 wpisów memo i stosu. Nadmiernie duży odczyt opcodu jest odrzucany przed alokacją.

CLI konwersji sprawdza teraz dokładny artefakt Zenodo 17800058: rozmiar i MD5 archiwum, rozmiary obu członków, brak duplikatów, path traversal, szyfrowania, symlinków i nieobsługiwanej kompresji. Przed rozpoczęciem konwersji atomowo zapisuje `complete=false`; błąd pozostawia manifest fail-closed. Manifest końcowy zawiera SHA-256 archiwum, rozmiary wszystkich shardów oraz faktyczne statystyki ograniczeń pamięci.

Loader benchmarku wymaga pary IQ+bity dla każdej grupy, unikalnych grup i plików oraz zgodności shape/dtype/rozmiaru z manifestem. NPY są otwierane przez `mmap`; porcje są krojone po rekordach. Nie ma `pickle.load`, `IQ_data[:]` ani materializacji 1,323 mln rekordów.

## RAM

- Ekstrakcja HDF5: porcje 8 MiB przy hashowaniu i rozpakowaniu; konserwatywnie poniżej około 64 MiB.
- Inwentaryzacja HDF5: domyślnie 65 536 samych etykiet/SNR/rate na porcję, bez IQ; konserwatywnie poniżej 128 MiB.
- Konwersja Pickle: rzeczywisty shard IQ ma 16 384 000 B. Z limitów i schematu wynika około 100–300 MiB peak dla opublikowanego pliku; nawet pojedynczy złośliwy opcode nie może zażądać więcej niż około 64 MiB plus narzut ramki. Jest to daleko poniżej limitu VM 20 GB.
- Benchmark przy `--batch-size 32`: IQ porcji to 524 288 B; największy tymczasowy FFT QPSK/OQPSK to 524 288 B na aktualnie obrabiany rekord. Memmap jednej grupy IQ ma 16 384 000 B przestrzeni plikowej, ale nie jest ładowany w całości. Konserwatywny peak procesu jest poniżej 256 MiB, poza odzyskiwalnym page cache systemu.

## Dysk

Metadane publikacji dają:

- dwa archiwa: 40 580 410 133 B;
- wyodrębniony HDF5: 21 691 910 760 B;
- shardy NPY: najwyżej około 28 521 712 227 B (payload obu Pickli plus 256 B nagłówka dla 2 × 1323 plików);
- łącznie przy zachowaniu wszystkiego: około 90 794 033 120 B, czyli 84,56 GiB.

Migawka z `2026-09-02T14:52:10Z`: 134 935 433 216 B wolnego, a manifest pobrania raportował 6 297 749 780 B już zapisanych. Przy braku innych dużych zapisów po pobraniu, ekstrakcji i konwersji pozostanie około 50 439 149 876 B (46,98 GiB). Każdy etap ponownie sprawdza ustawioną rezerwę minimum 10 GB; równoległe zadania mogą zmienić tę projekcję.

HDF5 nie zawiera prawdy bitowej potrzebnej do BER i według kodu autorów ma co najmniej `IQ_data` oraz numeryczne `class`. Jest przydatny do inwentaryzacji/AMR. Pełny fizyczny BER należy uruchamiać z parowanych shardów Pickle, gdzie klucze `(modulation, SNR, symbol_rate)` zachowują czytelne etykiety.

## Bezpieczna sekwencja po `complete=true`

W katalogu `/home/ubuntu/telemetry-yield`:

```bash
rtk jq -e '.complete == true and .verified_count == 2 and ([.artifacts[].status] | all(. == "downloaded_verified" or . == "skipped_verified"))' \
  work/nature-dataset/download-manifest.json

rtk .venv/bin/pytest -q \
  tests/test_rml24_archive.py tests/test_rml24_benchmark.py \
  tests/test_rml24_physical_plugin.py tests/test_verified_download.py \
  tests/test_download_guard.py tests/test_cli.py
```

Opcjonalna ścieżka HDF5 do audytu klas (nie jest potrzebna do BER):

```bash
rtk ionice -c2 -n7 nice -n 10 .venv/bin/python -m telemetry_yield.cli \
  extract-rml24-hdf5 \
  work/nature-dataset/archives/RML24_IQdata.zip \
  work/nature-dataset/extracted/RML24_IQdata.h5 \
  --plan work/nature-dataset/hdf5-extraction-plan.json \
  --report reports/rml24-hdf5-extraction-v1.json

rtk ionice -c2 -n7 nice -n 10 .venv/bin/python -m telemetry_yield.cli \
  inventory-rml24-hdf5 \
  work/nature-dataset/extracted/RML24_IQdata.h5 \
  --batch-size 65536 \
  --output reports/rml24-hdf5-inventory-v1.json
```

Obowiązkowa ścieżka BER:

```bash
rtk ionice -c2 -n7 nice -n 10 .venv/bin/python -m telemetry_yield.cli \
  convert-rml24-pickle \
  'work/nature-dataset/archives/pkl format.zip' \
  work/nature-dataset/shards \
  --plan work/nature-dataset/pickle-conversion-plan.json \
  --manifest work/nature-dataset/shards/manifest.json \
  --max-array-mib 64

rtk jq -e '.complete == true and .status == "complete" and .pickle_executed == false and .pickle_opcode_reads_bounded == true and .paired_group_count == .group_count and .peak_array_payload_bytes <= .max_array_bytes' \
  work/nature-dataset/shards/manifest.json

rtk ionice -c2 -n7 nice -n 5 .venv/bin/python -m telemetry_yield.cli \
  benchmark-rml24-physical \
  work/nature-dataset/shards/manifest.json \
  --batch-size 32 \
  --sample-rate 1000000 \
  --max-shift-bits 8 \
  --output reports/rml24-physical-ber-v1.json

rtk jq -e '.records_seen == .source.record_count and (.metrics.bit_demodulation.status == "complete" or .metrics.bit_demodulation.status == "complete_supported_subset") and .claims.packet_yield_comparable_to_satnogs == false' \
  reports/rml24-physical-ber-v1.json
```

Jeśli plan ma więcej niż siedem dni albo kontrola wolnego miejsca nie zostawia 10 GB rezerwy, komenda celowo odmówi pracy. Nie należy omijać tej blokady; trzeba odświeżyć plan lub zwolnić miejsce.

## Pozostałe ograniczenia

- 1 323 000 rekordów jest mocno potwierdzonym wnioskiem z aktualnych rozmiarów i README, ale wynik końcowy należy brać z manifestu po konwersji.
- Benchmark raportuje BER tylko dla dokładnie wspieranych klas. Inne klasy są liczone jako `unsupported`, nie jako błędna demodulacja.
- RML24 nie ma prawdy ramek AX.25/CCSDS, więc nie daje porównania liczby poprawnych ramek z SatNOGS.
- Pełny przebieg będzie długi obliczeniowo, zwłaszcza QPSK/OQPSK, lecz nie powinien być pamięciowo niebezpieczny.

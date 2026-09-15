# RML24 — plan i stan benchmarku streamingowego

## Stan teraz

Pełne pobieranie obu archiwów działa w tle. Jest sekwencyjne, wznawialne po przerwaniu i uruchomione z `nice=15` oraz `ionice=best-effort/7`, żeby nie przeszkadzać trwającym skanom. Najpierw pobierany jest wariant Pickle z referencyjnymi bitami, potem HDF5. Bieżący postęp zapisuje się atomowo w `work/nature-dataset/download-manifest.json`.

Każdy ukończony plik musi przejść trzy bramki: dokładny rozmiar, MD5 podany przez Zenodo oraz lokalnie policzony SHA-256. Dopiero wtedy `.part` jest atomowo przemianowany na plik końcowy.

## Co już działa

- `verified_download.py`: wznowienie HTTP Range, retry, sekwencyjność, kwarantanna błędnego pliku, kontrola miejsca, postęp i sumy kontrolne.
- `rml24_benchmark.py`: stałopamięciowe porcje HDF5, bezpieczne `NPY` przez memory-map oraz trzy jawnie rozdzielone wyniki:
  - AMR accuracy;
  - BER względem referencyjnych bitów;
  - AX.25/CCSDS frame yield = **niedostępny w RML24**, a nie zero.
- Wyniki AMR i BER są agregowane per prawdziwa modulacja, SNR i szybkość symbolowa.
- Autorzy w swoim loaderze robią `hdf['IQ_data'][:]`, czyli ładują całość. Nasz loader zawsze używa wycinków `start:stop`.
- Bezpośrednie `pickle.load` dla monolitycznych 28,5 GB po rozpakowaniu jest zabronione. Najpierw sprawdzimy rzeczywisty układ serializacji, potem wykonamy ograniczoną konwersję do nieobiektowych tablic NPY/memmap.

Po sprawdzeniu prefiksu wdrożony został ścisły konwerter Pickle: interpretuje małą listę dozwolonych opcode'ów, nie wykonuje `GLOBAL`, dopuszcza wyłącznie wzorzec rekonstrukcji `numpy.dtype`/`numpy.ndarray`, ogranicza rozmiar pojedynczej tablicy i od razu zapisuje każdą grupę jako osobny nieobiektowy plik NPY. Test syntetyczny potwierdza parowanie IQ ↔ bity oraz streamingowy BER bez `pickle.load`.

Bezpieczne rozpakowanie HDF5 również jest gotowe: sprawdza rozmiar i MD5 archiwum, wszystkie ścieżki ZIP, typ pliku, metodę kompresji, deklarowany rozmiar, CRC32 i SHA-256 wyjścia. Inwentaryzacja HDF5 czyta porcjami tylko etykiety/SNR/rate i nie dotyka macierzy IQ.

Pierwszy właściwy plugin BER obsługuje BPSK i binarne GMSK/2FSK. Odzyskuje CFO, nieznaną fazę/polaryzację i fazę zegara bez dostępu do prawdziwych bitów. Prawda trafia dopiero do jawnego alignmentu ±8 bitów i liczenia BER. CLI `benchmark-rml24-physical` uruchamia tylko te profile, a pozostałe klasy zapisuje jako `unsupported`.

Testy RML24 przeszły: `32 passed`. Pokrycie obejmuje wznowienie, MD5+SHA-256, odrzucenie path traversal, CRC, porcjową inwentaryzację HDF5, AMR, BER, bezpieczny Pickle→NPY z ograniczonym memo/opcode, rzeczywistą demodulację BPSK/QPSK/OQPSK/2FSK/GMSK oraz wymusza brak nieuprawnionego wyniku ramek telemetrycznych.

## Kolejne kroki po pobraniu

1. Zweryfikować oba archiwa i ustalić rzeczywiste klucze, kształty, typy danych i liczbę klas.
2. Sprawdzić strukturę Pickle bez wykonywania zawartego w nim kodu i bez alokacji całych tablic.
3. Przekonwertować potrzebne pola do nieobiektowych NPY/memmap z manifestem zachowującym wyrównanie IQ ↔ bity ↔ etykiety.
4. Podłączyć klasyfikator AMR i demodulatory jako lokalne pluginy oraz policzyć wyniki porcjami.
5. Nie mieszać tych metryk z osobnym testem odzysku poprawnych ramek z realnych nagrań satelitarnych.

Pełnych wyników RML24 jeszcze nie ma, bo pierwszy 20,5-gigabajtowy plik nadal się pobiera. Nie raportuję syntetycznego self-testu jako wyniku badawczego.

## Gotowe polecenia po pobraniu

```bash
telemetry-yield extract-rml24-hdf5 \
  'work/nature-dataset/archives/RML24_IQdata.zip' \
  'work/nature-dataset/extracted/RML24_IQdata.h5' \
  --plan 'work/nature-dataset/hdf5-extraction-plan.json' \
  --report 'reports/rml24-hdf5-extraction-v1.json'

telemetry-yield inventory-rml24-hdf5 \
  'work/nature-dataset/extracted/RML24_IQdata.h5' \
  --output 'reports/rml24-hdf5-inventory-v1.json'

telemetry-yield convert-rml24-pickle \
  'work/nature-dataset/archives/pkl format.zip' \
  'work/nature-dataset/shards' \
  --plan 'work/nature-dataset/pickle-conversion-plan.json'

python work/nature-dataset/run_rml24_benchmark.py \
  'work/nature-dataset/shards/manifest.json' \
  --source-type group-shards \
  --amr-predictor trusted_module:predict_modulation \
  --bit-demodulator trusted_module:demodulate_bits
```

Ostatnie polecenie wymaga konkretnych pluginów klasyfikatora i demodulatora; bez nich runner nadal może wykonać audyt źródła, ale uczciwie zapisze AMR/BER jako `not_run`.

# RML24 (Scientific Data 13, article 860) — ocena dla telemetry-yield

## Wynik

To jest właściwy zbiór: **RML24**, artykuł „Cognitive Radio for Satellite TT & C System: A General Dataset Using Software-defined Radio”, Scientific Data 13, 860 (2026), DOI artykułu `10.1038/s41597-026-07182-7`. Dane są publicznie dostępne na Zenodo pod DOI `10.5281/zenodo.17800058`.

Zbiór jest bardzo dobry do rozszerzania odbiornika na wiele modulacji i do mierzenia BER, ale **nie daje uczciwego bezpośredniego porównania liczby odzyskanych ramek telemetrycznych z SatNOGS**. Rekordy mają tylko 2048 próbek (2,048 ms), pochodzą z laboratoryjnego generatora/hybrydowego toru RF, a publikacja nie opisuje w nich prawdy referencyjnej dla ramek AX.25 ani CCSDS.

## Co zawiera

- Według artykułu: 1 386 000 rekordów, każdy jako `2 × 2048` próbek I/Q, próbkowanie 1 MHz.
- SNR: od −20 do 20 dB co 2 dB.
- Prędkości symbolowe: 100/200/500 kBd dla modulacji pojedynczych oraz 20/50/100 kBd dla złożonych.
- 1000 rekordów dla każdej kombinacji modulacja × SNR × szybkość symbolowa.
- Generacja w GNU Radio, tor RF z USRP X410, modelowane zaburzenia kanału i rzeczywiste artefakty toru radiowego. To nie są nagrania przelotów satelitów.
- Wariant Pickle zawiera referencyjne bity, więc pozwala mierzyć BER demodulatora.

Publikowane 22 modulacje:

- pojedyncze: BPSK, QPSK, OQPSK, SOQPSK-TG, FQPSK, ARTM, FM, PM, 8PSK, GMSK, 16QAM, 32QAM, 64QAM, 16APSK, 32APSK;
- złożone: PCM-BPSK-PM, PCM-QPSK-PM, PCM-SOQPSK-PM, PCM-FQPSK-PM, PCM-BPSK-FM, PCM-QPSK-FM, PCM-2FSK-PM.

Jest ważna niespójność wersji: aktualny README autorów z 16 kwietnia 2026 mówi, że wysłane archiwa zawierają na razie 21 klas z powodu problemu z „SOQPSK-PM” i zaleca wariant Pickle. Artykuł nadal opisuje 22 klasy i 1 386 000 rekordów. Rozmiar nieskompresowanego Pickle I/Q jest tylko o 89 625 bajtów większy niż surowa macierz `1 323 000 × 2 × 2048 × float32`, więc mocno potwierdza obecny upload 21-klasowy; dokładne etykiety sprawdzimy po pełnym pobraniu.

Jest też niespójność szybkości: bez wykonywania `pickle.load` odczytałem z prefiksu aktualnego artefaktu grupy `BPSK/-20/100000` i `BPSK/-20/250000`. Druga ma 250 kBd, podczas gdy artykuł podaje 200 kBd. Dlatego benchmark zinwentaryzuje etykiety bezpośrednio z pliku, a nie przyjmie tabeli z publikacji jako prawdy.

## Rozmiar i dostęp

Zenodo oznacza rekord jako otwarty, bez logowania, z licencją danych CC BY 4.0. Bezpośredni download i HTTP Range działają.

| Archiwum | Rozmiar | Suma po rozpakowaniu | Suma kontrolna Zenodo |
|---|---:|---:|---|
| `RML24_IQdata.zip` → jeden `RML24_IQdata.h5` | 20,074 GB | 21,692 GB | `md5:a34af2743489f04f78bf6c4a2cf80883` |
| `pkl format.zip` → `RML24_BITdata.pkl` + `RML24_IQdata.pkl` | 20,507 GB | 28,521 GB | `md5:35b3d996d290fc79e04969a0653baf31` |

Łącznie oba alternatywne warianty mają 40,580 GB (37,793 GiB) po sieci. Archiwa są monolityczne: HDF5 jest jednym skompresowanym wpisem, a Pickle dwoma dużymi wpisami. Nie ma małej, niezależnie pobieralnej próbki I/Q. Najpierw pobrałem i zahashowałem metadane, spis repozytorium, README oraz referencyjny loader; pełne, wznawialne pobieranie obu archiwów zostało następnie uruchomione w tle z niskim priorytetem.

## Jak go rzetelnie użyć

1. Dla RML24 mierzyć osobno trafność klasyfikacji modulacji oraz BER względem `Bitdata`, w podziale na modulację, SNR i szybkość symbolową.
2. Użyć zbioru do implementacji/testowania rodzin BPSK/QPSK/OQPSK/APSK/QAM/GMSK i modulacji złożonych, których obecny odbiornik jeszcze nie obejmuje.
3. Nie raportować tych wyników jako „więcej ramek SatNOGS”. Porównanie końcowe pakietów pozostawić na identycznych, długich nagraniach realnych satelitów i wymagać walidacji AX.25/CCSDS/FEC/CRC.
4. Pełne pobranie zacząć od Pickle (zawiera bity), ale potrzebuje ok. 20,5 GB transferu i 28,5 GB miejsca po rozpakowaniu. Loader autorów ładuje cały HDF5 do RAM, więc nasz benchmark powinien czytać dane strumieniowo/chunkami.

## Źródła pierwotne

- [Artykuł w Scientific Data](https://www.nature.com/articles/s41597-026-07182-7)
- [Rekord danych Zenodo](https://zenodo.org/records/17800058)
- [Kod i aktualne uwagi autorów](https://github.com/yiwawa/RML24-Cognitive-Radio-for-Satellite)

Lokalne, zahashowane artefakty źródłowe znajdują się w `work/nature-dataset/`; pełne sumy SHA-256 są zapisane w raporcie JSON.

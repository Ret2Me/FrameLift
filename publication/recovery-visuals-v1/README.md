# Prawdziwe „przed / po” — telemetria CANVAS

To wykresy **rzeczywistych pól odebranych pakietów**, nie ilustracja wygenerowana przez AI ani sztucznie uszkodzone zdjęcie. PNG to renderowane zrzuty identycznych paneli badawczych. Nie są zrzutami produkcyjnej Grafany.

## Pliki

Wydanie na GitHub zawiera dane `data/` i gotowe wykresy. Duże pliki źródłowe
`sources/capture.ogg` i `sources/recreated-shared-pcm16.wav` pozostają w lokalnym
archiwum; ich identyfikatory i hashe są zapisane w `data/provenance.json`.
Gotowe pliki wykonywalne także nie są częścią repozytorium — należy je zbudować
ze źródeł zgodnie z instrukcją poniżej.

- `figures/before.png`: unia wyników Dire Wolf i gr-satellites.
- `figures/after.png`: nasz `progressive_v3`, ten sam plik audio.
- `figures/comparison.png`: oba panele obok siebie.
- Odpowiedniki `.svg`: wektorowe źródła rysunków.
- `data/beacons.csv` / `beacons.json`: odczytane wartości wraz z całymi PDU, czasem MET, numerem sekwencji i obecnością w każdym odbiorniku.
- `data/native-frames-with-received-fcs.hex`: rzeczywiście odebrane ramki wraz z FCS, nie FCS dopisane do zgadniętych danych.
- `data/native-result.json`, `direwolf.stdout.log`, `gr-satellites.kiss`: oryginalne wyjścia trzech odbiorników.
- `data/provenance.json`: identyfikatory, hashe, źródło definicji telemetrii, jawne ograniczenia.
- `sources/capture.ogg`: kopia rzeczywistego nagrania; bez pogarszania lub ulepszania sygnału na potrzeby ilustracji.
- `sources/canvas.ksy`: przypięta publiczna definicja pól.

## Konkretny wynik

Obserwacja **14780429**, CANVAS, stacja **2865**, 14 sierpnia 2026, początek **00:31:48 UTC**. OGG trwa **326,364 s**. Porównujemy już wykonane, ukończone przebiegi zamrożonych dekoderów — nie uruchamiano nowego strojenia odbiornika pod wykres.

| Wynik | Dire Wolf | gr-satellites | Ich unia | Telemetry Yield progressive |
|---|---:|---:|---:|---:|
| Wszystkie unikalne PDU AX.25 UI | 30 | 2 | 31 | 118 |
| Pełne rekordy beacona APID 0x20 | 2 | 0 | 2 | 36 |

Nasze 36 beaconów zawiera oba rekordy referencyjne i **34 dodatkowe**. To większa liczba rzeczywistych pomiarów, a nie większa rozdzielczość obrazu. Ten przykład nie zawiera zdjęcia satelitarnego.

## Co przedstawiają wykresy

Każdy punkt jest polem z kompletnego 264-bajtowego PDU CANVAS, po kontroli nagłówka AX.25, wersji/typu CCSDS, nagłówka dodatkowego, APID, flag grupowania i deklarowanej długości. Pozostałe 82 PDU innych typów naszego odbiornika nie są interpretowane jako beacon.

Oś czasu to `bcn_des_met_time_sec − 829763`, czyli względny czas pokładowy MET odczytany z treści pakietu. Zakres odczytanych rekordów wynosi **215 s**. To nie pozycja próbki w OGG; nie zakładamy epoki timestampu ani ciągłości odbioru przez cały plik.

Pola `bcn_solar_panel1_temp` i `bcn_eps_batt1_temp` są dwubajtowymi wartościami big-endian, odpowiednio na offsetach 104 i 98 od początku AX.25 PDU. Definicja opisuje je jako odczyty temperatur, ale nie dokumentuje ich skali fizycznej, dlatego **nie podpisujemy wartości jako °C**. MET jest u32 big-endian na offsecie 44.

Wszystkie skale, zakresy, wielkości punktów i barwy są takie same w obu panelach. Nie interpolujemy ani nie wygładzamy braków. Kontury na pasku dostępności oznaczają znaczniki rekordów znanych z unii wyników, nie kompletną listę wszystkich pakietów nadanych przez satelitę.

## Rzetelność i ograniczenia

- Przykład wybrano **po obejrzeniu wyników**, aby pokazać mechanizm uzupełniania braków. Nie jest reprezentatywnym oszacowaniem przewagi ani osobnym testem statystycznym.
- Obserwacja należy do bieżącej kampanii, lecz **nie należy do migawki 189 obserwacji użytej we wstępnym paperze v1**. Rysunek powinien zostać włączony dopiero do odpowiednio opisanej kolejnej wersji.
- Zbiory PDU z podsumowania ponownie porównano z surowym hexdumpem Dire Wolf, plikiem KISS gr-satellites i natywnym wynikiem. KISS-owe pakiety metadanych czasu (komenda 9) nie są liczone jako telemetryczne PDU.
- Niezależnie odtworzono wspólny plik PCM16 WAV z oryginalnego OGG. SHA-256 odtworzonego WAV jest identyczny z zapisanym wejściem wszystkich trzech odbiorników: `758176340d3fc6b42dc5a37dd8154e1824e7b30bafa9446dfd0ddcb7d82ab782`. Plik znajduje się w `sources/recreated-shared-pcm16.wav`.
- Dla wszystkich 118 ramek natywnych sprawdzono rzeczywiście odebrane FCS algorytmem CRC-16/X-25. W wyjściach zewnętrznych FCS jest usunięte; polegamy na ich wewnętrznej kontroli. Wspólne rekordy mają identyczne bajty.
- Nie weryfikowano dodatkowego czterobajtowego checksumu aplikacyjnego CANVAS, ponieważ użyta definicja nie opisuje jego algorytmu. FCS i zgodność strukturalna nie stanowią kryptograficznego potwierdzenia pochodzenia.
- Nie podawano referencyjnych payloadów naszemu demodulatorowi i nie wykonywano korekty bitów na potrzeby uzyskania tych obrazków.

## Odtwarzanie

```sh
rustc --edition=2024 -O extract_beacons.rs -o extract-beacons
rustc --edition=2024 --test extract_beacons.rs -o extract-beacons-tests
./extract-beacons-tests
./extract-beacons < data/native-frames-with-received-fcs.hex
node render.cjs
node capture.cjs
```

`prepare.cjs` importuje wyniki z istniejącego lokalnego archiwum, kontroluje hashe i przelicza offsety ze schematu. Odmawia nadpisania istniejących danych. Odtwarzanie paneli z gotowego `data/` nie wymaga ponownej demodulacji. Generator grafiki korzysta z Node; parser zawartości i kontroli FCS jest w Rust. `capture.cjs` korzysta z lokalnego Playwright/Chromium wskazanego w pliku; można zmienić wyłącznie ścieżki narzędzi renderujących.

Publiczne źródła:

- [Obserwacja SatNOGS 14780429](https://network.satnogs.org/observations/14780429/)
- [Opis CANVAS i pól telemetrii w SatNOGS DB](https://db.satnogs.org/satellite/68635/)
- [Definicja CANVAS, przypięty commit dcf8af2c41fa9ac9ac755173719400ccd1a6c33f](https://gitlab.com/librespacefoundation/satnogs/satnogs-decoders/-/blob/dcf8af2c41fa9ac9ac755173719400ccd1a6c33f/ksy/canvas.ksy)

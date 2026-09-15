# Porównanie odzysku telemetrii z tych samych plików IQ

## Wynik

Sam phase-first nie zastępuje jeszcze SatNOGS: na trzech dodatnich,
protokołowo porównywalnych obserwacjach odzyskał 16 zaufanych ramek wobec 22
ramek gałęzi zgodnej ze źródłami SatNOGS. Jako druga, równoległa ścieżka
zwiększył jednak wspólny wynik do 24 różnych ramek: **+2 ramki, czyli +9,09%
względem 22**. Taki układ nie
traci ramek SatNOGS, a zachowuje ramki znalezione tylko przez phase-first.

| Obserwacja | Satelita | SatNOGS | Phase-first | Suma bez duplikatów | Kontrola protokołu |
|---:|---|---:|---:|---:|---|
| 14115025 | CANVAS | 10 | 11 | 11 | AX.25 + wewnętrzny CCSDS |
| 14366383 | CANVAS | 6 | 5 | 7 | AX.25 + wewnętrzny CCSDS |
| 4048 | NETSAT-1 | — | — | — | zgodność 1:1 surowego PDU; wyłączona z trusted |
| 4264 | SONATE-2 | 6 | 0 | 6 | AX.25 |
| **Trusted razem** |  | **22** | **16** | **24** | 4048 poza agregatem |

Obserwacja 4048 pozostaje wartościowym testem powtarzalności: oba tory zwróciły
ten sam 68-bajtowy PDU o SHA-256
`2c308f669e3679ebfc06a50df8898f7465c925119b608b9fb52012628109dbdf`.
Nie jest to jednak dowód poprawnej telemetrii. PDU nie przechodzi ścisłej
struktury AX.25 UI ani dokładnego surowego CCSDS, a nie potwierdzono osobnego
kontraktu payloadu NETSAT. Dlatego zgodność jest raportowana jako raw CRC/PDU
agreement i nie wpływa na liczby trusted ani przyrost.

To nie jest kwestia czterobajtowej otoczki. W przypiętym `gr-satnogs` dekoder
przekazuje do `metadata.pdu` cały bufor ramki po odjęciu wyłącznie dwóch bajtów
FCS. SatNOGS Client następnie dekoduje `pdu` z base64 i wysyła dokładnie te
bajty jako `demoddata`. Początkowe `00 00 ca 00` w 4048 jest więc częścią PDU,
nie ogólnym nagłówkiem SatNOGS, który można bezpiecznie usunąć. Źródła
pierwotne: [`gr-satnogs` AX.25 decoder](https://gitlab.com/librespacefoundation/satnogs/gr-satnogs/-/blob/8d94dc292ace327dcd15dba66dd2020031b2d5c7/lib/ax25_decoder.cc#L307-339)
oraz [`satnogs-client` upload danych](https://gitlab.com/librespacefoundation/satnogs/satnogs-client/-/blob/14561eff1d8f598ebc71f49e1347b16c3290a770/satnogsclient/scheduler/tasks.py#L101-116).

Obserwacja 14366383 jest najważniejsza metodologicznie: ustawienia phase-first
zamrożono przed odczytaniem ramek referencyjnych. SatNOGS i phase-first miały
odmienne błędy, dlatego ich suma dała 7 ramek wobec 6 z SatNOGS. Wszystkie
zaliczone ramki CANVAS przechodzą nie tylko CRC i strukturę AX.25, ale również
dokładną długość oraz nagłówek wewnętrznego pakietu CCSDS.

W rozwojowej obserwacji 14115025 dodatkowa ramka ma poprawne CRC, adresy
`CANVAS`/`LASP`, CCSDS APID 32 i numer sekwencji 5567. Wypełnia dokładnie lukę
między pakietami 5562 i 5572 w pięciosekundowym rytmie czasu, numeru sekwencji
i mission-elapsed-time.

## Nowe próbki z bucketu

- BUGSAT-1 4439: pełny ślepy skan 334 okien; SatNOGS 0, gr-satellites 0,
  phase-first 0. To remis 0:0, nie nowa telemetria.
- CANVAS 4440: pełny ślepy skan 370 okien; SatNOGS 0. Phase-first znalazł jeden
  przypadkowy ciąg z poprawnym CRC, ale nie jest on ani poprawnym AX.25, ani
  surowym pakietem CCSDS (wersja 7 i deklarowane 56 599 B przy faktycznych
  18 B). Wynik zaufany również 0:0.
- NETSAT-3 4135: pobrany po przywróceniu bucketu, pełny ślepy skan 218 okien;
  SatNOGS 0, phase-first 0, zaufany wynik 0:0.
- NETSAT-3 4083 i NETSAT-4 4136: phase-first znalazł po jednym ciągu z
  poprawnym CRC, ale oba odpadły na strukturze AX.25 i oficjalnym parserze
  misji. Nie są liczone jako telemetria.
- KOSTKA 4183: phase-first dał 0 kandydatów w 497 oknach. gr-satellites 5.9.0
  z aktualnym profilem KOSTKA wypuścił jeden siedmiobajtowy PDU po kontroli
  FCS, lecz jest on za krótki na legalny nagłówek AX.25. Zaufany wynik obu
  ścieżek pozostaje 0. Ten przypadek pokazuje, że sam CRC nie może być końcową
  bramką akceptacji także dla istniejącego dekodera.

## AX.25 i CCSDS

Nie stosujemy już globalnej zasady „musi być AX.25”. Kandydat jest sprawdzany
według kontraktu konkretnej misji: jako AX.25, surowy CCSDS Space Packet,
AX.25 zawierający CCSDS albo z użyciem stabilnego formatu danej misji.
CANVAS używa dwóch warstw naraz: AX.25 na zewnątrz i CCSDS wewnątrz.

Obecny phase-first na poziomie IQ nadal ma deframer HDLC/AX.25. Dodany walidator
potrafi już zaakceptować odzyskane bajty surowego pakietu CCSDS, ale pełny tor
dla surowego IQ CCSDS (ASM, transfer frames, randomizacja i kodowanie kanałowe)
wymaga osobnej gałęzi zależnej od misji. Nie przypisujemy więc obecnym testom
wydajności na surowym CCSDS, której nie zmierzono.

## RML24 z artykułu nr 860

RML24 jest dobrym zbiorem do porównania błędu bitowego i rozpoznawania
modulacji: ma 1 386 000 próbek, po 2048 próbek IQ przy 1 MHz, oraz prawdziwe
bity nadawcze. Nie jest uczciwym testem liczby ramek SatNOGS, ponieważ rekord
trwa tylko 2,048 ms i nie reprezentuje całego przelotu z granicami ramek oraz
referencyjną telemetrią misji.

## Ograniczenia i decyzja

„SatNOGS” oznacza tutaj odtwarzalny, zgodny źródłowo tor offline oparty na
przypiętych źródłach gr-satnogs/flowgraphs, a nie bitowo identyczny historyczny
kontener produkcyjny. Sam phase-first ma regresję na SONATE-2, dlatego właściwa
wersja wdrożeniowa to dwie równoległe ścieżki i protokół-specyficzna suma.

Wynik ma potencjał publikacyjny jako metoda komplementarna: istnieje niezależny
przypadek CANVAS z dodatkową ramką poprawną jednocześnie jako AX.25 i CCSDS.
Przed mocnym twierdzeniem o ogólnej przewadze potrzeba większej, z góry
zamrożonej próby ślepych nagrań, w tym prawdziwej misji nadającej surowe CCSDS.

## Najważniejsze artefakty

- `reports/canvas-extra-frame-audit.json` — dodatkowa ramka 14115025.
- `reports/canvas-14366383-ccsds-audit.json` — niezależny audyt obu warstw.
- `work/polyitan/obs-4439/bugsat-4439-comparison.json` — nowy BUGSAT.
- `work/polyitan/obs-4440/protocol-audit.json` — nowy CANVAS i odrzucona
  kolizja CRC.
- `reports/polyitan-iq-comparison-v1.json` — maszynowo czytelny bilans.

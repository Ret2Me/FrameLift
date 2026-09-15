# E-ST@R-II: dodatni AFSK1200 na identycznym IQ

## Wynik

To jest **dodatni benchmark demodulacji**, ale **nie dodatni benchmark
rygorystycznego AX.25**.

Na dokładnie tym samym pliku IQ:

- gr-satellites 5.9.0 odzyskał 3/3 oficjalnych PDU SatNOGS;
- niezależny natywny Bell-202 odzyskał te same 3/3 PDU i ich poprawne FCS;
- zbiory bajtów są identyczne;
- wszystkie pięć kontroli null dało zero ramek;
- wszystkie trzy PDU zostały poprawnie odrzucone przez rygorystyczny parser
  AX.25, ponieważ źródłowy bajt SSID `0x62` ma wyzerowany bit EA kończący
  łańcuch adresów.

Werdykt warstwowy: **PASS dla AFSK/HDLC/CRC**, **FAIL dla strict AX.25 z
powodu struktury nadanej przez satelitę**, a nie rozbieżności dekoderów.

## Dlaczego ta próbka

Najpierw sprawdzono priorytet AFSK1200. Dostarczony katalog PolyITAN ma 23
takie IQ, ale wszystkie bez ramek referencyjnych i wcześniejszy wynik 0:0.
Następnie skrzyżowano 467 pozycji niezależnego archiwum CAMRAS z oficjalnym
API SatNOGS. Znaleziono trzy obserwacje AFSK1200; tylko `6511229` ma
niepuste `demoddata` — trzy PDU.

[Archiwum CAMRAS](https://data.camras.nl/satnogs/) opisuje pliki jako surowe
zespolone próbki 16-bit przy 48 kS/s i bezpośrednio wiąże
`iq_6511229.raw` z E-ST@R-II. [API obserwacji
SatNOGS](https://network.satnogs.org/api/observations/6511229/) podaje AFSK,
1200 baud, stację PI9RD i trzy pliki `demoddata`. Oficjalny [profil
E-ST@R-II w gr-satellites](https://raw.githubusercontent.com/daniestevez/gr-satellites/main/python/satyaml/E-ST@R-II.yml)
ustala 437,485 MHz, AFSK1200, tony 1200/2200 Hz i AX.25.

Kohortę, komendy, walidację i nulle zamrożono przed natywnym dekodowaniem w
`reports/estar2-afsk1200-positive-same-iq-plan-v1.json`.

## Kontrakt identycznych bajtów

| Pole | Wartość |
|---|---:|
| Plik | `iq_6511229.raw` |
| Format | CI16-LE, I/Q interleaved |
| Próbkowanie | 48 000 próbek zespolonych/s |
| Rozmiar | 105 195 044 B |
| Próbki zespolone | 26 298 761 |
| SHA-256 | `f60c521fd22502d82061e6059b8e12646ef2870dcccce11612b2f2f0d4d76bae` |

Oba lokalne dekodery raportują ten sam zamrożony hash wejścia. Pobieranie
używało resume, a rozmiar, ETag, Last-Modified i lokalny SHA zapisano przed
dekodowaniem.

## Dokładne liczby

| Miara | gr-satellites | Native |
|---|---:|---:|
| Surowe/CRC-poprawne unikalne PDU | 3 | 3 |
| Dokładne dopasowanie do 3 referencji | 3 | 3 |
| Strict AX.25 | 0 | 0 |

Native zbadał 45 okien i 540 hipotez synchronizacji. Wykrył 54 wystąpienia
CRC-poprawnych kandydatów; po deduplikacji są to dokładnie trzy referencje.
Duplikaty wynikają z nakładających się okien. Wszystkie 54 wystąpienia
odrzucono dopiero na niezależnej kontroli struktury AX.25.

Problem jest widoczny w każdym źródłowym PDU: po dwóch siedmiobajtowych
adresach bajt źródłowego SSID to `0x62`, którego bit EA wynosi 0. Następny bajt
to już kontrola UI `0x03`; poprawny parser musiałby oczekiwać kolejnego
siedmiobajtowego adresu. Nie naprawiano tego bitu posthoc i nie liczono tych
PDU jako zaufanej telemetrii.

## Kontrole null

- gr-satellites: pełne zero tej samej długości — 0; deterministyczna
  permutacja próbek tej samej długości — 0;
- native: pełne zero — 0; deterministyczna permutacja — 0; błędny baud 2400
  — 0.

Audyt fail-closed (`work/positive-real-iq/audit_estar2_afsk_artifacts.py`)
ponownie sprawdził hash planu i IQ, KISS, zgodność zbiorów, powód odrzucenia
oraz wszystkie nulle: **PASS**.

Skupione testy AFSK, backendu gr-satellites i walidatora AX.25: **16 passed**.

## Granica wniosku

Ta jedna próbka jest mocnym, niezależnym dodatnim fixture’em regresyjnym dla
Bell-202 → HDLC → CRC i pokazuje zgodność bajtową z wykonywalnym baseline’em
[gr-satellites](https://github.com/daniestevez/gr-satellites). Nie przechodzi
zamrożonej bramki strict AX.25, nie dotyczy CCSDS i nie dowodzi przewagi,
recallu populacyjnego ani zachowania na innych misjach. Potrzebna pozostaje
druga próbka z normatywnie poprawnym AX.25 albo dodatni BPSK/QPSK/CCSDS na
identycznym IQ.

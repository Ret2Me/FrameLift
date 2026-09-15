# Niezależny audyt sample rate CAMRAS / main30 v1

Data audytu: 2026-09-03  
Werdykt: **FAIL_GLOBAL_48K_CONTRACT**

Zamrożony `main30` uruchomił wszystkie 30 plików jako 48 kS/s. Pełna lokalna kontrola wykazała, że 23 pliki mają wiarygodny, pierwotny kontrakt pipeline'u dający 57.6 kS/s, cztery mają wiarygodne 48 kS/s, a trzy nie mają dostatecznego kontraktu i muszą pozostać zablokowane. Wszystkie 30 lokalnych IQ (3,817,581,596 B) ponownie zahashowano; rozmiary i SHA-256 zgadzają się z manifestem acquisition.

Najważniejszy wynik dla RSP-03: spośród 21 plików 20 ma dokładne metadane joba `gr-satnogs` (`baudrate=9600`, `framing=ax25`, `enable-iq-dump=1`). Oficjalny pipeline daje dla nich `9600 × 6 = 57,600` próbek/s. Jedyny wyjątek, obserwacja `12817180`, nie ma `client_metadata`; czas/rozmiar wskazuje 57.6 kS/s, ale to tylko inferencja i plik pozostaje nierozstrzygnięty. Globalne 48 kS/s nie jest dopuszczalnym kontraktem RSP-03.

## Co jest dowodem

Pierwotny dowód pipeline'u składa się z dwóch niezależnie sprawdzonych elementów:

1. SHA-zweryfikowany snapshot API konkretnej obserwacji zapisuje wersję radia, dokładne parametry joba oraz włączony IQ dump.
2. Oficjalne, tagowane źródła `satnogs-flowgraphs` prowadzą tap IQ ze strumienia post-Doppler o rate `baudrate*decimation`; `gr-satnogs` definiuje parzyste `find_decimation`. Inwariant sprawdzono we wszystkich 21 lokalnie dostępnych wydaniach od `0.1` do `2.5.2`.

To jest mocna **derywacja ze źródeł pierwotnych**, lecz nie bezpośrednie pole `sample_rate` w sidecarze. Metadane `samp-rate-rx=1000000` opisują wejście SDR, nie rate archiwalnego strumienia.

CAMRAS index (`da3d4f2c697b40b2c6d5fc9c730adef8052203830201c5cd9cbdf8a98b027ff9`) publikuje nazwę, zaokrąglony rozmiar, link obserwacji, czas, misję i status — nie publikuje sample rate. W `b2-transfer` nie ma oryginalnych sidecarów SigMF ani README per plik. Lokalny `rsp03.chan1.sigmf-meta` z rate 1 MS/s jest innym, nowszym nagraniem VRT i nie należy do main30; jego rate nie wolno przenosić na pliki `data.camras`.

Test czas/rozmiar używa jawnego marginesu 30 s wyłącznie jako sygnału do przeglądu. API window nie jest kontraktem długości zapisanego IQ. Dlatego timing nie wypełnia `accepted_rate_hz`.

## Audyt 30/30

`primary` oznacza derywację z hashowanego joba i oficjalnego kodu; `timing-only` oznacza brak wiarygodnego kontraktu. Wartość `Δt` to `samples/Fs_candidate - API_window`; nie jest dowodem rate.

| Obs | Misja | Baud | Accepted Fs | Timing candidate (Δt) | Dowód | main30 @48k |
|---:|---|---:|---:|---:|---|---|
| 10301639 | GRBAlpha | 9600 | 57600 | 57600 (-21.05 s) | primary | misconfigured |
| 4554304 | MCUBED-2 | 9600 | 57600 | 57600 (-4.58 s) | primary | misconfigured |
| 4554483 | TIGRISAT | 9600 | 57600 | 57600 (-5.00 s) | primary | misconfigured |
| 6365642 | CELESTA | 2400 | 48000 | 48000 (-5.13 s) | primary | valid record |
| 13168691 | RSP-03 | 9600 | 57600 | 57600 (-9.72 s) | primary | misconfigured |
| 12579358 | RSP-03 | 9600 | 57600 | 57600 (-18.38 s) | primary | misconfigured |
| 12839477 | RSP-03 | 9600 | 57600 | 57600 (-9.37 s) | primary | misconfigured |
| 12780582 | RSP-03 | 9600 | 57600 | 57600 (-18.30 s) | primary | misconfigured |
| 12774765 | RSP-03 | 9600 | 57600 | 57600 (-18.72 s) | primary | misconfigured |
| 13218955 | RSP-03 | 9600 | 57600 | 57600 (-18.41 s) | primary | misconfigured |
| 12512296 | RSP-03 | 9600 | 57600 | 57600 (-14.06 s) | primary | misconfigured |
| 12579357 | RSP-03 | 9600 | 57600 | 57600 (-18.21 s) | primary | misconfigured |
| 12839478 | RSP-03 | 9600 | 57600 | 57600 (-9.86 s) | primary | misconfigured |
| 12879281 | RSP-03 | 9600 | 57600 | 57600 (-18.15 s) | primary | misconfigured |
| 12898582 | RSP-03 | 9600 | 57600 | 57600 (-8.32 s) | primary | misconfigured |
| 13009022 | RSP-03 | 9600 | 57600 | 57600 (-18.74 s) | primary | misconfigured |
| 13173314 | RSP-03 | 9600 | 57600 | 57600 (-9.37 s) | primary | misconfigured |
| 13406961 | RSP-03 | 9600 | 57600 | 57600 (-18.87 s) | primary | misconfigured |
| 12620260 | RSP-03 | 9600 | 57600 | 57600 (-18.56 s) | primary | misconfigured |
| 12729258 | RSP-03 | 9600 | 57600 | 57600 (-19.97 s) | primary | misconfigured |
| 12803482 | RSP-03 | 9600 | 57600 | 57600 (-18.18 s) | primary | misconfigured |
| 12817180 | RSP-03 | 9600 | — | 57600 (-18.86 s) | timing-only | blocked |
| 13168692 | RSP-03 | 9600 | 57600 | 57600 (-9.73 s) | primary | misconfigured |
| 1567220 | PAINANI-1 | 9600 | — | 57600 (-4.76 s) | timing-only | blocked |
| 12620261 | RSP-03 | 9600 | 57600 | 57600 (-18.56 s) | primary | misconfigured |
| 13009023 | RSP-03 | 9600 | 57600 | 57600 (-18.75 s) | primary | misconfigured |
| 489123 | FIREBIRD 4 | 19200 | — | 48000 (-1.07 s) | timing-only; conflict with generic 19k2 candidate 76800 | blocked |
| 5409715 | DELFI-PQ | 1200 | 48000 | 48000 (-4.97 s) | primary | valid record |
| 9388631 | Kashiwa | 4800 | 48000 | 48000 (-14.50 s) | primary | valid record |
| 9389746 | Kashiwa | 4800 | 48000 | 48000 (-4.94 s) | primary | valid record |

Pełne, nieucięte SHA-256 każdego IQ i snapshotu API są w raporcie JSON. Dla 9k6 bilans wynosi 25 plików: 23 mają accepted 57.6 kS/s, dwa (`12817180`, `1567220`) pozostają unresolved. Nie ma żadnego wiarygodnego 9k6/48 kS/s w main30.

## Ocena wyników main30 v1

- Ważne pozostają: identyczność bajtów między gałęziami, SHA plików, wykonanie zgodne z zamrożonym planem oraz cztery wyniki per-record przy poprawnym 48 kS/s.
- CELESTA `6365642` zachowuje lokalny wynik 3 baseline / 15 native / +12 native-only. DELFI-PQ `5409715` zachowuje 4/4. Dwa Kashiwa zachowują 0/0.
- Wszystkie 21 wyniki RSP-03 0/0 są niekonkluzywne: 20 uruchomiono z błędnym Fs, jeden bez kontraktu.
- 23 per-record wyniki przy 48 zamiast 57.6 kS/s są fizycznie misconfigured. Trzy kolejne wyniki są nieważne przez brak kontraktu.
- FIREBIRD `489123` dał 32/32 przy 48 kS/s i timing silnie wspiera 48 kS/s, ale bez dokładnego joba nie wolno go awansować do wyniku z prawidłowym kontraktem.
- Detekcja 3/30, porównanie 39:51, bootstrap, statystyki mission-level i null exposure nie mogą być interpretowane jako benchmark fizyczny. Kontrole z błędnym/nieznanym rate nie walidują przyszłego corrected-rate replay.

## Remediacja per profile

- RSP-03: 20 wskazanych rekordów zamrozić z `sample_rate_hz=57600`; `12817180` wykluczyć do czasu odzyskania metadanych recorder/joba.
- GRBAlpha, MCUBED-2, TIGRISAT 9k6: replay po 57.6 kS/s.
- PAINANI-1 9k6: zablokować; 57.6 kS/s jest tylko kandydatem timing/pipeline-family.
- CELESTA 2k4, DELFI-PQ 1k2, Kashiwa 4k8: 48 kS/s jest poprawne. Wyniki można jawnie zaimportować po SHA do nowego planu albo odtworzyć dla czystej spójności kampanii.
- FIREBIRD 4 19k2: zablokować. Timing wskazuje 48 kS/s, ogólny flowgraph 19k2 wskazywałby 76.8 kS/s; wymagane są historyczne parametry joba/recordera albo niezależny pomiar zegara.

Implementacyjnie native decoder ma stałą `SAMPLE_RATE=48000`; należy zastąpić ją wymaganym `--sample-rate`, uwzględnianym w plan hash, oknach/hopach i raporcie. Następnie zamrozić nowy, wersjonowany plan per-observation, uruchomić identity i wszystkie rate-sensitive controls na niezmienionych SHA IQ oraz przeliczyć statystyki tylko dla rekordów z kontraktem. Ponieważ wyniki/referencje v1 są już znane, poprawiony replay trzeba oznaczyć jako post-exposure configuration repair, a nie nowy pristine blind holdout.

## SHA i reprodukcja

- plan v1: `7190bbde0ed927f9d8ff60ee700aacd79da6e7138188201132ae937f4865a470`
- result v1: `b0f806fd9f552a3f4fa09d077a8f9e49feb379fefab75162f49297e460c67968`
- acquisition: `6914ffe7c483550fd00cc69670ceb7f26c870cad070ddcb4449b1f96c5a253db`
- auditor: `e5104ec17e025152a1d2bfbc37a3c3294c7444209d9120afe214f016d67668a6`
- upstream flowgraphs HEAD: `ac12b77974a4478fb4f24ae8b41bf74b808fb03a`
- upstream gr-satnogs HEAD: `8d94dc292ace327dcd15dba66dd2020031b2d5c7`

Pełna reprodukcja read-only z ponownym hashowaniem wszystkich IQ:

```bash
python3 work/positive-real-iq/audit_camras_sample_rate_independent.py --full-raw-hash --summary-only
```

Oczekiwany wynik: 30/30 SHA zgodne; 23 `misconfigured`, 4 `valid_for_this_record`, 3 `invalid_missing_rate_contract`.

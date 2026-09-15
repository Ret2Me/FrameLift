# Stan odzyskiwania telemetrii — 7 września 2026

Ponowne zsumowanie istniejących, ścisłych raportów daje **890 ramek naszego toru, 712 baseline i 896 w ich połączeniu**. Nasz tor wnosi **184 dodatkowe ramki**, a baseline zachowuje sześć, których nie odzyskał uwzględniony tor natywny. Połączenie daje **25,8% więcej ramek** niż sam baseline; sam tor natywny ma przewagę netto 25,0%.

To opis kilku różnych, wcześniej wykonanych eksperymentów na identycznych wejściach IQ, obejmujących 45 różnych obserwacji. Nie jest to jeden zamrożony dekoder ani ślepy test populacyjny. Dodatkowe ramki pojawiły się w 13/45 obserwacji (28,9%). Nie znamy całkowitej liczby nadanych ramek, więc nie da się podać bezwzględnej skuteczności ani prawdopodobieństwa sukcesu na przyszłym nagraniu.

| Składnik historyczny | Obserwacje | Nasz tor | Baseline | Tylko nasz | Tylko baseline |
|---|---:|---:|---:|---:|---:|
| Wielomisyjny P0 | 3 | 671 | 629 | 42 | 0 |
| Transfer v1 | 30 | 51 | 39 | 12 | 0 |
| CELESTA, siedem obserwacji | 7 | 123 | 18 | 105 | 0 |
| MTCube-2 | 2 | 24 | 4 | 20 | 0 |
| CANVAS 14115025 | 1 | 11 | 10 | 1 | 0 |
| CANVAS 14366383, v2 | 1 | 10 | 6 | 4 | 0 |
| SONATE-2 4264, starszy pełny test | 1 | 0 | 6 | 0 | 6 |
| Razem | 45 | 890 | 712 | 184 | 6 |

Ramka jest liczona raz dla pary „ID obserwacji + pełne bajty PDU bez FCS”. Powtórzenia dekodowania i nakładające się okna nie zwiększają licznika. Te same bajty w dwóch różnych obserwacjach liczą się oddzielnie. Nie jest to liczba globalnie unikalnych komunikatów satelitarnych.

890 ramek odpowiada **165 521 bajtom PDU AX.25** (około 166 kB), razem z nagłówkami AX.25, bez FCS. To suma długości istniejących wyników, a nie objętość samej treści aplikacyjnej ani samych 184 dodatkowych ramek. W tym audycie nie uruchamiano dekoderów i nie odzyskano nowych danych.

## Ograniczenia wpływające na publikację

Późniejszy audyt wykazał błędne próbkowanie w 23/30 nagraniach kohorty transfer-v1 oraz trzy dalsze prawdopodobne niezgodności. Jedyny przypadek przyrostu, CELESTA 6365642, miał poprawne próbkowanie. Po konserwatywnym wyłączeniu całej kohorty pozostaje 15 obserwacji: nasz tor **839**, baseline **673**, połączenie **845**, dodatkowe **172** (+25,6%). Pozostałe ograniczenia doboru próbek nadal obowiązują.

Formalny test CELESTA v2 nie przeszedł zapisanego wcześniej warunku identyczności surowych plików KISS. Same zbiory prawidłowych ramek są powtarzalne; wynik 123/18 jest jawnie analizą wrażliwości po obejrzeniu wyników. P0 nie osiągnął celu trzech dodatnich misji. CANVAS v2 nie jest całkowicie ślepym holdoutem. Baseline różni się między badaniami: część używa wykonywalnego gr-satellites, część rekonstrukcji zgodnej ze źródłami SatNOGS. Nie dowodzi to przewagi nad wszystkimi dekoderami SatNOGS.

Materiał wystarcza do udokumentowanych wyników eksploracyjnych i opisów przypadków. **Szeroka przewaga i gotowość wdrożeniowa nie są jeszcze potwierdzone.** Ten raport nie szacuje szans przyjęcia artykułu.

## Rozliczenie poprzednich liczb

Poprzednie podsumowanie 891/712, +185 i połączenie 897 nie ma potwierdzenia w wyliczonych tu ścisłych źródłach. Przyjmujemy konserwatywnie **890/712, +184, połączenie 896**. Starszy raport CANVAS zawiera dodatkowego 31-bajtowego kandydata CRC-only, którego nie obejmuje dziesięcioramkowy audyt strict v2; jest to możliwe źródło różnicy, ale przyczyna nie została udowodniona. Nie nadpisano historycznych raportów ani nie zmieniono kryteriów walidacji.

W oddzielnych kohortach PolyITAN wynik nadal wynosił 0:0 zaufanych ramek: 28 katalogowo nieudanych G3RUH, 19 katalogowo dodatnich G3RUH oraz 23 nieudane AFSK1200. Nie wolno przedstawiać powyższego +25,8% jako skuteczności ratowania wszystkich nieudanych nagrań z tego bucketu.

Pełne ID obserwacji, źródła, SHA-256, długości PDU i rachunek znajdują się w [raporcie JSON](/home/ubuntu/telemetry-yield/reports/research-yield-status-20260907.json). To nowy opis istniejących artefaktów, bez ponownego dekodowania i bez dostępu do nieujawnionych wyników kampanii potwierdzającej.

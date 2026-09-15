# Ile dodatkowych danych odzyskano — 7 września 2026

**184 dodatkowe ramki zawierają dokładnie 43 292 bajty** (43,3 kB; 42,28 KiB).
To dane znalezione przez naszą metodę, których nie było w porównywanym dekoderze bazowym na tych samych nagraniach.

Cała metoda odzyskała 890 unikalnych ramek / 165 521 bajtów. Dekoder bazowy: 712 ramek / 123 157 bajtów.
Bazowy odzyskał również 6 ramek / 928 bajtów, których nasza metoda nie odzyskała.
Połączenie wyników daje 896 ramek / 166 449 bajtów: **+25,8% ramek i +35,2% bajtów** względem bazowego.
Samodzielna metoda daje netto o 178 ramek / 42 364 bajty więcej, ale traci wspomniane 6 ramek.

| Historyczne porównanie | Obserwacje | Dodatkowe ramki | Dodatkowe bajty |
|---|---:|---:|---:|
| P0 — trzy misje | 3 | 42 | 8 030 |
| Transfer v1 | 30 | 12 | 3 024 |
| CELESTA — kanoniczny wariant analizy | 7 | 105 | 26 460 |
| MTCube | 2 | 20 | 5 040 |
| CANVAS #14115025 | 1 | 1 | 264 |
| CANVAS #14366383 | 1 | 4 | 474 |
| SONATE-2 #4264 — starszy pełny przebieg | 1 | 0 | 0 |
| **Razem** | **45** | **184** | **43 292** |

„Bajty” oznaczają pełne PDU bez dwubajtowej sumy FCS, ale z nagłówkami AX.25.
Nie jest to rozmiar samej użytecznej telemetrii aplikacyjnej, plików IQ, kontenera KISS ani bitów po kodowaniu radiowym.
Ten sam PDU liczony jest raz w obrębie obserwacji; identyczne bajty w różnych obserwacjach liczone są osobno.

## Rzetelność i ograniczenia

To dokładne rozliczenie wcześniej ujawnionych wyników eksploracyjnych, **nie nowy eksperyment ani nowe odzyskane ramki**.
Sprawdzono ponownie SHA-256 plików źródłowych, pełnych PDU oraz ich długości; zestawy porównano po pełnych bajtach, nie po średniej długości ramek.
Przyjęcie ramki jako poprawnej pochodzi z wcześniejszych, przypiętych skrótem audytów; nie uruchamiano ponownie dekodera ani walidatora CRC/protokołu.
Plik JSON zawiera 184 dokładne identyfikatory i długości dodatkowych PDU, sześć bazowych oraz ścieżki, skróty i rozmiary 44 źródeł.

W późniejszym audycie wykryto pewne błędne częstotliwości próbkowania w 23 z 30 obserwacji Transfer v1 oraz trzy dalsze rozbieżności wnioskowane.
Po konserwatywnym wyłączeniu **całej** tej grupy pozostaje 172 dodatkowych ramek / **40 268 bajtów** na 15 obserwacjach.
Jedyny przypadek poprawy w Transfer v1, CELESTA #6365642, miał zgodną częstotliwość próbkowania.
Pozostałe ograniczenia pozostają: dobór nagrań i strojenie po obejrzeniu części danych; wynik CELESTA jest analizą wrażliwości, a pierwotne kryterium identyczności surowych plików powtórzeń nie zostało spełnione.
Nie zastąpiono nieudanego pełnego przebiegu SONATE-2 późniejszym ratowaniem ręcznie zlokalizowanych okien.
Nie użyto niepotwierdzonego starszego podsumowania 891/185 ani dodatkowych kandydatów mających jedynie poprawną CRC.

Materiał ten wspiera opis konkretnych korzyści i porażek metody, ale sam nie dowodzi uniwersalnej przewagi, gotowości wdrożeniowej ani skuteczności na przyszłych nagraniach.
Oddzielne przebadane grupy PolyITAN z zerowym wynikiem pozostają zerowe i nie wchodzą do tej grupy 45 obserwacji.

Źródło zbiorcze: [JSON z pełną ewidencją](/home/ubuntu/telemetry-yield/reports/research-extra-yield-bytes-20260907.json).
Poprzednie zestawienie pozostawiono bez zmian: [status badań](/home/ubuntu/telemetry-yield/reports/research-yield-status-20260907.json).

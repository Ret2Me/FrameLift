# Wynik ponownego testu — 7 września 2026

**Błędy wykonania zostały usunięte w tym przebiegu, ale nie odzyskano dodatkowej potwierdzonej telemetrii.**

Wszystkie **6168 prób zakończyły się poprawnie, bez błędów procesów, limitów ani weryfikacji artefaktów**. Test i końcowy raport trwały około **5 godzin 59 minut**: 17:27:17–23:26:38 UTC. Wcześniejszy pełny przebieg miał 269 błędnych prób; jego wyników nie zmieniano.

| Wynik | Wszystkie 30 nagrań | 29 nagrań bez #4491 |
|---|---:|---:|
| Ukończone próby | 6168/6168 | 5976/5976 |
| Błędy wykonania | 0 | 0 |
| Zaufane ramki: nasza metoda / komponent bazowy / profil misji | 0 / 0 / 0 | 0 / 0 / 0 |
| Dodatkowe zaufane dane | 0 B | 0 B |
| Niezgodne pary powtórzeń | 11 | 11 |
| Czas kontrolnego sygnału użyty do oceny fałszywych przyjęć | 30,558 h | 29,670 h |
| Górna 95% granica fałszywych przyjęć na godzinę | 0,09803 | 0,10097 |

100% poprawnie wykonanych prób **nie oznacza 100% skuteczności odbioru**. Na tych nagraniach żadna z porównywanych ścieżek nie dostarczyła zaufanej ramki. Taki wynik nie dowodzi ogólnej równorzędności dekoderów ani przewagi naszej metody.

## Co nadal nie przeszło

- Wszystkie 11 niezgodnych par dotyczyło profili misji na sygnałach kontrolnych. Niezależnie sprawdzono, że różnią się faktyczne zbiory **niezaufanych** PDU, a nie tylko logi. Nie ukrywano tych różnic.
- Profile misji dały 4 unikalne niezaufane PDU sygnałowe, łącznie 78 B, oraz 69 kontrolnych, łącznie 903 B. **Nie zaliczamy ich do odzyskanej telemetrii.** Liczenie usuwa duplikaty według obserwacji i pełnej zawartości PDU; bajty obejmują nagłówki.
- Pełna grupa spełniła liczbowy warunek ekspozycji kontrolnej i górnej granicy fałszywych przyjęć. Grupa 29 nagrań nie spełniła wymaganego minimum 30 godzin ani granicy 0,1/h. W żadnej grupie nie przyjęto zaufanej fałszywej ramki.
- Brak przyrostu telemetrii, niezgodne powtórzenia i uprzednie ujawnienie tego zbioru oznaczają, że **publikacja i wdrożenie nadal nie są gotowe**. To jawnie ponowny test operacyjny na znanych danych, nie nowy ślepy eksperyment.

## Co sprawdzono niezależnie

Odtworzono dokładny zestaw 6168 jednostek z metadanych 30 źródeł i 330 kontroli, bez wywoływania wykonawcy kampanii. Sprawdzono skróty własne wszystkich normalizacji i potwierdzeń procesów, tożsamość 6168 plików surowych wyników dekoderów, skróty PDU oraz zgodność końcowych zliczeń i 11 niezgodnych par z oceną. Wszystkie limity odpowiadają zatwierdzonej poprawce: cztery zadania równolegle, 2 GiB RAM i 512 procesów/wątków na zadanie.

Wszystkie procesy miały potwierdzone sprzątnięcie; po zakończeniu nie zostały jednostki scope tego wykonawcy. Nie wykonywano ponownego dekodowania ani niezależnego przeliczenia CRC — przyjęcia odziedziczono po niezmienionym, weryfikującym normalizatorze. Zachowano poprzednie wyniki v1 i przerwanego v2.

Ten test **nie powiększa wcześniejszego wyniku eksploracyjnego: 184 dodatkowych ramek / 43 292 B**. Następne badania muszą dotyczyć jakości odzyskiwania danych i pozostałej niepowtarzalności, a nie samych limitów wykonania.

[Niezależny audyt i skróty źródeł](/home/ubuntu/telemetry-yield/reports/scientific-campaign-v3-independent-terminal-audit-20260907.json) · [Automatyczny raport](/home/ubuntu/telemetry-yield/work/blind-phase-confirmatory-v2/campaign-v4-scientific-run-v3/terminal-summary.json) · [Ocena naukowa](/home/ubuntu/telemetry-yield/work/blind-phase-confirmatory-v2/campaign-v4-scientific-run-v3/evaluation.json).

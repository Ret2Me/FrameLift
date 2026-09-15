# Wynik pełnego przebiegu naukowego — 7 września 2026

**W tym nowym teście nie przyjęto żadnej poprawnie zweryfikowanej ramki telemetrii ani dodatkowych bajtów.**
Tak samo zakończyła się nasza metoda i porównywany dekoder bazowy.
To **nie potwierdza przewagi ani równej skuteczności**, bo eksperyment ma istotne błędy wykonania.

Wszystkie 6 168 zaplanowanych prób mają wynik końcowy: 6 166 uruchomiono teraz, a dwa wcześniejsze wyniki zaimportowano bez ponownego dekodowania.
5 899 prób zakończyło się poprawnie, 269 błędem. Wykonawca uczciwie zakończył się kodem 2 i statusem `campaign_scored_incomplete`.
Przebieg trwał **3 godziny 49 minut 25 sekund** (03:13:12–07:02:37 UTC), po zewnętrznym oznaczeniu czasu zamrożonej poprawki o 03:12:24 UTC.

| Wynik | Pełne 30 obserwacji | 29 obserwacji bez #4491 |
|---|---:|---:|
| Próby z zachowanym wynikiem końcowym | 6 168 | 5 976 |
| Próby zakończone poprawnie | 5 899 | 5 712 |
| Próby zakończone błędem | 269 | 264 |
| Przyjęte ramki: nasza metoda / bazowy / profil misji | 0 / 0 / 0 | 0 / 0 / 0 |
| Dodatkowe zweryfikowane dane | 0 B | 0 B |
| Niezgodne pary powtórzeń | 215 | 212 |
| Gotowość do publikacji / wdrożenia według zamrożonych kryteriów | nie / nie | nie / nie |

95,64% poprawnie zakończonych **prób wykonania** nie oznacza 95,64% skuteczności odbioru telemetrii.
Nie znamy liczby wszystkich rzeczywiście nadanych lub możliwych do odzyskania ramek.

## Co nie przeszło

- 221 prób otrzymało `descendant_cgroup_survivor`; 48 prób profili misji przekroczyło limit liczby procesów/wątków (`pids_limit`). Końcowe sprzątanie zostało potwierdzone, ale nie zmienia to wcześniejszych błędów w sukces.
- 209 niezgodnych par powtórzeń zawiera co najmniej jedną nieudaną próbę. Pozostałe 6 to pary profili misji, w których obie próby zakończyły się poprawnie; ich wynik semantyczny różni się.
- Nie było zaufanych fałszywych ramek w kontrolach. W pełnej grupie liczbowy warunek ekspozycji/rate przeszedł: 30,558 h i górna granica 0,09803/h. W grupie 29 obserwacji nie przeszedł: 29,670 h i 0,10097/h. Nie naprawia to braków całego eksperymentu.
- Profil misji wyemitował 7 unikalnych **niezaufanych** PDU sygnałowych (135 B) i 64 kontrolne (879 B). Nie zaliczamy ich do odzyskanej telemetrii.

## Co zachowano i sprawdzono

Plan, wejścia, konfigurację, dekodery, normalizator, kontrolne przekształcenia, kolejność statystyki i zakres obserwacji pozostawiono bez zmian. Nie strojono metody pod wyniki, nie wybierano nowych próbek w trakcie i nie powtarzano nieudanych jednostek.
Zachowano wszystkie wyniki surowe, potwierdzenia wykonania, normalizacje i 330 dzienników kontrolnych. Tymczasowe wygenerowane IQ usuwano po zweryfikowanym zapisie wyników; oryginalnego IQ nie usunięto.
Niezależnie od uruchomionego wykonawcy przeliczono ewidencję jednostek i sprawdzono skróty własne wszystkich 6 168 normalizacji oraz obu ocen końcowych. Dalszy niezależny audyt naukowy pozostaje wymagany.
Stary proces namespace 2072832 pozostawiono działający; sukcesu historycznego mechanizmu v3 nie zadeklarowano.

Ten przebieg **nie zwiększa** historycznego wyniku eksploracyjnego: 184 dodatkowe ramki / 43 292 B pozostają wynikiem wcześniejszych, innych porównań.
Bezpośredni następny problem to stabilność wykonania i diagnostyka profili misji; ten raport nie uruchamia poprawek ani ponownych dekodowań.

[Ewidencja i skróty źródeł](/home/ubuntu/telemetry-yield/reports/scientific-campaign-v1-terminal-status-20260907.json) · [Oryginalna ocena wykonawcy](/home/ubuntu/telemetry-yield/work/blind-phase-confirmatory-v2/campaign-v4-scientific-run-v1/evaluation.json) · [Historyczne dodatkowe bajty](/home/ubuntu/telemetry-yield/reports/research-extra-yield-bytes-20260907.json).

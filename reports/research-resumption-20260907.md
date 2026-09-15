# Wznowienie badań — 2026-09-07

## Wynik naukowy na początku wznowienia

Aktualny rachunek znajduje się w `research-yield-status-20260907.json` i `.md`:
890 ścisłych ramek toru natywnego, 712 baseline, 184 dodatkowe i 896 w sumie
obu torów (+25,8%). Przyrost wystąpił w 13/45 historycznych obserwacji.
To wyniki eksploracyjne; nie jest to zakończony test potwierdzający.
Nie uzyskano nowych ramek podczas opisanych niżej czynności technicznych.

## Zakończona nieudana próba retry4

Uruchomienie prywatnej przestrzeni montowań powiodło się. Aktywacja zatrzymała
się przed montowaniem projektu i uruchomieniem dekoderów: historyczny manifest
systemu plików nie odpowiadał uprawnieniom i inode po wcześniejszym utwardzeniu
środowiska. Zawartość plików środowiska pozostała identyczna.

Próbę `d19ca6e01fc84f75be86236d8d021c011227a1b7599129a813ab77d72ef9c6d6`
zamknięto narzędziem `abort_retry4_preactivation_v1.py` po sprawdzeniu dokładnej
tożsamości procesu, pustego tmpfs wyników, braku innych posiadaczy przestrzeni
montowań oraz braku zmian zewnętrznych montowań. Proces zakończono przez pidfd.
Nie deklarowano ukończenia kampanii ani eksportu wyników.

Wszystkie pliki kontrolne zachowano przez zmianę nazwy katalogu na:

`/var/lib/telemetry-yield-confirmatory-v3-aborted-retry4-d19ca6e01fc84f75be86236d8d021c011227a1b7599129a813ab77d72ef9c6d6`

Potwierdzenie zamknięcia: `retry4-preactivation-abort-receipt-v1.json`,
SHA-256 `56656e527ff7a639008063fbc4f4b5d05400d4407dd1fb58c881c460c9546030`.
Nie usunięto nagrań, wyników ani środowisk.

## Potwierdzony stan środowiska

Nowy manifest uzupełniający:
`blind-phase-confirmatory-component-hardening-resumption-v1.json`,
SHA-256 `50df6cee03ac7b5b7959c5bb20f720f54fac8e7f302a92e0a34d47a046ff213d`.
Publikacja lokalna nastąpiła po niezależnym przeglądzie kodu i 26 testach.
Dwa pełne odczyty 26 003 pozycji były identyczne.

- Historyczny manifest pozostaje niezmieniony i jawnie **nie** opisuje obecnych metadanych.
- Treść, ścieżki, rozmiary, mtime, bity wykonywania i cele dowiązań są identyczne.
- Zmiany obejmują właściciela root, usunięcie bitów zapisu, nowszy ctime,
  zastąpienie inode katalogu głównego i zerwanie 19 619 zewnętrznych twardych dowiązań.
- Manifest semantyczny: `c5041616e41f7081abbdaab25f0677280866b2780066e6d2ca9f00cf84b161d7`.
- Obecny manifest surowy: `af161e99b592ed9c106739b1804119bb7ecd2687ef18fe9f2b38f5125bde1aed`.

## Próba retry5

Wersjonowane poprawki przeszły niezależny przegląd i 33 testy uruchomione także
przez agenta głównego. Obejmują zgodność manifestów i wspólnego formatu kontroli
wykonania; nie zmieniają algorytmu, kohorty, punktacji ani zaplanowanych wyłączeń.

Amendment: `blind-phase-confirmatory-operational-amendment-v3-retry5-v1.json`,
SHA-256 `ddcd12454903d076d1ff06701427730e0853bfbecd2c60ac0407b13a1892b843`.
Znacznik czasu RFC3161 zweryfikowano względem zapytania z nonce oraz dokładnych
bajtów dokumentu; czas wystawienia: 2026-09-07 02:08:43 GMT.

Nowa próba: `4ae35ed0a8d02a8ddeeea96932c3ae8a000528c516b6f8ce762e4e4d76a8c2a8`.
Uruchomienie przestrzeni montowań i aktywacja zakończyły się pomyślnie, bez zmian
zewnętrznych montowań. Kopie 30 IQ i wybranych plików projektu zostały utworzone.
Etap `seal` oraz `close-seal-window` zakończyły się pomyślnie. Transakcja
uszczelnienia ma SHA-256
`a4a362c5dab831ba6e7461dd4eea97463cafd17d05efcfb01855bb6ffe300696`.
Keeper nadal ma PID 2072832, starttime 44980743 i mount namespace 4026532311.
Są to dane bieżącej próby, nie identyfikatory do ponownego użycia bez weryfikacji.

Następny `freeze-guard` zakończył się błędem `candidate source manifest bootstrap
content drift`. Nie powstał poprawny guard ani nowe wyniki dekodowania. Audyt
wykazał dwie niezależne niespójności infrastruktury:

1. Kontrola historycznych źródeł ponownie czyta dwa rozwojowe pliki dotyczące
   planowania obserwacji, choć przygotowane kopie wykonywalnego pakietu mają
   dokładnie zamrożone bajty. Mechanizm mapowania kopii nie jest użyty na tym
   etapie. Nie są to zmiany DSP.
2. Kontroler wymaga wykonania interpretera przez alias
   `/var/lib/telemetry-yield-confirmatory-v3/runtime/candidate/bin/python`,
   a ocena wymaga oryginalnej ścieżki środowiska. Obie ścieżki prowadzą do tego
   samego niezmienionego interpretera, lecz warunki tekstowe wzajemnie się
   wykluczają. Same inne argumenty wywołania tego nie naprawią.

Plan nadal obejmuje 30 IQ i 6 166 pozostałych wykonań; dwóch wcześniej ujawnionych
jednostek nie wolno ponawiać. Kampania pozostaje jawnie niepristine.

## Jawna korekta operacyjna w przygotowaniu

Nie będziemy uznawać retry5 za poprawnie zakończoną kontrolę ani zmieniać
zamrożonych plików w miejscu. Trwa przygotowanie małego, oddzielnego wykonawcy
porównania, wykorzystującego te same sterowniki jednostek, limity procesów,
normalizator v2, transformacje szumu kontrolnego i funkcję punktacji. Nowy
dokument operacyjny jawnie opisze słabsze założenia ochrony wykonania:
zaufany host oraz sprawdzenie skrótów rzeczywiście wykonywanego kodu i danych,
bez przypisywania mu gwarancji nieudanego systemu v3.

Przed pierwszym nowym odczytem ślepych danych przez dekoder wymagane są testy
syntetyczne faktycznego uruchomienia wszystkich trzech sterowników, niezależny
przegląd, utrwalenie tożsamości kodu i nowy znacznik czasu dokumentu. Dwa
wcześniej ujawnione wykonania pozostają wyłączone z ponownego wykonania.
Nie zmieniamy hipotezy, doboru próbek, ustawień DSP, punktacji ani kryteriów
sukcesu. W tej chwili nadal nie ma nowych wyników kampanii.

Ten plik jest notatką roboczą, a nie zgodą na pominięcie walidacji, zamrożonym
planem ani dowodem powodzenia przyszłego uruchomienia.

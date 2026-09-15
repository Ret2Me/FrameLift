# Ocena demodulatora i zakończone testy — 11 września 2026

## Decyzja

**Nie jest jeszcze gotowy jako uniwersalny demodulator ani do wdrożenia produkcyjnego.** Zamrożono i sprawdzono konfigurację do ściśle ograniczonej oceny odzyskiwania AX.25/G3RUH 9600 z nagrań CANVAS. Uruchomienie niezależnego benchmarku pozostaje osobnym, niespełnionym warunkiem dotyczącym danych. Nie dodajemy kolejnych metod do konfiguracji testowej na podstawie wyników tego zbioru.

Rozsądny kierunek publikacji to artykuł o sprawdzalnym, wznawialnym oprogramowaniu i zmierzonych korzyściach z ponownego przetwarzania, **nie twierdzenie o rewolucyjnym algorytmie**. Roboczy tekst dla SoftwareX jest przygotowany, ale nie jest gotowy do wysłania. Odrębne eksperymenty, wersje programu i reprezentacje sygnału nie zostały połączone w jeden procent skuteczności.

## Co rzeczywiście uzyskaliśmy

| Eksperyment | Wynik naszego toru | Porównanie i ograniczenie |
|---|---:|---|
| Starsze 20 publicznych OGG CANVAS | 127 par obserwacja–PDU | Dire Wolf 86, gr-satellites 44; 42 pary nieobecne w obu wynikach porównawczych, ale jedna ramka Dire Wolfa pominięta. Zbiór jest już rozwojowy. |
| Późniejszy pełny benchmark na tych samych 20 OGG | 139 par dla progressive, 138 dla innovation | Dire Wolf 86, gr-satellites 44. Pełny progressive dodaje 53 pary i nie traci żadnej względem obu zewnętrznych torów; innovation traci jedną. Nowy moduł codec-pool dał zero dodatkowych par. |
| Pełne zapisane IQ prywatnej obserwacji CANVAS #5122 | 1 znana ramka | Identyczna z archiwalnym PDU i wynikiem odtworzonego toru SatNOGS. To kontrola poprawności, nie dodatkowe dane. |
| Bezstratnie odtworzone audio z tego IQ, pełny tor progresywny | 2 ramki / 528 bajtów PDU | Jedna dodatkowa ramka 264 B względem jednego sprawdzonego pakietu stacji; nie jest odzyskana z oryginalnego OGG. |
| To samo odtworzone audio po wspólnej konwersji do PCM16 | 2 ramki | Dire Wolf 1, gr-satellites 0 na identycznym pliku; pełne 994/994 zadań. Wybrany przypadek rozwojowy. |
| Oryginalne OGG #5122, pełny tor progresywny | 0 ramek | Kontrolowana kompresja Vorbis również obniżyła wynik badanych torów. Nie dowodzi to niemożności odzysku inną metodą. |
| Zakończona prywatna kampania OGG, ograniczone tory native/innovation | 0 potwierdzonych AX.25 UI | 538 pobranych nagrań, 440 skierowanych do co najmniej jednego toru, 98 nadal bez obsługi. Dire Wolf odzyskał jedno PDU PCSAT #4157. |

Wierszy nie wolno sumować: część zawiera te same pakiety wielokrotnie w innych torach. „PDU” oznacza tutaj zewnętrzny pakiet bez FCS, a nie 264 bajty nowych pomiarów naukowych. Dodatkowy pakiet CANVAS zawiera 248 bajtów pola informacyjnego; poprawny zewnętrzny FCS i zgodna struktura CCSDS nie zastępują interpretacji jego danych użytkowych.

Starsze 127/86/44 to wynik [zachowanego porównania](/home/ubuntu/telemetry-yield/reports/satnogs-today20-threeway-20260910.md), nie wynik tej nocy. W szczególności brak pakietu w uruchomionych dekoderach porównawczych nie dowodzi jego braku w całej publicznej bazie.

Aktualniejszym wynikiem **pełnej konfiguracji na tym zbiorze rozwojowym** jest [139/86/44](/home/ubuntu/telemetry-yield/reports/innovation-v2-benchmark-20260910.md), czyli opisowo około 61,6% więcej par niż Dire Wolf. Ponownie sprawdzono sumy i różnice zbiorów we wszystkich 20 kompletnych wynikach. To tylko cztery grupy nakładających się przelotów; nie jest to oszacowanie z 20 niezależnych prób. Średni czas pełnego progressive wynosił 328,26 s wobec 3,67 s Dire Wolfa na współdzielonym hoście. Samo włączenie dodatkowego codec-pool nie poprawiło wyniku wcześniejszego innovation.

Objętość tych par PDU bez FCS wynosi 36 696 B dla pełnego progressive, 22 704 B dla Dire Wolfa i 11 616 B dla gr-satellites. Zysk pełnego progressive względem połączonych wyników obu porównań to 53 × 264 = **13 992 B zewnętrznych pakietów**, liczonych oddzielnie w każdej obserwacji. Nie jest to liczba globalnie nowych bajtów pomiarowych.

Końcowe [ponowne rozliczenie zbiorów](/home/ubuntu/telemetry-yield/work/decoder-readiness-20260911/old20-paired-yield-analysis-v6.json) potwierdza zysk w **7/20 nagrań, czyli 35% tego zbioru**, utratę w 0/20 i brak różnicy w 13/20. To nie „35% ogólnej skuteczności” ani prognoza dla innych satelitów. Po usunięciu powtórzeń między nagraniami **41 różnych PDU, 10 824 B**, nie wystąpiło nigdzie w wynikach obu dekoderów odniesienia dla tych 20 kompletnych obserwacji. Nie sprawdzono ich nieobecności w całej bazie SatNOGS. Liczby 53 dodatkowych par, 47 różnych PDU dodatkowych gdziekolwiek i 41 PDU nieobecnych wszędzie w porównaniu oznaczają trzy różne rzeczy.

Konserwatywne grupowanie czasowe daje tylko cztery grupy dla nakładających się obserwacji, trzy przy przerwie do 30 minut i dwie przy przerwie do 120 minut. Zgodnie z planem nie podajemy przedziału niepewności poniżej dziesięciu grup; ten próg sam w sobie nie dowodzi niezależności.

[Rozliczenie kosztów](/home/ubuntu/telemetry-yield/reports/paired-costs-accounting-20260911.md) odtworzono z 100 zachowanych procesów. Pełny progressive zużył łącznie 10 115,19 s CPU użytkownika i 231,80 s CPU systemu; Dire Wolf 70,39 s i 0,94 s; gr-satellites 127,48 s i 20,27 s. Czasy ścienne na nagranie pozostają 328,26 / 3,67 / 4,15 s. Są to pomiary współdzielonego hosta, bez pobierania i przygotowania audio, a nie równobudżetowa przewaga wydajnościowa. Nowy analizator oddziela koszty końcowej próby od wszystkich zachowanych ponowień; tutaj ponowień nie było, a timeouty i braki sprawdzono na jawnych testach.

## Uczciwe porównanie dodatkowej ramki

[Wspólny plik PCM16](/home/ubuntu/telemetry-yield/work/decoder-readiness-20260911/lossless-shared-half-pcm16.wav) pochodzi z odtworzonego bezstratnego audio IQ, ma 48 kHz i jeden kanał. Zastosowano ustalony mnożnik 0,5 przed kwantyzacją; kontrola nie wykryła przesterowania, błąd kwantyzacji nie przekroczył połowy LSB. Nie jest to matematycznie bezstratna konwersja float→PCM16.

W [kompletnym porównaniu](/home/ubuntu/telemetry-yield/work/decoder-readiness-20260911/shared-lossless-comparison-v2/result.json) nasz pełny tor odzyskał dwie ramki w 154,50 s; Dire Wolf jedną w 2,20 s, gr-satellites zero w 2,40 s. Uruchomienia miały wspólne wejście, lecz nie równy koszt obliczeń. To czasy z tego hosta, ze startem procesu, bez przygotowania wejścia i bez gwarancji izolowanego pomiaru wydajności.

Pierwszy przebieg porównania zachowano jako niekompletny: wrapper błędnie wymagał UTF-8 od binarnego tekstu monitorującego Dire Wolfa. Poprawka używa udokumentowanej obsługi binarnego logu, pozostawiając autorytatywny parser hex ścisłym. 17 testów wrappera i jego zależności przeszło, w tym odrzucenie uszkodzonego hex oraz zmienionego programu porównawczego. Całe porównanie następnie uruchomiono ponownie do nowego katalogu.

Niezależny [audyt dwóch ramek na float](/home/ubuntu/telemetry-yield/work/canvas-reference-audit-20260911/progressive-two-frame-audit.json) potwierdził wszystkie 994 zapisy zadań, wejście, sesję, strukturę pakietów i faktycznie odebrane FCS. Dodatkowe PDU ma SHA-256 `dbe14147d44f34488eefe3ed8d892392996b5e03b08502d569345d4600867360`. [96 sparowanych prób](/home/ubuntu/telemetry-yield/work/canvas-reference-audit-20260911/matched-slicer-ablation.json) pokazało lokalny zysk estymacji sekwencji nad prostym progowaniem przy tych samych zegarach, próbkach i modelu. To dowód działania mechanizmu w jednym przypadku, nie dowód jego nowości w literaturze.

Następnie osobny [audyt wspólnego PCM16](/home/ubuntu/telemetry-yield/work/canvas-reference-audit-20260911/shared-pcm16-comparison-independent-audit.json) potwierdził 2/1/0: wszystkie 994 zapisy native, odebrane FCS obu ramek, dokładny pakiet z binarnego logu Dire Wolfa, rzeczywiście pusty KISS gr-satellites oraz zgodność wejścia i poleceń.

## Zakończenie prywatnej kampanii

Stan oryginalnych, niezmienionych wyników: **436 obserwacji kompletnych, 4 częściowe, 98 nieobsługiwanych**, razem 538. Dodatkowe 207 obserwacji zastępuje wyłącznie wcześniejsze wpisy „brak profilu”. Cztery częściowe przypadki naprawiono i powtórzono oddzielnie; starych wyników nie nadpisano.

| Tor w oryginalnych dwóch partiach | Kompletne próby | Niekompletne | Nieobsługiwane wśród 440 skierowanych | Potwierdzone pary AX.25 UI |
|---|---:|---:|---:|---:|
| Nasz native audio | 402 | 2 | 36 | 0 |
| Nasz innovation | 300 | 2 | 138 | 0 |
| Dire Wolf | 392 | 0 | 48 | 1 |
| gr-satellites | 438 | 2 | 0 | 0 |

Liczba kompletnych prób nie oznacza liczby nagrań zawierających sygnał. Nie wszystkie formaty obsługiwane przez gr-satellites są AX.25 UI, dlatego ostatnia kolumna nie oznacza całkowitej ilości dowolnej telemetrii.

Rozliczenie [sprawdzono bez zmieniania wyników](/home/ubuntu/telemetry-yield/work/decoder-readiness-20260911/private-campaign-reconciliation-v2.json): ponownie odczytano hashe źródeł, metadanych, wyników i artefaktów kompletnych torów; sprawdzono rozłączność faktycznie przetworzonych partii, liczniki i strukturę UI. Ten audyt agregacji nie jest ponownym niezależnym sprawdzeniem odebranego FCS z zewnętrznych dekoderów, które FCS usuwają.

## Dodane i naprawione komponenty

- Naprawa normalizacji: zachowanie niezerowych, skończonych reszt sygnału, gdy skala percentylowa wynosi zero. Wszystkie 28 wcześniej odrzucanych okien przeszło; ponowne dekodowanie #3928 i #3929 zakończyło się bez ramek. Poprawa niezawodności, nie zysk telemetrii.
- Jawne przypisanie wersji programów potomnych w dispatcherze, sprawdzanie hashy przed i po uruchomieniu. Brak definicji gr-satellites nie jest utożsamiany z brakiem natywnego toru SatNOGS.
- Usunięcie nieaktualnych powiązań pomocniczych Codec2 w dwóch profilach offline. Nie usunięto ani nie zmieniono podstawowej demodulacji.
- Walidator zagnieżdżonego KISS Taurusa: 57/82 zgłoszone fragmenty nie są potwierdzoną telemetrią. Syntetyczny szum wygenerował 48 podobnych fragmentów. Żadnego z nich nie wliczono do zysku.
- Kontrole dodatnie/ujemne, niezależne sprawdzanie CRC i zgodności wejścia oraz ochrona przed uznawaniem niepełnych wyników za zero.
- Eksperymentalny zegar M&M i wersja okienkowa. To znana metoda; nie uzyskała dodatkowego zysku na OGG, więc nie została włączona jako „nowy lepszy” domyślny tor.

Szczegóły: [naprawy runtime](/home/ubuntu/telemetry-yield/docs/decoder-runtime-repair-v1.md), [Taurus](/home/ubuntu/telemetry-yield/reports/taurus-validation-20260911-v1.md), [diagnoza IQ/OGG](/home/ubuntu/telemetry-yield/reports/canvas-reference-audit-20260911.md).

## Kontrola regresji i fałszywych wyników

Aktualna biblioteka: **313 zaliczonych testów, 5 jawnie pominiętych**. Osobne narzędzia mają własne liczniki testów; nie sumujemy powtarzających się testów jako niezależnych dowodów.

Po naprawie przeszło także **15/15 testów integracyjnych CLI**, w tym przerwanie/wznowienie, odrzucenie błędnych formatów i zgodność wyniku przy równoległym przetwarzaniu. Pierwszy test odseparowanej kopii źródeł wykrył błąd pakowania: brak ośmiu historycznych plików, których hashe sprawdzają dwa testy pochodzenia wzorców. Wynik tej próby to 311 zaliczonych, dwa niezaliczone i pięć pominiętych testów biblioteki, a nie udana reprodukcja. Pliki referencyjne dodano do wersji 2 paczki; nie są wykonywane przez Python.

**Powtórzenie na nowej, osobno wypakowanej paczce zakończyło się pomyślnie o 09:18 UTC:** 313 testów biblioteki i 15 testów CLI, zero błędów, pięć jawnie pominiętych testów. Użyto świeżego katalogu kompilacji, ale istniejącego kompilatora i cache zależności tego samego hosta. To odtwarzalność wewnętrzna rdzenia Rust, nie niezależna reprodukcja całego środowiska pięciu dekoderów. [Log i pokwitowanie](/home/ubuntu/telemetry-yield/work/decoder-readiness-20260911/clean-core-reproduction-v2.json).

Sprawdzenie luki PCSAT #4157: ponownie przygotowany PCM16 ma dokładnie hash historycznego wspólnego wejścia. Naprawiona wersja z bankiem `full` oraz bankiem `burst` zakończyła po 216/216 okien, bez błędów, ale oba przebiegi nadal dały zero PDU. Samo poszerzenie tego przeszukiwania nie naprawiło utraty pakietu AFSK.

Naprawiona wersja zachowała 3/3 ramki publicznej obserwacji #14967362 i semantyczne wyniki wszystkich 1596 zadań względem starego nieprzerwanego przebiegu. Różnice to celowo zmieniony program/sesja oraz czas, nie wynik dekodowania. [Potwierdzenie](/home/ubuntu/telemetry-yield/work/decoder-runtime-repair-20260911-v1/public14967362-candidate-receipt.json).

Cztery pełne kontrole ujemne, każda 600 s (biały, różowy i brązowy szum oraz dwuton), zakończyły po 1393/1393 zadań, każda z zerowym wynikiem. To **40 minut syntetycznych zakłóceń**, nie dowód zerowego odsetka fałszywych ramek w terenie. Bez prawdziwych pakietów nie zostają także uruchomione wszystkie ścieżki wymagające poprawnej ramki kotwiczącej.

Następnie do każdej z tych samych czterech ścieżek szumu wstawiono sześć sekund jednej znanej transmisji. Każdy pełny przebieg odzyskał wyłącznie znane PDU, bez nieoczekiwanych ramek. [Niezależny audyt](/home/ubuntu/telemetry-yield/reports/anchored-controls-regression-audit-20260911.md) potwierdził wszystkie zapisy zadań, odebrane FCS i faktyczne wykonanie po 1824 prób w każdej z dwóch gałęzi korzystających z jednej transmisji wzorcowej. Gałęzie wymagające dwóch rozłącznych transmisji pozostawały poprawnie pominięte; dla nich uruchomiono osobny test z dwiema kopiami. Ponowne użycie szumu nie zwiększa niezależnej ekspozycji z 40 do 80 minut.

Ten sam audyt potwierdził regresję naprawionego innovation na #14967362: dokładnie cztery wcześniejsze ramki, zero dodanych i zero utraconych, wszystkie z niezależnie poprawnym CRC. Ponowna konwersja oryginalnego OGG odtworzyła identyczne wejście PCM16. Nie zaakceptowano żadnego modelu codec-pool, więc nie jest to jego kontrola dodatnia.

**Zakończono także dwie transmisje wzorcowe:** cztery pełne przebiegi, po 1393 zadania, tylko oczekiwany pakiet i zero nieoczekiwanych. Każda z dwóch gałęzi wielowzorcowych faktycznie wykonała po 408 prób w 17 oknach każdego nagrania. Audyt sprawdził rozłączne źródła, odstępy czasowe, właściwą generację modeli, pełne banki prób i CRC. [Pokwitowanie](/home/ubuntu/telemetry-yield/work/canvas-reference-audit-20260911/two-anchor-control-suite-independent-audit-v1.json). To zamyka ten konkretny brak pokrycia testami syntetycznymi; nie dowodzi skuteczności codec-pool ani odporności na wszystkie zakłócenia terenowe.

## Następny zamrożony etap

Metadane publicznego zbioru doprowadzono do rzeczywistego końca: 216 stron i 5389 rekordów. Ustalony wcześniejszym rankingiem wybór obejmuje 500 obserwacji z 215 stacji. **To na razie wybór metadanych, nie ukończony benchmark.** [Aktualny audyt ekspozycji](/home/ubuntu/telemetry-yield/reports/innovation-current-exposure-audit-20260911.md) wykluczył 234 kandydatów z powodu bliskości czasowej do wcześniejszych badań tego samego satelity. Pozostało 266 propozycji, ewentualnie 67 po dodatkowym rozdzieleniu czasowym wewnątrz zbioru. Próg dwóch godzin jest konserwatywnym przybliżeniem, nie obliczeniem orbit. Brak pełnego przypisania części historycznych artefaktów nadal blokuje uznanie tego wyboru za niezależny; niczego nowego z tej puli nie pobrano ani nie zdekodowano.

Niezależny [przegląd runnera benchmarku](/home/ubuntu/telemetry-yield/work/canvas-reference-audit-20260911/innovation-runner-independent-review-v1.md) odtworzył trzy luki w przyjmowaniu wyników: brak kontroli rzeczywistej kompletności wszystkich zadań, brak ścisłego powiązania planu innovation oraz zbyt słaba kontrola procesu przy wznawianiu. Testy te wykazały możliwość zaakceptowania niepoprawnych artefaktów, nie fakt sfałszowania wcześniejszych wyników. **Poprawki zostały zakończone**: runner v3 zaliczył 34 testy i oddzielną kontrolę istniejących wyników. [Niezależny przegląd końcowych zabezpieczeń](/home/ubuntu/telemetry-yield/reports/benchmark-gate-independent-review-20260911.md) objął również związanie wersji modułów, wejścia, wznowienia i pobrania z właściwymi zapisami.

[Konfiguracja odbiornika jest zamrożona](/home/ubuntu/telemetry-yield/work/decoder-readiness-20260911/receiver-freeze-reviewed-v2.json): najnowszy główny audyt ponownie sprawdził 255 plików oraz wspólny hash wybranych modułów. To nie jest pozwolenie na uznanie zbioru za niezależny ani hermetyczny obraz całego systemu. Najnowsze pobieranie v8 zaliczyło 22 testy; prawdziwa, niedopuszczona propozycja 266 rekordów została odrzucona przed stworzeniem katalogu wynikowego i pobraniem audio. Osobny wcześniejszy test transportu ponownie pobrał tylko jeden już znany plik rozwojowy #14967362: poprawny plik przyjęto, 11 zmienionych zapisów odrzucono. Nie były to nowe dane testowe.

Analizator wyniku v6 zaliczył 18 testów, analizator kosztów 12. Zliczanie dni używa UTC i zachowuje wszystkie siedem dni nawet wtedy, gdy żadna obserwacja nie ma kompletnego wyniku. Braków nie zamienia się w zero odzyskanej telemetrii. Liczniki testów tych narzędzi nie są sumą niezależnych prób naukowych.

Ostatni ograniczony [audyt pochodzenia](/home/ubuntu/telemetry-yield/reports/innovation-lineage-independent-followup-20260911.md) wyjaśnił 475 z 876 wcześniej nierozliczonych artefaktów; **401 nadal wymaga przypisania**. Nie są to 401 nowych obserwacji. Historyczna propozycja 266 pozostaje niedopuszczona.

Jako oddzielny plan zarejestrowano obserwacje z **12–18 września 2026 UTC**, z zamknięciem okna 19 września o 00:00 UTC, maksymalnie 500 nagrań CANVAS, bez selekcji według wyniku dekodowania. [Aktualna rejestracja analizy](/home/ubuntu/telemetry-yield/work/innovation-prospective-20260912-v2/analysis-registration-v3.json) wiąże dokładne narzędzia i z góry określone miary. Limit 500 jest praktyczny, nie wynika z wykazanej mocy statystycznej. **To lokalnie utrwalony plan, nie zewnętrzna prerejestracja i nie wykonany test.**

Zaimplementowano pobieranie i powtórne sprawdzanie kompletnych metadanych oraz obsługę dokładnego przyszłego planu w pobieraniu OGG, nadal za obowiązkową kontrolą dopuszczenia. [Końcowa integracja](/home/ubuntu/telemetry-yield/reports/prospective-api-integration-20260911.md) poprawia nazwy filtrów API przed pozyskaniem próbek, bez zmiany logicznego wyboru danych. Nowy moduł metadanych zaliczył 13 testów; rzeczywista próba uruchomienia została zatrzymana przez kontrolę daty, bez żadnego GET metadanych obserwacji ani pobrania nagrania. Dodatnie przypadki nowych testów HTTP są jawnie lokalnymi przykładami, nie wynikiem prawdziwego przyszłego pozyskania danych. Najnowsze poprawki integracji sprawdził główny agent; wcześniejsze niezależne przeglądy nie są przedstawiane jako przegląd nowych wersji.

**Nie zaplanowano zadania, które samo wystartuje 19 września.** Zakończone testy kontrolne nie pracują dalej w tle. Następny etap wymaga albo domknięcia pochodzenia historycznej puli, albo zamknięcia przyszłego okna, sprawdzenia kompletnego pozyskania i niezależnego audytu rankingu/ekspozycji. Dopiero wtedy wolno dopuścić zbiór i wykonać pięć torów. Wynik może być negatywny i nie będzie poprawiany doborem wygodniejszych nagrań.

Przed produkcją pozostają m.in. pozytywne kontrole każdego deklarowanego protokołu, realne zakłócenia, zachowanie przy przerwaniach i czysta instalacja. Przed wysłaniem publikacji pozostają niezależne wyniki, publicznie odtwarzalny pakiet, dane autorów i odpowiedzialny przegląd człowieka. [Roboczy tekst SoftwareX](/home/ubuntu/telemetry-yield/reports/decoder-softwarex-working-draft-20260911.md) nie ukrywa tych ograniczeń; niczego nie wysłano do czasopisma.

Graphify pomógł wskazać wcześniejsze rozróżnienia pojęć, ale jego starszy graf nie obejmuje bieżących narzędzi Rust. Decyzję oparto na bezpośrednim audycie kodu i rzeczywistych artefaktów.

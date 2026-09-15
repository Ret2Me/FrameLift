# Plan oceny dekodera progresywnego — 2026-09-10

Status: protokół i narzędzie testowe, nie wynik eksperymentu potwierdzającego
przewagę. Nowa kohorta nie została jeszcze pobrana ani przeanalizowana w ramach
tego planu. Nie ma podstaw do deklaracji gotowości publikacyjnej. Współpraca
kilku stacji w demodulacji jest poza zakresem; grupowanie ich obserwacji służy
jedynie uczciwej analizie statystycznej.

## Co już wiemy, a czego nie wolno uznać za holdout

`work/satnogs-today20-20260910-v1` zawiera 20 znanych obserwacji CANVAS,
GMSK 9600. Końcowe zweryfikowane liczby ścisłych par obserwacja–PDU to
127/86/44 dla dotychczasowego Rust/Dire Wolf/gr-satellites. Nasz dekoder nie
odzyskał jednej ramki Dire Wolfa. Różnica kosztu CPU była duża. Wynik ten
jest odtąd zbiorem **rozwojowym**, również jeśli nagrania zostaną ponownie
zakodowane do OGG, podzielone na okna lub pogorszone sztucznym szumem.
Narzędzie odrzuca oznaczenie tych 20 ID jako walidacji lub holdoutu.
Źródłem liczb jest `reports/satnogs-today20-threeway-20260910.md` i zamrożony
`verified/summary.json`, SHA256
`9a6b09606942762705a3099c4c88e4f948d0ed400483e3d5b91f7330390714fe`.

## Hipotezy i kolejność

1. Ślepy start daje prawidłowe dodatkowe PDU w nagraniach bez pierwszej
   poprawnej ramki. Przed oceną grupy „bez ramki” zamrozić definicję bazowego
   przebiegu; nie wybierać jej według wyników ulepszonego wariantu.
2. Kilka rozłącznych, wiarygodnych kotwic kanału zwiększa odzysk względem
   pojedynczej najbliższej kotwicy. To nie jest łączenie sygnałów różnych stacji.
3. Korzyść utrzymuje się przy zadanym czasie ściennym, a osobna analiza
   pokazuje relację z kosztem CPU. Ten sam limit ścienny nie dowodzi równego CPU.
4. Szybka sesja wznowiona do końca daje ten sam skończony zbiór ramek co
   pełna sesja tej samej polityki. Samo zatrzymanie po 3 s nie oznacza analizy
   całego nagrania. Liczyć przygotowanie wejścia, sprawdzanie checkpointów,
   start procesu, zapis i czas przerwanego zadania.

Najpierw testy jednostkowe i rozwój na znanych danych, następnie zamrożenie
źródeł, pliku wykonywalnego, konfiguracji, parserów, kolejności zadań,
hipotez, metryk, reguł wykluczenia i analizy statystycznej. Dopiero potem
pozyskanie nieoglądanych wyników. Błąd parsera można poprawić wyłącznie z
audytowalnym zachowaniem surowych wyjść i ponownym odczytem wszystkich ramion;
zmiana DSP po obejrzeniu testu wymaga nowego holdoutu.

## Podział i dobór nowych danych

Podstawowy test: wszystkie zakończone obserwacje ze z góry ustalonego przedziału
UTC, publiczne audio, jawnie wspierane profile. Nie filtrować po statusie,
liczbie ramek, waterfallu ani wyniku któregokolwiek dekodera. Najpierw pełna
paginacja metadanych i zamrożona lista ID; potem pobieranie. Błąd pobrania,
procesu lub parsera nie jest zerowym odzyskiem. Raportować licznik każdego
etapu i wszystkie nieudane przypadki.

Wszystkie okna, wersje OGG/WAV/IQ i obserwacje tego samego satelitarnego przelotu
muszą należeć do jednego podzbioru. Identyczne SHA256 nie mogą przekraczać
granicy podziału. Przelot musi mieć ID wspólne między stacjami, nadane z
metadanych orbitalnych/czasowych przed dekodowaniem; identyfikator obserwacji
nie jest identyfikatorem niezależnego przelotu. Kontrole pochodzące z jednego
nagrania dziedziczą jego grupę, nawet jeśli mają inne bajty i inne ID.

Pierwszy test może dotyczyć przyszłych przelotów znanych satelitów, ale daje
tylko wniosek wewnątrz tych profili. Osobno zamrozić test na niewidzianych
stacjach; dla wniosku o przenoszeniu między misjami potrzebny jest dodatkowy
podział po satelicie/profilu. Narzędzie egzekwuje `pass` oraz opcjonalnie
`station`/`satellite` jako osie bez przecieku. Sam zapis deklaracji
`previously_inspected: false` nie jest dowodem nieoglądania danych: potrzebny
jest dziennik akwizycji i zamrożenia. Nie twierdzić, że jeden profil potwierdza
obsługę wszystkich protokołów, CCSDS lub surowego IQ.

Rozmiar przyszłego holdoutu ustalić przed uruchomieniem na podstawie
pilotażowej zmienności **między przelotami**, minimalnego istotnego praktycznie
zysku i docelowej szerokości przedziału ufności. Nie kończyć pobierania, gdy
pojawi się korzystny wynik. Liczba obserwacji nie zastępuje liczby niezależnych
grup. Zachować także z góry określony przedział czasowy, jeżeli obserwacji
będzie mniej od oczekiwanej liczby.

## Ablacje i rzeczywisty koszt

| Polityka | Dotychczasowe gałęzie | Ślepy start | Kilka kotwic |
|---|---|---|---|
| `existing` | tak | nie | nie |
| `blind` | tak | tak | nie |
| `multi` | tak | nie | tak |
| `both` | tak | tak | tak |

Domyślna macierz narzędzia to 3 s i 60 s, cztery polityki, trzy powtórzenia,
jeden wątek, nowe sesje w każdej komórce. Kolejność polityk rotuje; nagrania
są przetwarzane szeregowo. Czasy obejmują cały proces dekodera; pobieranie i
wspólna konwersja są raportowane oddzielnie. Nie czyścić systemowego page
cache na współdzielonym serwerze. Opisać ciepły/zimny cache i obce obciążenie;
powtórzyć pomiary wydajności na wyłącznym hoście przed mocnym twierdzeniem.

Raportować zmierzony czas ścienny, user+system CPU całej grupy procesów, RSS,
przekroczenie deklarowanego budżetu, liczbę zakończonych zadań, udział nagrania
objęty poszczególnymi technikami i czas do pierwszej poprawnej ramki. Znacznik
`complete` odnosi się do skończonego banku zadań, nie do odzyskania wszystkich
ramek fizycznie obecnych w eterze.

Przy równym limicie czasowym dodatkowa technika może wypchnąć inną z budżetu,
więc raportować nie tylko zysk netto, lecz także ramki bazowe pominięte w
ograniczonej sesji. Przy pełnym zakończeniu addytywna unia powinna zachowywać
ramki istniejących gałęzi. Testy wznawiania muszą porównywać pełne PDU, nie
sam licznik, i objąć przerwanie zapisu oraz zmianę wejścia/polityki.

Osobny etap porównania z Dire Wolf i gr-satellites wymaga świeżego wykonania
na tych samych całych PCM16 WAV i zamrożonych wersjach/konfiguracjach.
Stare czasy z innego obciążenia hosta nie są pomiarem przy równym budżecie.
Podstawowe baseline'y: `atest -B 9600 -F 0 -h` i oficjalny FSK9600/AX.25 G3RUH
gr-satellites, bez podstawiania audio za IQ. Dla większego budżetu można
użyć tylko zamrożonych sensownych wariantów baseline'u, wybranych na zbiorze
rozwojowym; powtarzanie identycznego deterministycznego dekodowania nie jest
mocniejszą metodą. Wykreślić granicę osiągalnego odzysku względem CPU i czasu,
bez dopasowywania konfiguracji osobno do wyników każdego nagrania.

`progressive_evaluate` obecnie wykonuje cztery natywne ablacje przy równym
**deklarowanym** limicie czasu i zapisuje faktyczny CPU. Nie egzekwuje limitu
CPU ani nie uruchamia zewnętrznych dekoderów. Wynik ma jawne
`equal_actual_cpu_cost_established: false`; nie wolno zamieniać tego w
twierdzenie o zysku przy identycznym koszcie CPU. Osobny eksperyment CPU musi
zamrozić pułap zużycia całej grupy procesów i porównywać wyłącznie artefakty
utrwalone przed jego osiągnięciem, z pełnym rozliczeniem przerwanych zadań.

## Poprawność, fałszywe alarmy i metryki

Podstawowa jednostka to unikalna para obserwacja–PDU, bez FCS. Osobno liczyć
wszystkie wyjściowe PDU oraz wspólny ścisły podzbiór AX.25 UI. Zachować pełne
odebrane FCS nowego dekodera i sprawdzać niezależnym algorytmem bitowym.
Nie doklejać wyliczonego FCS do wyjść baseline'ów, które go usuwają.
„Wszystkie PDU” oznacza wszystkie PDU faktycznie emitowane przez dany
dekoder, nie obietnicę obsługi każdej struktury/protokołu.

Liczyć globalnie różne PDU osobno od sumy po obserwacjach. Zysk ponad unię
baseline'ów nie oznacza nowości w całym archiwum SatNOGS. Powtórzenia czasowe
służą ocenie stabilności i kosztu, a ich unia nie jest średnim odzyskiem.

CRC nie jest samodzielnym dowodem prawdy przy milionach hipotez. Potrzebne są:

- niezależne syntetyczne transmisje ze znanymi, wcześniej wygenerowanymi
  bitami/payloadami, kanały i ziarna niewidziane przy strojeniu; dokładne
  porównanie bajtów, PER/recall, a po ustaleniu wyrównania również BER;
- szum bez transmisji: biały i kolorowy, zakłócenia impulsowe, nośne, DC,
  przesterowania oraz reprezentatywne puste nagrania terenowe; przetestować
  ten sam pełny bank technik i największy dopuszczony czas, nie tylko 3 s;
- dla nowych PDU terenowych weryfikacja niezależna od ścieżki wyszukiwania:
  struktura misji, licznik/sekwencja/czas oraz, gdzie dostępne, osobny dowód
  po zakończeniu dekodowania. Payload referencyjny nie wchodzi do wyszukiwania.

Raportować każde nieoczekiwane PDU, liczbę obserwacji z fałszywym alarmem,
liczbę hipotez i ekspozycję w godzinach; rozdzielić fałszywy alarm na godzinę
od prawdopodobieństwa fałszywego alarmu na obserwację. Zero błędów nie oznacza
zerowego ryzyka. Przy założeniu procesu Poissona i zerowej liczbie zdarzeń
jednostronna granica 95% wynosi `-ln(0.05) / T`; założenie i zależność
syntetycznych wersji trzeba jawnie wskazać. Narzędzie podaje surowe liczniki
kontroli i dokładne trafienia payloadów; nie wylicza BER bez znanych pozycji
bitów ani nie uznaje pustego terenowego audio za potwierdzony czysty szum.

Przedziały ufności liczyć parami, przez bootstrap całych przelotów, po
uśrednieniu powtórzeń w obserwacji; nie traktować ramek ani okien jako
niezależnych pomiarów. Narzędzie wykonuje 5000 replik percentylowych ze
stałym ziarnem i zwraca brak CI dla mniej niż dwóch grup. Te CI są
eksploracyjne, bez korekty wielokrotnych porównań. Dla publikacji wybrać
z góry jedną podstawową hipotezę i analizę potwierdzającą, pozostałe oznaczyć
jako wtórne; sprawdzić wrażliwość na współzależność stacji i satelitów.

## Narzędzie i format manifestu

Kod: `examples/progressive_evaluate.rs`. Kompilacja/testy:

```text
rtk proxy /home/ubuntu/.cargo/bin/cargo test --example progressive_evaluate
rtk proxy /home/ubuntu/.cargo/bin/cargo build --release --example progressive_evaluate
```

Minimalna struktura JSON (wartości przykładowe, nie istniejący zbiór):

```json
{
  "schema": "progressive-evaluation-manifest-v1",
  "frozen_utc": "2026-09-10T18:00:00Z",
  "selection_rule": "Pełne kryteria przedziału, profilu i kolejności, bez filtrów wynikowych",
  "leakage_axes": ["pass"],
  "observations": [{
    "id": 1, "satellite": "CANVAS", "station": "station-id",
    "pass": "shared-satellite-pass-id", "split": "development",
    "kind": "field", "previously_inspected": true,
    "input": {"path": "/absolute/input.wav", "bytes": 100, "sha256": "64-lowercase-hex-digits"},
    "audio_seconds": 10.0, "expected_payloads": []
  }]
}
```

`kind` przyjmuje `field`, `noise`, `known_bits`; tylko `known_bits` wymaga
niepustych `expected_payloads` w kanonicznym małym HEX. Żadne bajty prawdy
nie są przekazywane procesowi dekodera.

```text
progressive_evaluate validate --manifest /absolute/frozen-manifest.json
progressive_evaluate run --manifest /absolute/frozen-manifest.json --binary /absolute/frozen-decoder --output /absolute/new-experiment --split development --budgets-ms 3000,60000 --repeats 3 --threads 1
progressive_evaluate summarize --experiment /absolute/new-experiment --output /absolute/new-summary.json
```

Manifest i tożsamość binarnego programu zostają utrwalone przed uruchomieniem
macierzy. Każda komórka ma własną sesję, logi, pomiar całego procesu, hash
wyniku i `record.json`. Kopia `decoder-snapshot.json` zachowuje wynik na końcu
budżetu nawet po późniejszym wznowieniu sesji dekodera. Wejście i plik wykonywalny są hashowane przed i po
uruchomieniu. Błędy pozostają w wynikach, nie znikają z mianownika.
Runner nie ponawia automatycznie przerwanych eksperymentów ani nie nadpisuje
istniejących katalogów; wznowienia dekodera są osobnym testem poprawności.
Jeśli budżet wyczerpie się jeszcze przed przygotowaniem wejścia i weryfikacją
checkpointów, poprawny wynik częściowy może nie mieć `result.json`. Runner
zachowuje go jako zero **utrwalonych** ramek oraz nieznane pokrycie zadaniami,
z osobnym licznikiem `preparation_incomplete_runs`; nie oznacza to braku ramek
w całym nagraniu. Macierz raportuje też brakujące komórki po przerwaniu
eksperymentu i nigdy nie przedstawia niepełnej macierzy jako ukończonej.

## Warunek kolejnego kroku

Za gotową podstawę mocnego artykułu uznać dopiero: zamrożony i kompletny
holdout z wiarygodnym zyskiem, ujawnione regresje, uczciwe krzywe kosztu,
niezależną kontrolę dodatkowych PDU, ekspozycję testów negatywnych i
odtwarzalne artefakty. Do wdrożenia ponadto potrzebne są testy limitów,
odtwarzania sesji po awarii, uszkodzonych wejść/cache, stabilności pamięci,
izolacji procesów i brak pozostawionych procesów potomnych. Dobry wynik
inżynierski nie dowodzi nowości naukowej; opis wkładu ma odróżniać znane
elementy demodulacji od konkretnej, zmierzonej korzyści ich nowej kombinacji.

## Wykonane kontrole narzędzia

16 testów przechodzi: 12 specyficznych dla oceny i 4 współdzielonego
uruchamiania procesów. Sprawdzono m.in. znane ID rozwojowe, przeciek przelotu
i identycznych danych, opcjonalny podział po stacji, niezależny FCS,
duplikaty komórek, brakujące pomiary CPU, oddzielenie szumu od wyników
terenowych, uśrednianie powtórzeń przed bootstrapem oraz niepełną macierz.

Kontrola integracyjna w `work/progressive-evaluation-smoke-20260910-v1`:
0,5 s deterministycznego białego szumu (ffmpeg `anoisesrc`, ziarno 20260910,
48 kHz, amplituda 0,001, PCM16). Cztery polityki przy 50/100 ms dały 8/8
poprawnych wyników „przygotowanie nieukończone”. Przy 1000 ms wszystkie
cztery ukończyły bank i wyemitowały zero PDU. Ponowna agregacja obu raportów
dała pliki identyczne bajt w bajt. To kontrola API i checkpointów, nie
istotna ekspozycja do oszacowania ryzyka fałszywych alarmów.

Wcześniejsza próba na aktywnie przebudowywanym pliku `target/debug` została
prawidłowo odrzucona przez kontrolę tożsamości binarnej i zachowana w
`experiment/`. Próby z 1 ms ujawniły dolną granicę API 50 ms; runner sprawdza
ją teraz przed wykonaniem. Do miarodajnych prób używa się kopii zamrożonego
programu, a nie pliku docelowego zmienianego podczas kompilacji.

SHA256 kontrolnych raportów:

- `release-experiment/summary.json`:
  `912de383a88a643cb48737b5f5efca8a2474704fa7ffe2818b55e56cd36a0c9d`;
- `release-1000ms-experiment/summary.json`:
  `40c321abdbd1bbd007faa67e7017cc08b48939ee3743f182bba9af2bf69fccc5`.

## Wynik diagnostyczny na prawdziwym nagraniu 14967361

Zakończono 8/8 komórek: cztery polityki × 3 s/60 s, jedno powtórzenie,
cztery wątki. Wejście to niezmieniony, wspólny PCM16 WAV trwający 536,985 s,
SHA256 `1e3eb29f21afbc520e55bccad2496436635a8d01ec69705d4dd22e938246ae9b`.
To celowo wybrany **znany przypadek rozwojowy**, na którym poprzedni dekoder
Rust miał 0 PDU, Dire Wolf 1, a gr-satellites 0. Nie jest to holdout.
Zamrożony dekoder progresywny v2 ma SHA256
`7ca889ee674701db09ffe2f241dc59ab4b7102837d849d6ba8e66b61207498c6`.

Wszystkie cztery zimne przebiegi 3 s zakończyły budżet podczas przygotowania
wejścia: zero utrwalonych ramek, pokrycie analizy nieznane. Czas samego
nadzorowanego wywołania wynosił 2,975–3,016 s, zewnętrzny pomiar procesu
3,025–3,110 s (czytnik procesu odpytuje co 100 ms). Nie jest to gwarancja
twardego czasu rzeczywistego ≤3,000 s ani użyteczny szybki odzysk na każdym
długim pliku.

| Polityka, 60 s | Ukończone zadania / cały bank | PDU | Rzeczywisty CPU procesu |
|---|---:|---:|---:|
| existing | 227/534 | 0 | 179,89 s |
| blind | 301/712 | 0 | 225,49 s |
| multi | 221/712 | 0 | 198,71 s |
| both | 237/890 | 0 | 203,86 s |

Minutowe przebiegi wykonały 178 zadań `quick` oraz część
`baseline-remainder`; nie zdążyły jeszcze przejść do nowych technik. Różnice
w pokryciu przy tym samym limicie czasu nie dowodzą różnic algorytmicznych:
host był współdzielony, równolegle działały inne testy. Jest to konkretne
ograniczenie obecnej fazowej kolejności zadań — minutowy tryb nie gwarantuje
użycia wszystkich technik na każdym nagraniu. Następna optymalizacja musi
objąć koszt zimnego przygotowania i wcześniejsze dopuszczanie nowych gałęzi
do budżetu, przy zachowaniu poprawności zależności i wznawiania.

Następnie wznowiono **tę samą** sesję `both` po 60 s do końca, bez powtórnego
uruchomienia ukończonych zadań. Dodatkowe 67,526 s wywołania i 226,897 s CPU
dało ukończenie 890/890 zadań. Łącznie z poprzednią minutą to 127,511 s
nadzorowanych wywołań i około 430,76 s CPU. Dla samej kontynuacji `/usr/bin/time`
zmierzył 67,53 s, 226,89 s CPU i szczyt RSS 249 052 KiB.

**Wynik pełny: 2 różne PDU po 264 bajty, obydwa wyłącznie z gałęzi `blind`.**
Dotychczasowe gałęzie nie miały żadnej ramki, a więc ten przypadek faktycznie
sprawdza start bez poprawnej ramki-kotwicy. Niezależny bitowy walidator CRC
w JavaScript potwierdził odebrany FCS obu wyników po zakończeniu dekodowania.
Jedno PDU jest dokładnie ramką pominiętą wcześniej przez Rust, a znalezioną
przez Dire Wolfa. Drugie nie występuje w wyjściu Dire Wolfa ani gr-satellites
**dla tego nagrania**. To 528 bajtów PDU ogółem, w tym dodatkowe 264 bajty
względem tych dwóch odtworzeń zewnętrznych. Nie jest to dowód nowości w całym
archiwum ani niezależne potwierdzenie prawdy drugiego PDU poza FCS/strukturą.
Referencje porównano dopiero po dekodowaniu, nie były wejściem wyszukiwania.

To pierwszy dodatni wynik tej konkretnej diagnostyki: naprawienie znanej
regresji 0→2 przy Dire Wolf 1. Nie wolno rozciągać go na całą dwudziestkę,
podawać jako poprawy o 100% na populacji ani twierdzić, że został osiągnięty
w 3 s lub 60 s. Potrzebny jest jeszcze niezależny zbiór i porównanie kosztu.

Artefakty: `work/progressive-evaluation-real-20260910-v1/`.
`experiment-v2/summary.json` zawiera wyłącznie zamrożoną macierz ograniczoną
czasem; pełna kontynuacja jej nie zmienia. `full-result-frozen.json` i
`full-audit.json` zawierają oddzielny wynik końcowy i porównanie dokładnych
bajtów. Ponowna agregacja macierzy podczas oraz po pełnej kontynuacji dała
plik identyczny bajt w bajt z pierwotnym raportem.

- SHA256 raportu macierzy:
  `95decb74134d8cf62b2e113dc4c3a7a8efcd81e4536d59b93346a44a0035da11`.
- SHA256 niezmienionej kopii wyniku `both` po 60 s:
  `8d78b0602ff5953a2ad4c276317d585c33bedebe493430149ecc484ac1083e64`.
- SHA256 pełnego wyniku:
  `b209fa3b176044df9bf4f9e91855dd5aff5dbc4798197c57c7b03f872050cac4`.
- SHA256 audytu pełnych ramek:
  `fd20401091965b3d2d9d1bbad73ec97a1dd32a17271f2958acc7ca00071b9e13`.

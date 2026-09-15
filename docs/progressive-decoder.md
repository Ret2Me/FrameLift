# Progresywny dekoder audio — wersja eksperymentalna

Nowe polecenie `decode-progressive` wykonuje skończony bank małych zadań,
utrwala odebrane ramki i pozwala kontynuować tę samą sesję. Dotychczasowe
`decode-adaptive-audio` zachowuje swój bank i pozostaje punktem odniesienia.
Nie ma tu łączenia sygnałów różnych stacji.

Nowe BPSK/QPSK/OQPSK, CSP/AOS/USLP i RS/LDPC są dostępne przez generyczne
`decode` / `decode-metadata`, a nie ten bank audio. Zobacz
[zakres i konfigurację PSK/FEC](psk-and-channel-coding.md).

## Uruchamianie

Poniższe ścieżki wejścia i sesji są przykładami do zastąpienia własnymi.
Katalog nadrzędny sesji musi istnieć; pierwsze uruchomienie wymaga nowej nazwy.

```bash
/home/ubuntu/telemetry-yield/target/release/telemetry-yield-rs decode-progressive --input /absolute/capture.ogg --output /absolute/session --mode quick --threads 4
/home/ubuntu/telemetry-yield/target/release/telemetry-yield-rs decode-progressive --input /absolute/capture.ogg --output /absolute/session --mode deep --threads 4 --resume
/home/ubuntu/telemetry-yield/target/release/telemetry-yield-rs decode-progressive --input /absolute/capture.ogg --output /absolute/session --mode full --threads 4 --resume
```

| Tryb | Budżet jednego uruchomienia | Znaczenie |
|---|---|---|
| `quick` | 3000 ms | Najpierw mały zestaw zegarów i Gardner; częściowe pokrycie jest jawne. |
| `deep` | 60000 ms | Kolejne nieukończone zadania; nie obiecuje pełnego banku dla dowolnie długiego pliku. |
| `full` | Bez limitu czasu | Kończy skończony bank; można wcześniej przerwać i wznowić. |

`--budget-ms 30000` zastępuje limit trybu; dopuszczalne 50 ms–24 h.
Budżet obejmuje przygotowanie audio, haszowanie i sprawdzenie checkpointów,
nie tylko DSP. Nadzorca zatrzymuje proces roboczy z niewielkim zapasem na
sprzątanie. Linux nie daje gwarancji czasu rzeczywistego: planowanie systemu,
operacje dyskowe, zapis końcowego potwierdzenia i wyjście CLI mogą dodać
narzut. Mierzyć także cały proces zewnętrznym zegarem. Nie twierdzimy, że
każde wywołanie zawsze wróci przed dokładnie 3,000 s.

Tryby zmieniają budżet, nie bank technik. Po szybkiej fazie kolejka przeplata
kończenie bazowego banku, ślepy start i wczesne warianty adaptacyjne.
Te wczesne warianty używają zamrożonych kotwic z szybkiej fazy, również po
wznowieniu. Końcowe warianty adaptacyjne korzystają z drugiej, pełnej generacji
kotwic. Równoległość nie narusza tych zależności. Nie wszystkie zadania muszą
zdążyć zakończyć się w minutę.

## Co jest w banku

Każde okno ma 6 s, przesunięcie wynosi 3 s. Kolejność:

1. `quick`: pierwsze 8 ocenionych na podstawie sygnału zegarów i pierwsze
   2 konfiguracje Gardner na każdym z dwóch frontendów: 20 prób/okno.
2. Przeplatana faza pogłębiona: `baseline-remainder` — pozostałe 152 zegary i
   14 konfiguracji Gardner na każdym frontendzie: 332 próby/okno,
   bez powtarzania prób z punktu 1.
   Razem z nią pracują `early-nearest`, `blind` i `early-multi`; oba wczesne
   warianty używają wyłącznie zamrożonej generacji kotwic z punktu 1.
3. `nearest`: dotychczasowe MLSE z najbliższą rozłączną kotwicą CRC, po
   ukończeniu całego bazowego banku. Obok niego działa końcowe `multi-anchor`.

Metody dodatkowe:

- `blind`: ograniczona wielostartowa estymacja kanału przez naprzemienne
   decyzje MLSE i najmniejsze kwadraty; bez pierwszej poprawnej ramki.
   Model przechodzi osobny sprawdzian dopasowania przebiegu. Bity treningowe
   są **wnioskowane**, a niski błąd przebiegu nie jest dowodem transmisji.
- `multi-anchor`: dodatkowy model ważony jakością i odległością czasową,
   z odrzucaniem niezgodnych kotwic. Fragmenty treningowe są rozłączne
   względem celu i siebie nawzajem. Jest to lokalne uśrednianie, nie pełny
   model czasowo zmiennego kanału ani współpraca stacji.

Oryginalna ścieżka `nearest` pozostaje w unii. Żadna nowa ramka ani ślepo
wyestymowany model nie staje się rekurencyjnie źródłem treningu kotwic.
Wynik zawiera odebrane FCS i strukturalnie poprawne AX.25 UI; nie naprawiamy
bitów pod CRC i nie podajemy dekoderowi referencyjnych payloadów.

`--cache-mib 2048` ustala budżet dokładnego cache przygotowania okien na proces.
Domyślnie jest to 2048 MiB; dopuszczalne 0–4096 MiB. Zero wyłącza jedynie
zatrzymywanie przygotowanych frontendów — nie wyłącza żadnej techniki ani próby.
Przy czterech równoległych procesach domyślne rezerwacje mogą łącznie sięgnąć
8 GiB, dodatkowo do pamięci roboczej. Program zachowuje dotychczasową kontrolę
oszacowania 6 GiB na proces; cache jest ograniczany pozostałym zapasem. Nie jest
to twardy limit RSS. Przy małej pamięci można podać np. `--cache-mib 512`.

`--no-blind` oraz `--no-multi-anchor` służą kontrolowanym ablacjom. Polityka
musi pozostać taka sama przez całą sesję. `--threads`, tryb i budżet można
zmienić przy wznowieniu, podobnie jak `--cache-mib`.

## Trwałość i ograniczenia

- `manifest.json`: SHA256 źródła, programu, WAV używanego do odczytu i polityka.
- Oryginalny WAV jest czytany oknami, bez bufora całego nagrania. OGG jest raz
  konwertowany do `prepared-SHA256.wav` (float32 bez dodatkowej kwantyzacji
  względem dotychczasowej konwersji); ukończona konwersja nie jest powtarzana.
- `tasks/`: niezmienne, osobno sumowane kontrolnie wyniki z pełną proweniencją.
- `result.json`: atomowo publikowana unia ramek, licznik zadań i `complete`.
- `runs/`: osobne opcje, log i potwierdzenie każdego uruchomienia.

Ukończone zadania nie są uruchamiane ponownie. Zadanie zabite przed atomowym
zapisem może zostać powtórzone. Filtrowanie i ranking zegara między etapami
korzystają z ograniczonego cache w pamięci bieżącego procesu: domyślnie do 2048 MiB
rezerwacji, dodatkowo ograniczonej zapasem oszacowania pamięci roboczej.
Cache zachowuje dokładne próbki f64 i pełny bank zegara tego samego okna,
a nie modele ani wyniki etapów. Tożsamość bitów PCM jest sprawdzana przez
SHA256, razem z częstotliwością próbkowania i szybkością symbolową.
Gdy okno nie mieści się w rezerwacji, jest przeliczane — żadne zadanie ani
hipoteza nie są pomijane. Nie jest to trwały cache frontendów: po wznowieniu
w nowym procesie przygotowanie okien jest obliczane od nowa. Rezerwacje cache
nie są twardym limitem RSS całego procesu.
Migawka może po przerwaniu opóźniać się o ostatnie zatwierdzenia; wznowienie
odtwarza ją z autorytatywnych plików zadań. Nie porównujemy tylko liczników:
testy zgodności sprawdzają pełne bajty ramek i modele.

Sesja jest blokowana przed równoczesnym zapisem. Zmienione wejście, plik
wykonywalny, polityka lub uszkodzony checkpoint powodują odmowę wznowienia.
Gdy krótki budżet skończy się jeszcze podczas weryfikacji, potwierdzenie
zawiera `input_and_checkpoints_verified: false` i nie przedstawia poprzedniej
migawki jako aktualnego wyniku. Brak ukończonego przygotowania nie oznacza
negatywnego wyniku dekodowania całego pliku.

Zwykły timeout sprząta nadzorowany katalog roboczy, również po SIGKILL
procesu dekodera. Awaria całego hosta lub zabicie samego nadzorcy może
pozostawić katalog roboczy w `runs/`; nie jest on używany jako checkpoint.
Nie usuwać przygotowanych próbek ani zadań, jeśli sesja ma być wznawiana.
Sesje WAV nie tworzą pełnego bufora f64 na dysku. Przy wejściu OGG przygotowany
float32 WAV zajmuje około 115 MB/10 min przy 48 kHz. Każde aktywne zadanie
ładuje tylko swoje okno; każda sesja OGG ma własny plik przygotowany.
Pełna suma kontrolna wejścia jest sprawdzana przy otwarciu, a niezmienność
pliku przy każdym odczycie okna. Kontrola skończoności próbek dotyczy
przetworzonych okien; nieukończony przebieg nie poświadcza wszystkich próbek.

Obecny nowy bank obsługuje mono OGG/WAV po demodulacji FM, FSK/GMSK,
AX.25 UI plain NRZI/G3RUH, domyślnie 9600 baud. Nie należy podawać surowego
IQ jako audio. Istniejące generyczne wejścia i protokoły projektu pozostają
osobnymi ścieżkami; nie deklarujemy jeszcze ich progresywnej obsługi.

## Ocena naukowa

Plan i narzędzie ablacjne: `reports/progressive-evaluation-plan-20260910.md`
oraz `examples/progressive_evaluate.rs`. Te same limity czasu nie oznaczają
identycznego CPU. Dotychczasowe 20 obserwacji jest teraz materiałem
rozwojowym, a nie niezależnym testem potwierdzającym. Nowe bloki są znanymi
rodzinami estymacji i MLSE; samo ich połączenie nie dowodzi nowości naukowej,
dodatkowych ramek, gotowości publikacyjnej ani wdrożeniowej.

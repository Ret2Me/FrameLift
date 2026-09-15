# Adaptacyjny odbiornik sekwencyjny — implementacja i test rozwojowy

## Wynik

Nowa eksperymentalna gałąź odbiornika zwiększyła odzysk na czterech wcześniej
znanych nagraniach OGG z **78 do 112 par obserwacja–PDU**: o 34, czyli 43,6%.
Nie zniknęła żadna ramka bazowych gałęzi. Po wspólnej deduplikacji treści
czterech nagrań wynik wynosi **65 → 88 różnych PDU**, czyli 23 dodatkowe
pakiety i 6072 bajty. To rezultat rozwojowy, nie estymacja zysku dla całej
bazy SatNOGS i nie dowód nowości algorytmu.

| Obserwacja | Cztery bazowe gałęzie | Z nową gałęzią | Dodatkowe | Dodatkowe bajty PDU | Czas całego uruchomienia |
|---|---:|---:|---:|---:|---:|
| 14936407 | 16 | 20 | 4 | 1056 | 131,85 s |
| 14936415 | 17 | 23 | 6 | 1584 | 95,76 s |
| 14936424 | 19 | 31 | 12 | 3168 | 146,98 s |
| 14936444 | 26 | 38 | 12 | 3168 | 163,55 s |
| Suma par obserwacja–PDU | 78 | 112 | 34 | 8976 | — |

Bajty oznaczają całe PDU AX.25 z nagłówkiem, bez dwubajtowego FCS; nie samą
użyteczną treść aplikacyjną. W sumie indywidualnych zbiorów dodatków jest 30
różnych PDU, ale siedem występuje już w bazowym wyniku innego z tych czterech
nagrań. Dlatego przyrost wspólnej unii to **23**, a nie 30 ani 34.

Wyniki i konfiguracja są w
[katalogu eksperymentu](/home/ubuntu/telemetry-yield/work/adaptive-sequence-20260910-v1),
a zestawienie maszynowe w
[verification-summary.json](/home/ubuntu/telemetry-yield/work/adaptive-sequence-20260910-v1/verification-summary.json).

## Potwierdzenie bajtów w archiwum

Wszystkie 34 dodatkowe pary obserwacja–PDU, obejmujące 30 różnych treści
w zbiorze dodatków, są dokładnie zgodne ze wcześniej pobranymi plikami
archiwalnymi. Porównano wszystkie 264 bajty PDU, a nie jedynie nagłówki
czy numery pakietów. Tym samym potwierdzono również wszystkie 23 pakiety
stanowiące przyrost wspólnej unii czterech nagrań. Referencje odczytano
przy ocenie po zakończeniu dekodowania, nie podczas wyszukiwania ramek.

Jedna ramka, o markerze `0820c384`, początkowo nie miała dopasowania
w archiwum mniejszej kohorty. Została jednak odnaleziona w starszym
[reference-0006.bin](/home/ubuntu/telemetry-yield/work/satnogs-ogg-refinement-20260907/validation-inputs/inputs/14936397/reference-0006.bin)
obserwacji 14936397, a także we wcześniejszych wynikach obu dekoderów.
Obecnie odczytano ją zgodnie z nagrań stacji Pecna 3746 i UT4UYF/P 3925.
To dwa fizyczne odbiorniki tego samego przelotu, nie dwie niezależne emisje.
Jej SHA256 bez FCS wynosi
`b3d9d7ef7f55e985be8e63a67e1fc9358687da29b468a7045256020fbdaeb928`.

Żaden z dodatków nie jest zatem nieznanym wcześniej PDU względem wszystkich
sprawdzonych archiwów. Wynik jest dodatkowym poprawnym odzyskiem z obecnie
analizowanych plików. Pełne mapowanie dowodów:
[archive-corroboration.json](/home/ubuntu/telemetry-yield/work/adaptive-sequence-20260910-v1/archive-corroboration.json).

## Implementacja

Dodano opt-in `decode-adaptive-audio`, przyjmujący mono WAV/OGG po
demodulacji FM. Pierwsza integracja dotyczy FSK/GMSK z AX.25 UI, zwykłym
NRZI lub G3RUH; nie jest to koherentny tor IQ ani uniwersalny dekoder CCSDS.
Domyślne istniejące ścieżki i zamrożona wcześniejsza kampania nie zostały
zastąpione.

Najpierw uruchamiane są niezmienione cztery gałęzie: dwa frontendy,
każdy z pełnym bankiem 160 hipotez czasu symboli i 16 konfiguracjami
Gardnera. Z fizycznych symboli poprawnych ramek estymowany jest krótki model
kanału: trzy współczynniki impulsu i składowa stała. Brzegi pól treningowych
są wyłączone, a nakładające się symbole treningowe deduplikowane.

Model jest przenoszony wyłącznie między rozłącznymi oknami tego samego
frontendu, z odstępem co najmniej jednej sekundy i najwyżej 60 sekundami
między początkami okien. Cel otrzymuje parametry modelu i oszacowanie tempa
symboli, nie treść pakietu. Dopiero potem czterostanowy Viterbi odtwarza
sekwencję symboli. Nowa gałąź nie uczy się ponownie z własnych dodatkowych
wyników i nie zmienia bitów w celu wymuszenia zgodności CRC.

Akceptacja obejmuje odebrany FCS, niezależnie zaimplementowaną kontrolę
reszty CRC i strukturę AX.25 UI. Wynik jest sumą zbiorów bazowych i nowej
gałęzi, więc zachowanie ramek bazowych jest własnością konstrukcji.
Podwójna implementacja CRC pomaga wykrywać błędy programu, ale nie jest
drugim niezależnym dowodem radiowym.

Kod: [adaptive.rs](/home/ubuntu/telemetry-yield/rust/adaptive.rs),
[sequence.rs](/home/ubuntu/telemetry-yield/rust/sequence.rs),
[anchors.rs](/home/ubuntu/telemetry-yield/rust/anchors.rs).
Pełny protokół przed testem:
[adaptive-sequence-receiver-v1.md](/home/ubuntu/telemetry-yield/docs/adaptive-sequence-receiver-v1.md).

## Kontrola porównania

Dla każdej z czterech obserwacji potwierdzono zgodność SHA256 oryginalnego
OGG oraz wynikowego PCM/WAV z wcześniejszym `tracking-v1`. Dokładne zbiory
bajtów wszystkich czterech bazowych wariantów, nie tylko liczby ramek,
są identyczne. Stare wyniki pozostają niezmienione w
[tracking-v1](/home/ubuntu/telemetry-yield/work/receiver-improvements-20260908/tracking-v1).

Wersję programu, archiwum źródeł i wybór czterech nagrań zamrożono przed
ich ponownym dekodowaniem. SHA256 odbiornika:
`df4f06e030348ffaefaee8895ea802fd04cb205a116a699cf986b8f7ba6a427c`.
Zapisy:
[manifest](/home/ubuntu/telemetry-yield/work/adaptive-sequence-20260910-v1/release/manifest.json)
i [plan](/home/ubuntu/telemetry-yield/work/adaptive-sequence-20260910-v1/replay-plan.json).
Nie dostrajano algorytmu ani jego parametrów do wyniku tych replayów.

Łącznie użyto 204 modeli kotwic i wykonano 29 424 próby dodatkowej gałęzi.
Czas samego dodatkowego przebiegu wyniósł odpowiednio 14,16; 23,05; 21,23
i 17,36 sekundy. Czas bazowy obejmuje także estymację modeli, więc nie jest
czystym pomiarem starego odbiornika. Zadania pracowały współbieżnie, a część
z nich dzieliła procesor z kompilacją lub testem syntetycznym. Nie jest to
izolowany benchmark czasu względem SatNOGS/gr-satellites.

Zmierzony szczyt RSS procesów wynosił około 200–296 MiB. Preflight w kodzie
ogranicza estymatę PCM i pamięci roboczej do 6 GiB, ale nie obejmuje całej
retencji wyników i serializacji JSON. Nie jest to twardy limit całkowitego
RAM ani kwalifikacja produkcyjna dla maksymalnie gęstego wejścia.

## Testy poprawności i syntetyczne

Polecenie `cargo test --lib --bins --examples` zakończyło się kodem 0;
dotychczasowe celowo ignorowane testy pozostały ignorowane. Osobno przeszło
13 testów `cli_integration`. Nowy test lokalizacji spanów sprawdził 260
kombinacji obcięcia prefiksu, początkowego poziomu NRZI i odwróconej
polaryzacji; test porównania protokołu obejmuje 515 zamrożonych przypadków.

Testy detektora obejmują między innymi porównanie z wyczerpującym
przeszukiwaniem krótkich sekwencji, estymację znanego modelu, wykluczenie
samotrenowania na docelowym oknie oraz odrzucanie błędnych i osobliwych
danych. Wyjście obecnego Viterbiego to twarde ±1, nie skalibrowane LLR.

### Interfejs symbolowy

Na 64 z góry ustalonych syntetycznych pakietach z różną treścią niż kotwica
MLSE odzyskał 64/64, a slicer 28/64. Slicer otrzymał ten sam nauczony bias.
W 64 kontrolach bez pakietu, również z dostępnym modelem kotwicy, oba tory
zaakceptowały zero ramek. Nie było zaakceptowanych ramek innych niż nadane.

To test krótkiego modelu na interfejsie symbolowym, bez pełnego frontendu,
akwizycji, kodeka OGG i kanału RF. Nie wolno przenosić stosunku 64/28 na
SatNOGS. Surowy wynik:
[symbol-probe/result.json](/home/ubuntu/telemetry-yield/work/adaptive-sequence-20260910-v1/symbol-probe/result.json).

### Pełny tor PCM

Pierwszy generator `linear-isi` nie dał poprawnej kotwicy w żadnym z czterech
18-sekundowych przypadków. Wszystkie wyniki miały zero ramek i zero prób
MLSE. Kontrola pokrycia prawidłowo zgłosiła brak wykonania transferu:
[pcm-probe/result.json](/home/ubuntu/telemetry-yield/work/adaptive-sequence-20260910-v1/pcm-probe/result.json).
Nie zaliczamy tego jako testu fałszywych akceptacji MLSE ani dowodu
nieskuteczności jego algorytmu.

Wprowadzono osobno oznaczony generator `clean-rectangular`, zgodny z
istniejącą dodatnią próbką PCM: zwykłe utrzymanie fizycznego poziomu przez
pięć próbek, bez dodanego ISI. Pozostawiono perturbacje wzmocnienia, DC,
fazy i szumu oraz dwa cele zawierające wyłącznie zakłócenia. Jest to naprawa
pokrycia testu, nie poprawa odbiornika; stary generator, jego binarium i
pierwszy wynik zachowano. Wynik wariantu v2 znajduje się w osobnym katalogu
`pcm-probe-rectangular-v2`.

Wariant v2 przeszedł kontrolę pokrycia we wszystkich czterech przypadkach:
960 prób dodatkowej gałęzi, z czego 384 obejmowały region celu. Obie
nadane treści docelowe odzyskały zarówno baseline, jak i MLSE; w dwóch
celach zawierających wyłącznie zakłócenia nie pojawiły się ramki. Nie było
obcych zaakceptowanych ramek ani strat baseline. Zysk wyniósł zero,
zgodnie z przeznaczeniem tego prostego testu pokrycia. Wynik:
[pcm-probe-rectangular-v2/result.json](/home/ubuntu/telemetry-yield/work/adaptive-sequence-20260910-v1/pcm-probe-rectangular-v2/result.json).
Cztery testy jednostkowe generatora również przeszły. To wciąż mała
symulacja, nie kwalifikacja częstości fałszywych akceptacji.

## Co wynik potwierdza, a czego jeszcze nie

Potwierdzono korzyść **całej dodatkowej gałęzi** na czterech wybranych
nagraniach rozwojowych. Nie odizolowano jeszcze efektu samego Viterbiego:
gałąź zmienia także zestaw faz próbkowania, przenosi tempo symboli i używa
modelu z różnymi wzmocnieniami. Pierwszą obowiązkową ablacją jest slicer
na dokładnie tym samym banku celu, z zerowym oraz nauczonym, przeskalowanym
progiem, bez dostępu do bajtów odniesienia podczas dekodowania.

Kolejne wymagania to nowe nagrania spoza użytej czwórki, kilka stacji i
satelitów, realne kontrole szumu/RFI z działającym transferem oraz porównanie
kosztu przy równym budżecie hipotez. Wcześniejsze kwalifikacje innych gałęzi
nie przechodzą automatycznie na ten detektor. Bez kotwicy ta wersja nie
uruchamia dodatkowego przebiegu i nie rozwiązuje problemu obserwacji
całkowicie zerowych.

Wynik uzasadnia dalsze testy adaptacji kanału, nie deklarację rewolucji lub
gotowości produkcyjnej. Priorytety i wcześniejszy dorobek opisuje
[raport przeglądu literatury](/home/ubuntu/telemetry-yield/reports/deep-research-demodulation-improvements-20260910.md).

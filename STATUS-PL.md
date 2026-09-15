# Status prostym językiem

## Jaki problem rozwiązujemy

Chcemy odzyskiwać poprawną telemetrię z surowych nagrań radiowych IQ także
wtedy, gdy pojedynczy istniejący dekoder niczego nie zwrócił. Program ma być
generyczny: SatNOGS jest jedną z dostępnych gałęzi i punktem odniesienia, a nie
jedynym środowiskiem, z którym system może działać.

## Jak działa metoda

1. Czuły selektor wskazuje fragmenty mogące zawierać transmisję. Ma padding,
   zachowuje słabe okna z top-k i kontrolnie przepuszcza część odrzuceń, żeby
   dało się mierzyć jego przeoczenia.
2. Rejestr możliwości kieruje fragment do pasujących demodulatorów. Obecna
   własna gałąź phase-first obsługuje binarne FSK, GFSK i GMSK. Inne modulacje
   są dokładane jako pluginy albo bezpieczne programy zewnętrzne.
3. Kandydaci przechodzą właściwy FEC, synchronizację i deframing zależny od
   profilu łącza. FSK nie jest automatycznie traktowane jako AX.25.
4. Dopiero walidator protokołu może nazwać wynik telemetrią. Sam nagłówek CCSDS
   albo przypadkowo poprawny 16-bitowy CRC nie wystarcza.
5. Wyniki wszystkich gałęzi są łączone bez duplikatów, ale z zachowaniem ich
   pełnego pochodzenia. Znana gałąź bazowa pozostaje w unii, więc słabszy nowy
   demodulator nie może usunąć wcześniej odzyskiwanych ramek.

## Co już działa

- Własny odbiornik pluginowy, selektor wysokiej czułości, zewnętrzne backendy,
  adapter gr-satellites/KISS i wspólny ledger wyników.
- Walidacja AX.25 UI, AX.25 zawierającego dokładny pakiet CCSDS, surowego CCSDS
  z dodatkową kontrolą integralności, ramek CCSDS TM Transfer Frame z FECF lub
  zewnętrznym dowodem integralności oraz profili ze stałym syncwordem.
- Wznawialna inwentaryzacja, download, konwersja CI16 do CF32, przetwarzanie i
  audyt protokołu. Awaria jednego pliku nie zatrzymuje kampanii.
- W bucketcie znaleziono 290 IQ: 257 katalogowych i 33 nowsze. Pobrano
  wszystkie 207 niepustych katalogowych nagrań, na których SatNOGS nie odzyskał
  ramek (17,89 GB), oraz wszystkie 33 nowe (2,72 GB). Obiekt 4165 ma 0 B już
  po stronie S3, więc nie zawiera danych do dekodowania.
- Dokładne powiązanie metadanych z rejestrem 409 profili gr-satellites pozwala
  skierować 67 z 208 nagrań bez ramek do 9 istniejących profili. Dla 104 trzeba
  dodać lub poprawić profil, a 37 blokują braki albo niejednoznaczność
  metadanych. Te liczby nie używają zgadywania po nazwach.
- Pełny skan 28 nagrań bez ramek, z jawnym profilem FSK 9600/AX.25 G3RUH, jest
  zakończony. Na dokładnie tych samych plikach IQ oficjalna gałąź wygenerowała
  11 surowych kandydatów, a natywna 9; wszystkie odpadły na ścisłej walidacji,
  więc uczciwy wynik zaufanych ramek to 0:0.
- Osobna gałąź CW/Morse poprawnie odzyskuje syntetyczne `SOS` przy 20 WPM.
  Na dziesięciu najbardziej podejrzanych nowych plikach nie znalazła jednak
  wiarygodnego tekstu; przypadkowe napisy odrzuciła jako artefakty.
- Dodano niezależny odbiornik Bell-202 AFSK1200/AX.25: dyskryminator FM,
  niekoherentne energie tonów, własny bank zegara, NRZI/HDLC, CRC-X25 i ścisły
  parser AX.25. Na zamrożonej kohorcie 23 identycznych IQ obie strony uzyskały
  0 zaufanych ramek, a 69 kontroli negatywnych także dało 0. Jest to parytet
  0:0 i rozszerzenie pokrycia, nie przewaga. Pliki są czytane oknami, bez
  materializowania całego nagrania w RAM.
- Pobrano i zweryfikowano wszystkie 49 dodatnich nagrań referencyjnych SatNOGS
  (100,72 GB). Na dokładnym podzbiorze 19 nagrań G3RUH oficjalna gałąź zwróciła
  7 surowych kandydatów, a natywna 8; wszystkie odpadły na ścisłej walidacji
  AX.25, więc także tutaj wynik zaufanych ramek to 0:0. To parytet, nie dowód
  większej skuteczności.
- Zbiór RML24 z publikacji Scientific Data pobrano, zweryfikowano i przeskanowano
  w całości: 1 323 000 rekordów i 21 klas. Aktualny fizyczny demodulator umie
  trasować tylko BPSK, GMSK, OQPSK i QPSK (252 000 rekordów); jego pełny,
  optymistyczny BER wynosi 0,463707, więc na tym zbiorze jest obecnie słaby.
  Diagnostyka wykazała błędny limit synchronizacji ±8 bitów: po rozszerzeniu
  spadł on na zamrożonej próbce z 0,464851 do 0,379539, podczas gdy kontrola
  pozostała na 0,463862. Filtr RRC 0,35 nie poprawił niezależnego holdoutu.
  RML24 mierzy BER/modulację, a nie kompletne ramki AX.25/CCSDS, więc nie jest
  bezpośrednim testem frame-yield względem SatNOGS.
- Nowa, opcjonalna gałąź `carrier_timing_v2` używa ograniczonego estymatora
  nośnej drugiej/czwartej potęgi i sześciu z góry ustalonych hipotez OQPSK. Na
  zamrożonym holdoucie +20 dB obniżyła BER BPSK/QPSK/OQPSK z 0,235669 do
  0,149167; niezależny audyt odtworzył wszystkie 144 rekordy i potwierdził
  wynik. Na pełnych 252 000 obsługiwanych rekordów BER spadł tylko z 0,463707
  do 0,462666. Ten pełny wynik jest częściowo obciążony większym bankiem
  hipotez przy starym zakresie ±8, dlatego nie jest samodzielnym dowodem
  przewagi. Domyślny tryb nadal pozostaje `legacy`.
- Pierwszy IQ-only router wszystkich 21 klas RML24 osiągnął 35,4% top-1 i
  55,4% top-3 na rozłącznych rekordach. W ostrzejszym, rozłącznym po całych
  komórkach modulacja/SNR/rate transferze uzyskał 28,2% top-1 i 49,6% top-3,
  wobec losowych 4,8% i 14,3%. To wynik post-development służący do ograniczenia
  liczby uruchamianych dekoderów, nie dowód detekcji transmisji ani ramek.

## Najważniejszy wynik jakościowy

Na trzech protokołowo porównywalnych nagraniach z tym samym IQ gałąź zgodna ze
źródłami SatNOGS odzyskała 22 zaufane ramki, sam phase-first 16, a ich
zwalidowana suma 24. To **dwie dodatkowe ramki, +9,09% względem wyniku
bazowego**, bez utraty ramek znalezionych tylko przez bazę. Obserwacja 4048
pozostaje osobnym dowodem identyczności surowego PDU, lecz bez potwierdzonej
gramatyki payloadu nie jest liczona jako trusted. Sam phase-first nie jest
jeszcze lepszy od SatNOGS; kompletny system z unią jest już lepszy na tym małym
benchmarku.

Nowszy, osobny eksperyment v2 dodał filtrowanie zespolonego kanału przed
różnicą fazy. Na dokładnie tym samym post-Doppler IQ CANVAS 14366383 unia dwóch
natywnych gałęzi odzyskała 10 zaufanych ramek wobec 6 baseline, bez utraty
którejkolwiek ramki bazowej. Niezależny audyt ponownie sprawdził bajty, FCS,
strukturę AX.25 i wewnętrzny CCSDS oraz potwierdził wynik.
Wynik ma oznaczenie `transfer_holdout_not_preregistered`: parametry zamrożono
na SONATE-2 i sam decode był reference-free, ale nagranie transferowe było już
znane z wcześniejszej diagnozy. Starszy agregat 22/16/24 pozostaje bez zmian,
dopóki nowa gałąź nie przejdzie całej trójki nagrań w jednym zamrożonym runie.

Jednocześnie program odrzuca fałszywe sukcesy. Na KOSTKA 4183 gr-satellites
zwrócił siedmiobajtowy PDU po kontroli FCS, lecz był on za krótki na legalną
ramkę AX.25. Nie został policzony jako telemetria.

## Co pozostaje

- Zastąpić słabą synchronizację RML24 pełnym odzyskiwaniem nośnej i zegara,
  equalizacją, truth-independent estymacją całkowitego początku rekordu oraz
  jawnym mapowaniem symboli; potem powtórzyć zamrożony holdout. Prosta estymacja
  początku z samej fazy zegara została przetestowana i odrzucona jako null.
- Dodać natywne pluginy pozostałych PSK/QAM/APSK, AX.100, CCSDS AOS/USLP
  z typowymi FEC oraz analogowe SSTV/APT. Architektura potrafi je rejestrować,
  ale nie twierdzimy, że są już zaimplementowane.
- Wytrenować i skalibrować docelowy model AI do selekcji fragmentów. Obecny
  ranking energii i widma jest deterministycznym heurystycznym fallbackiem, a
  nie modelem AI ani dowodem obecności telemetrii.

Aktualny przekrojowy raport znajduje się w `reports/research-campaign-v3.md`,
a maszynowo sprawdzalne liczby w odpowiadających mu plikach JSON. Opis
architektury znajduje się w `docs/generic-receiver-architecture.md`.

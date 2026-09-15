# Pełny katalog audio prywatnego SatNOGS — 10 września 2026

Pobieranie zakończone. Analiza nagrań jest uruchomiona, ale **jeszcze nie zakończona**.
Ten raport nie stanowi wyniku końcowego benchmarku ani dowodu przewagi dekodera.

## Co pobrano

Instancja: `https://polyitan.duckdns.org:8001`, połączenie bezpośrednie z
`10.0.0.11`. Zachowano weryfikację TLS nazwy hosta i łańcucha certyfikatu;
nie użyto wyłączenia weryfikacji certyfikatu. Pliki audio pobrano z portu 8019.

Zakres: wszystkie obserwacje widoczne w API i zakończone do
**2026-09-10 22:41:04 UTC**, bez wyboru według wcześniejszego sukcesu dekodowania.
Przejrzano wszystkie **43 strony API**, do końca paginacji.

| Pozycja | Liczba |
|---|---:|
| Zakończone obserwacje w katalogu | 1 055 |
| Obserwacje mające link do audio | 802 |
| Pobrane i zweryfikowane oryginały OGG | **538** |
| Linki zwracające HTTP 404 | 264 |
| Obserwacje bez linku do audio | 253 |
| Łączny rozmiar pobranych oryginałów | **1 586 036 083 bajty (1,586 GB)** |

Pobieranie zakończyło się o 22:59:23 UTC. Użyto 12 równoległych pracowników,
bez limitu łącznej liczby plików lub bajtów; pozostawiono ochronę 16 GiB wolnego
miejsca. Wszystkie 264 nieudane pobrania w końcowym raporcie mają HTTP 404,
a nie przekroczenie limitu transferu. To nie dowodzi, że obiekt nie istnieje
pod żadnym innym adresem. Zakres nie obejmuje niepowiązanych obiektów bucketu,
surowego IQ ani nowych obserwacji zakończonych po ustalonym odcięciu.

Ścisły wynik pobierania ma `status: incomplete`, ponieważ 264 wskazane obiekty
są niedostępne. Jednocześnie `metadata_complete: true` i `metadata_error: null`:
katalog został odczytany w całości. Nie należy ponawiać całego pobierania tylko
dlatego, że usługa zakończyła się kodem 1 wskutek tych brakujących obiektów.

## Integralność i zachowanie materiału

Po zakończeniu transferu wykonano niezależny od odbiornika odczyt i kontrolę
SHA-256 wszystkich **538 nagrań**, **538 powiązanych pełnych plików metadanych**
i **43 surowych odpowiedzi stron API**. Wszystkie kontrole przeszły.
Suma bajtów i liczba unikalnych identyfikatorów w potwierdzeniach pobrania
zgadzają się z końcowym raportem. Identyfikatory zawsze należą do tej prywatnej
instancji, nie do publicznego SatNOGS.

Oryginały i pełne odpowiedzi API zachowano. Zwięzły `inventory.json` celowo
nie zawiera dużych historii dekodowania; nie usunięto ich z oryginalnych
metadanych. Udane kopie w katalogach prób oraz `capture.ogg` są dowiązaniami
twardymi do tych samych danych. Nie należy liczyć ich ponownie jako osobnych
gigabajtów materiału.

## Analiza w toku

Usługa `telemetry-polyitan-audio-analysis-20260910-v1.service` wystartowała
o **23:00:50 UTC**, z kolejką wszystkich 538 nagrań. To niezależny proces
systemowy; nie wymaga otwartej rozmowy. Ustawienia faktycznego uruchomienia:
4 równoległe nagrania, po 2 wątki DSP, 900 s limitu na pojedynczy dekoder,
10 GiB limitu pamięci całej usługi. Limity ochronne i timeouty nie są
traktowane jako wynik „0 ramek”.

Stan odczytany bezpośrednio z gotowych wyników około 23:10 UTC:
**11 nagrań z zakończonymi dostępnymi ramionami dekodowania, 16 z jawnym
brakiem dopasowanego profilu, 0 niekompletnych prób i 0 potwierdzonych
unikalnych ramek naszego dekodera**. Pozostałe nagrania nadal oczekują lub są
przetwarzane. Agregat zapisuje się partiami, więc może chwilowo pozostawać za
pojedynczymi `obs-ID/result.json`. Jest to bardzo wczesny stan, nie wynik dla 538.

Ramiona dobierane według jednoznacznych metadanych i profilu:

- nasz ogólny odbiornik audio FSK/AFSK AX.25;
- nasz wariant innovation v2 dla zgodnego G3RUH/9600/OGG 48 kHz;
- Dire Wolf bez naprawiania bitów;
- gr-satellites z zapisanym profilem konkretnego nadajnika.

Nie każde nagranie jest obsługiwane przez każde ramię. Pełny starszy bank
progressive nie jest częścią tego pierwszego przebiegu. Reprezentacją wejściową
jest rzeczywiste audio z OGG, nie surowe IQ. Wszystkie aktywne ramiona dla
danego nagrania dostają ten sam PCM16, bez zmiany częstotliwości próbkowania,
wzmocnienia, przycinania lub mieszania kanałów.

Za potwierdzone ramki AX.25 UI uznaje się tylko dane spełniające walidację
struktury i odpowiedni warunek CRC. Dla naszych ramek odebrane FCS jest
sprawdzane niezależnie; dla zewnętrznych dekoderów, które usuwają FCS,
potwierdzenie CRC pochodzi z dekodera. Surowe kandydaty innych protokołów
pozostają osobną kategorią do walidacji. Przykładowy jednobajtowy kandydat
gr-satellites z obserwacji 5178 nie został zaliczony do telemetrii.

FOX/DUV nie ma właściwego profilu i deframera w zainstalowanym gr-satellites.
Sama zmiana nazwy modulacji nie rozwiąże tego braku. Takie obserwacje pozostają
jawnie nieobsługiwane; nie wolno przypisać im zerowej skuteczności odbiornika.
Nie wykonano jeszcze porównania zbiorów ramek z archiwum instancji, więc
nie ma podstaw do deklarowania dodatkowych ramek względem jej archiwum.

## Naprawy i odtwarzalność

Podczas pobierania poprawiono dwa błędy narzędzia akwizycji, zachowując
wcześniejsze wersje i dowody prób:

1. V1 błędnie stosował limit 4 MiB odpowiedzi również do HEAD, gdzie curl
   porównywał go z rozmiarem zdalnego audio. V2 usunął ten błąd; wszystkie
   22 wcześniej odrzucone w ten sposób obiekty później odzyskano.
2. V2 natrafił na stronę metadanych nr 26 o rozmiarze 17 078 831 bajtów,
   przekraczającą początkowy limit 16 MiB. V3 zwiększył ochronny limit strony
   do 256 MiB, czyta ją strumieniowo i utrzymuje mały indeks; kontynuacja
   dotarła do końca wszystkich 43 stron.

Końcowe testy akwizycji: **8/8 PASS**. Końcowe testy dispatchera analizy:
**20/20 PASS**. Nie zmieniano rdzenia zamrożonych dekoderów. Wcześniejszy smoke
dispatchera obejmował 2 nagrania × 4 ramiona, ale poprzedzał ostatnie drobne
poprawki śledzenia pochodzenia danych; nie jest dowodem pełnej walidacji obecnej
kampanii. Czasy tej kampanii są mierzone przy współdzieleniu hosta i nie stanowią
izolowanego porównania szybkości.

## Pliki wynikowe

Wszystkie ścieżki poniżej są względem `/home/ubuntu/telemetry-yield/`:

- `work/polyitan-audio-all-20260910-v1/acquisition/inventory.json` — pełny indeks;
- `work/polyitan-audio-all-20260910-v1/acquisition/acquisition-complete.json` — finalny bilans transferu i 264 brakujące linki;
- `work/polyitan-audio-all-20260910-v1/acquisition/obs-ID/capture.ogg` — oryginały;
- `work/polyitan-audio-all-20260910-v1/analysis/summary.json` — **bieżące**, aktualizowane wyniki analizy;
- `work/polyitan-audio-all-20260910-v1/analysis/obs-ID/result.json` — wynik i ograniczenia danego nagrania;
- `work/polyitan-audio-all-20260910-v1/parent-audit.json` — niezależny audyt i rzeczywiste parametry uruchomienia;
- `work/polyitan-audio-all-20260910-v1/analysis-build.json` oraz `docs/polyitan-audio-analysis-20260910.md` — identyfikatory kompilacji i kontrakt analizy.

Końcowy wynik analizy wymaga `terminal_for_current_invocation: true`
i sprawdzenia liczby niekompletnych oraz nieobsługiwanych przypadków.
Sam kod wyjścia procesu nie jest dowodem uniwersalnego wsparcia protokołów.

Graphify wykorzystano do orientacji w projekcie; jego starszy indeks nie
obejmował obecnych narzędzi Rust. Powyższe wyniki pochodzą z bezpośrednich
pomiarów, zachowanych plików i uruchomionych procesów, nie z grafu.

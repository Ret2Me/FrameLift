# Dekoder progresywny: wdrożenie i pilotaż, 10 września 2026

Wdrożono eksperymentalne tryby czasowe i wznawianie, dwie dodatkowe metody
estymacji kanału oraz narzędzia kontrolowanego porównania. Nie wdrażano
współpracy wielu stacji. Nie deklarujemy gotowości publikacyjnej ani produkcyjnej.

## Najważniejszy wynik odbioru

Na znanej obserwacji rozwojowej **14967361** dotychczasowy Rust odzyskiwał
0 ramek, Dire Wolf 1, a gr-satellites 0. Nowa gałąź `blind` odzyskała **2 różne
ramki z odebranym FCS**, po 264 bajty PDU. Jedna odpowiada dokładnie ramce
Dire Wolfa, druga jest dodatkowa względem obu zamrożonych odtworzeń
zewnętrznych na tym samym PCM16. Obie nie występowały też w poprzednim
zestawie wyników naszego dekodera na znanej dwudziestce. Nie przeszukano
całego archiwum SatNOGS, więc nie jest to twierdzenie o globalnej nowości.

Wynik wystąpił w pełnej kontynuacji, **nie w przebiegach 3 s ani 60 s tego
słabego nagrania**. Potwierdzono niezależnie bitowy CRC odebranych bajtów.
Post factum, bez użycia tych informacji podczas wyszukiwania, sprawdzono
również zawartość AX.25: oba pola informacji odpowiadają pakietom CCSDS
o dokładnej długości 248 bajtów, APID 32, licznikach sekwencji 5399 i 5404.
To dodatkowa zgodność strukturalna, nie uwierzytelnienie nadajnika.

Dowody: `work/progressive-evaluation-real-20260910-v1/full-audit.json`,
`ccsds-structure-audit.json` oraz raport macierzy i pełnej kontynuacji v3
w tym samym katalogu. Pełny zbiór 2 ramek jest identyczny w zamrożonych
wersjach v2 i v3. Wszystkie 890 wspólnych zadań obu wersji mają identyczną
proweniencję po pominięciu czasu wykonania i identyfikacji sesji.

Na dwóch innych znanych przypadkach nie wykazano dodatkowego zysku nowych
metod: **14967362: 3 → 3**, **14967413: 45 → 45**. Nie wyliczamy z celowo
wybranych przypadków procentowej skuteczności dla całej populacji.

## Co zmieniono w algorytmie

1. **Start bez poprawnej ramki**: ograniczona wielostartowa estymacja
   trzytapowego kanału, naprzemienne MLSE i najmniejsze kwadraty, osobny
   fragment sprawdzający dopasowanie przebiegu. Nie dostaje payloadów,
   prawdziwych bitów ani odpowiedzi dekoderów referencyjnych. Wnioskowane
   bity treningowe są jawnie odróżniane od kotwic CRC.
2. **Kilka kotwic kanału**: dodatkowy model ważony jakością i odległością
   czasową, z odrzucaniem niezgodnych modeli. Kotwice są rozłączne względem
   celu i siebie nawzajem. Zachowano poprzednią gałąź pojedynczej najbliższej
   kotwicy; nie wykazano jeszcze wyłącznego zysku agregacji na tych trzech
   rzeczywistych przypadkach.
3. **Ablacje i koszt**: runner Rust wykonuje cztery polityki przy tych samych
   zadanych limitach ściennych, utrwala rzeczywiste koszty oraz niezmienne
   kopie wyników. Nie utożsamia limitu ściennego z identycznym CPU. Nowych
   rezultatów nie przedstawiamy jako przewagi przy równym CPU nad zewnętrznymi
   dekoderami; takiego eksperymentu jeszcze nie ukończono.
4. **Niezależna walidacja**: implementacja manifestu kontroluje podział danych,
   rozdział przelotów i opcjonalnie stacji/satelitów, odrzuca znaną dwudziestkę
   jako holdout, obsługuje znane payloady i kontrole szumowe oraz grupowe
   przedziały ufności. Protokół jest przygotowany; nowy, duży holdout nie
   został w tym wdrożeniu przeprowadzony.

Są to znane rodziny estymacji/MLSE. Nowości naukowej nie dowodzi sama lista
bloków. Potencjalny wkład wymaga powtarzalnego dodatkowego odzysku z realnych
danych, analizy mechanizmu i uczciwej relacji z kosztem.

## Tryby i rzeczywisty test wznawiania

Nowe polecenie: `decode-progressive`. `quick` ma budżet 3 s, `deep` 60 s,
`full` kończy skończony bank. `--resume` kontynuuje tę samą sesję; można
zmienić budżet i liczbę wątków. Kod, wejście i polityka muszą pozostać zgodne.

Po szybkim prefiksie przeplatane są dokończenie banku, `blind` oraz wczesne
warianty adaptacyjne. Używają one zamrożonych kotwic szybkiego prefiksu.
Końcowa faza korzysta z zamrożonej pełnej generacji. Dzięki temu częściowo
wykonany bank nie zmienia po cichu modeli po wznowieniu.

WAV jest czytany małymi oknami, bez pełnego bufora f64. Przy OGG raz powstaje
zweryfikowany WAV float32. Ukończone zadania i ramki są zachowywane; filtrowanie
między etapami może nadal być liczone ponownie. Skończenie budżetu oznacza
wynik częściowy, nie brak transmisji ani ukończenie całego nagrania.

Na WAV **14967362, 686,039 s audio**, zmierzono:

| Uruchomienie | Ukończone zadania łącznie | Ramki łącznie | Zewnętrzny czas procesu |
|---|---:|---:|---:|
| Nowa sesja `quick`, 4 wątki | 16/1596 | 1 | 2,98 s |
| Wznowienie `deep`, 4 wątki | 250/1596 | 2 | dodatkowe 59,99 s |
| Wznowienie `full`, 2 wątki | 1596/1596 | 3 | dodatkowe 288,86 s |
| Oddzielne pełne wykonanie, 4 wątki | 1596/1596 | 3 | 252,31 s |

Po minucie były już ukończone zadania `blind`, `early-nearest`, `early-multi`
oraz część `baseline-remainder`. Przerwanie rzeczywiście nastąpiło wewnątrz
fazy zależnej od kotwic, nie tylko przy pustym wejściu.

Niezależny komparator Rust sprawdził **wszystkie 1596 zadań** obu zakończonych
sesji: identyczne ramki, modele, parametry, kolejność wyników i proweniencja.
Pominięto wyłącznie `elapsed_seconds` i zweryfikowany identyfikator sesji.
Zweryfikowano sumy kontrolne zadań, audio, pełność migawek i odebrane FCS.
Wynik `equal: true`: `work/progressive-20260910-v3/resume-equivalence.json`,
SHA256 `a581e15807f168f1392595e826058a21552dfae3fc1c12649d611a53541bf2c1`.

Szczyt RSS pierwszego szybkiego WAV wyniósł 56 164 KiB, około 54,8 MiB.
Nie jest to optymalny, już naukowo wykwalifikowany harmonogram technik.
Pomiary były wykonywane na współdzielonym hoście, z równoległymi badaniami;
nie używać ich jako izolowanego benchmarku przepustowości.

Pierwsze 3 s tego samego OGG nie wystarczyły na przygotowanie. Sesja z
budżetem 15 s przygotowała wejście i odzyskała jedną ramkę. Kolejne wznowienie
3 s wykorzystało konwersję, zwiększyło liczbę zadań 18 → 27 i zachowało ramkę,
przy zmierzonym czasie 2,96 s. Nie przedstawiamy tego jako odzysku z zimnego
OGG w 3 s. System nie daje gwarancji czasu rzeczywistego dokładnie ≤3,000 s;
narzut planisty, sprzątania i końcowego zapisu jest jawny.

CPU obejmuje nadzorcę i rozliczonych potomków. Przy zabiciu konwersji OGG
przed odebraniem jej statusu część CPU kodeka może nie zostać rozliczona przez
`getrusage`; te przebiegi nie kwalifikują twierdzeń o równym koszcie CPU.
Macierze porównawcze używają wspólnego WAV bez uruchamiania kodeków.

## Testy i granice wniosków

- Pełny przebieg przed zmianą wejścia na odczyt oknami: 263 testy biblioteki
  i 15 CLI zaliczone, 4 celowo pominięte.
- Finalna ścieżka progresywna: 30 testów biblioteki zaliczonych; dokładna
  zgodność odczytu sześciu reprezentacji WAV, rzeczywista konwersja OGG,
  wykrywanie modyfikacji, podmiany pliku i uszkodzeń cache.
- Testy CLI limitu, wznawiania, zachowania poprawnej ramki, zmiany polityki,
  brakującej zależności oraz sprzątania po przerwaniu: zaliczone.
- Runner oceny: 16 testów; komparator sesji: 7 testów zaliczonych.
- Osiem istniejących syntetycznych nagrań PCM: **12/12 wystąpień ramek**
  zamiast 6/12; wszystkie sześć dodatkowych wystąpień z `blind`, zero
  nieoczekiwanych ramek. To tylko **3 globalnie różne wartości ramek**,
  wcześniej znane z czystych wariantów. Nie są to niezależne próby terenowe.
- 48 syntetycznych kontroli gaussowskich odrzuconych przez sam estymator
  nie jest pomiarem fałszywych alarmów całego banku. Potrzebny dłuższy,
  reprezentatywny test negatywny oraz znane bity w niewidzianych kanałach.

## Artefakty i następny krok naukowy

Zamrożony dekoder: `work/progressive-20260910-v3/bin/telemetry-yield-rs`,
SHA256 `633f1f4ecaf7d4617f46fda83bf0866223bc123fa5013e3146cc3e32edefbb91`.
Archiwum źródeł: `work/progressive-20260910-v3/decoder-sources.tar.gz`,
SHA256 `9d682bbf808127580d0c5484a637e0305c292968985ec2373d73217e052faaa5`.
Późniejszy dodatkowy test odrzucania brakującej zależności oraz kod osobnego
komparatora są w aktualnych plikach źródłowych, nie w tym archiwum dekodera.

Obsługa: `docs/progressive-decoder.md`. Plan niezależnej oceny i opis runnera:
`reports/progressive-evaluation-plan-20260910.md`. Kolejny etap to zamrożenie
konfiguracji po pilotażu, pozyskanie nieoglądanych obserwacji i pełny eksperyment
koszt/odzysk z negatywnymi kontrolami. Wyniku pojedynczego znanego przypadku
0 → 2 nie wolno przedstawiać jako skuteczności na całej sieci.

Aktualny nowy bank dotyczy mono audio FSK/GMSK i AX.25 UI, także z pakietem
CCSDS wewnątrz. Istniejące pozostałe wejścia/protokoły projektu pozostają
oddzielnymi ścieżkami. Nie deklarujemy progresywnej obsługi wszystkich
protokołów, surowego IQ ani gotowości wdrożeniowej.

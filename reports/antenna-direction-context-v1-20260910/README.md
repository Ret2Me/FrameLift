# Kierunek anteny jako brakująca informacja o odbiorze

## Wynik

W miesięcznym planie znaleziono 89 przydziałów, których wszystkie niegraniczne próbki położenia satelity znajdują się po stronie nieba odradzanej przez operatora stacji. Dotyczy to 19,87% spośród 448 przydziałów na dwóch stacjach z jawnym ograniczeniem wschód/zachód, a nie 19,87% całego planu. Wynik ujawnia brak jawnego ograniczenia eksploatacyjnego. Nie stanowi pomiaru 89 nieudanych odbiorów ani wzrostu trafności predykcji.

Zaimplementowano osobny moduł wyznaczający położenie satelity względem stałego kierunku anteny, powiązany z rzeczywistymi odpowiedziami SatNOGS i dokładnymi TLE użytymi w planie. Moduł uruchomiono na wszystkich 12 208 przydziałach: jednoznaczny kierunek anteny pozwolił wyliczyć geometrię dla 2252 z nich. Pozostałe zachowują jawnie brak tej informacji. Nie zastąpiono brakującej charakterystyki promieniowania wymyślonym wzmocnieniem.

Ta poprawka uzupełnia [przegląd literatury i wcześniejsze porównanie metod](/home/ubuntu/telemetry-yield/reports/conditional-target-alignment-v1-20260910/deep-research.md). Tam wykazano poprawę prognozy warunkowego artefaktu demodulacji dzięki spójniejszej historii etykiet. Tutaj sprawdzono inny problem: czy plan respektuje dostępną informację o rzeczywistej konfiguracji odbiornika. Nowego wyniku procentowej skuteczności modelu nie wyznaczano.

## Dane antenowe i ich granice

Operator stacji 1710 podaje antenę Yagi o pionowej polaryzacji, azymut 100° i elewację 25°, z prośbą o nieplanowanie przelotów zachodnich. Stacja 2029 ma azymut 270°, elewację 30° i przeciwną prośbę — bez przelotów wschodnich.[^1][^2] Są to publiczne deklaracje konfiguracji, nie niezależne pomiary anteny ani upoważnienie do sterowania stacją.

Stacje 4497 i 4690 opisują odpowiednio Yagi oraz czaszę skierowane w zenit.[^3][^4] W takim przypadku odchylenie od osi wynosi 90° minus elewacja satelity i nie wymaga arbitralnego azymutu. Żadne z tych źródeł nie podaje wystarczającej charakterystyki kątowej, aby wiarygodnie przeliczyć odchylenie na tłumienie w decybelach.

Opis stacji 2830 podaje helisę RHCP oraz ustawienie „60 degree to south”, ale odniesienie kąta nie jest dostatecznie jednoznaczne do automatycznego przyjęcia elewacji 60°. Operator zaznacza też, że stacja powinna służyć jedynie wykrywaniu sygnału.[^5] Zachowano polaryzację i ostrzeżenie, lecz nie wyliczano arbitralnego kąta ani nie ustawiano prawdopodobieństwa demodulacji na zero. W planie ta stacja ma 2007 przydziałów wymagających uwzględnienia tego ograniczenia przy dalszym projektowaniu celu.

Stacja 766 identyfikuje antenę WiMo TA-1. Dokument producenta opisuje polaryzację RHCP oraz zależność zysku od elewacji, jednak podaje wartości w dBc, nie dBi. Opis częstotliwości w tym samym dokumencie jest niejednolity: 137–152 MHz w tekście oraz 135–152 MHz w tabeli.[^6] Nie przeniesiono tych liczb do pola zmierzonego zysku całego toru stacji ani do oddzielnej anteny UHF. Znalezienie karty produktu nie jest równoznaczne ze znajomością strat przewodów, montażu i temperatury szumowej instalacji.

Zakres jednej z anten stacji 432 zaczyna się w zapisanej odpowiedzi od 400 Hz. Tak szeroki zakres oznaczono do przeglądu, nie poprawiono jednak jednostek bez potwierdzenia. Nie wiadomo, czy jest to pomyłka operatora, czy sposób opisu w bazie. Sam domysł o literówce nie może zmieniać danych wejściowych.

## Co uwzględniał dotychczasowy model

Zamrożony planer korzysta z rzeczywistych zakresów częstotliwości anten jako ograniczenia dopuszczalności. Model prawdopodobieństwa ma również identyfikator stacji oraz azymuty początku i końca przelotu. Może więc pośrednio nauczyć się części zależności kierunkowych z historii. Nie był całkowicie pozbawiony informacji o kierunku.

Nie zawiera jednak jawnego kierunku nieruchomej anteny, jej polaryzacji ani opisanych przez operatora ograniczeń strony nieba. Nowy moduł dostarcza taką informację osobno, z pochodzeniem i datą dostępności. To uzasadniony kandydat na dodatkowe cechy i ograniczenia, ale jego przewaga nad dotychczasowymi cechami wymaga osobnego porównania. Nie wolno zakładać, że ponowne zakodowanie części tej samej informacji automatycznie poprawia predykcję.

## Metoda audytu

Użyto rewizji 2 planu `prospective-satnogs-network-shadow-v4h-isolated-50-target-20260913`, obejmującego okres 13 września–14 października 2026. Jego plik ma sumę SHA256 `112f7703b989ba622720b0ad72ae6b47fc9dab43f5b814989eca13f95e5701b4`. Wykorzystano towarzyszący mu niezmienny zapis odpowiedzi API, a nie późniejszą stronę WWW czy najnowszy, zmienny cache.

Każdy przydział odwołuje się do konkretnej sumy kontrolnej TLE. Odpowiednie elementy pobrano z lokalnej bazy otwartej wyłącznie do odczytu i wyeksportowano do osobnego pliku dowodowego. Nie pobierano nowych TLE ani nie zastępowano brakującego zestawu najnowszym dostępnym. Czas przechwycenia danych stacji i TLE musiał poprzedzać utworzenie wejściowego planu.

Interpretacje opisów zostały zapisane jako nowe informacje 10 września o 20:13:24 UTC. Nie nadano im wstecznie godziny powstania oryginalnego planu ani dat dawnych obserwacji. Audyt zakończył się o 20:13:36 UTC, przed początkiem badanego horyzontu. Rejestr obejmuje dokładne sumy kontrolne odpowiedzi stacji: zmieniona odpowiedź wymaga nowego przeglądu, zamiast bezwarunkowo dziedziczyć dawne ustawienie po samym numerze stacji.

Trajektorię obliczono w zamkniętym przedziale każdego przydziału co 10 sekund, z uwzględnieniem końca krótszego ostatniego kroku. Kąt względem osi anteny wynika z geometrii sferycznej. Średni kąt i udział czasu po stronie wschodniej/zachodniej oszacowano metodą trapezów. Południki oraz zenit traktowane są jako granica, nie arbitralnie jako wschód albo zachód.

Dla wszystkich 448 przydziałów z ograniczeniem strony nieba wykonano dodatkowe obliczenia co 2 sekundy. Zdefiniowano je przed wyliczeniem wyników głównych. Opisy operatorów nie określają matematycznie, co oznacza przelot mieszany; dlatego oddzielnie raportowane są odcinki całkowicie przeciwne, mieszane i strona w chwili zapisanej kulminacji. Nie zastosowano wybranego po obejrzeniu rezultatów progu automatycznego odrzucenia.

## Wyniki planu

| Stacja | Przydziały | Wszystkie próbki niegraniczne po stronie zalecanej | Wszystkie po stronie przeciwnej | Mieszane |
|---|---:|---:|---:|---:|
| 1710, antena wschodnia | 103 | 90 | 3 | 10 |
| 2029, antena zachodnia | 345 | 26 | 86 | 233 |
| Razem | 448 | 116 | 89 | 243 |

W 194 przydziałach zapisana kulminacja przypada po stronie przeciwnej do preferowanej. Ta liczba częściowo pokrywa się z kategorią 89 i nie powinna być do niej dodawana. Dodatkowych 1804 obliczeń geometrycznych dotyczy anten skierowanych w zenit: 134 na stacji 4497 i 1670 na stacji 4690. Nie mają one deklaracji wschód/zachód.

Zagęszczenie próbkowania nie zmieniło żadnej z 448 klasyfikacji strony nieba. Największa różnica oszacowanego udziału czasu po stronie wschodniej wyniosła 0,007159, czyli około 0,72 punktu procentowego; największa różnica minimalnego kąta wyniosła 1,254°. Stabilność kategorii nie oznacza zatem dokładności minimalnego kąta do ułamka stopnia ani dowodu ciągłego zachowania pomiędzy próbkami.

Wszystkie te liczby dotyczą przydziałów istniejącego planu, a nie pełnej puli konkurujących okazji. Nie odrzucono 89 wpisów, aby nazwać pozostałą listę „optymalnym planem”. Poprawiona optymalizacja musi ponownie uwzględnić wszystkie możliwości, konflikty, priorytety i dostępność zasobów.

## Implementacja i sprawdzenie

Moduł [antenna_direction_context_v1.py](/home/ubuntu/telemetry-yield/work/operations/antenna_direction_context_v1.py) udostępnia adapter odpowiedzi stacji oraz funkcję obliczania kontekstu trajektorii. Pola zysku, temperatury szumowej, szerokości wiązki, strat i prawdopodobieństwa odbioru pozostają puste, jeżeli brak danych. Nie wywołuje API SatNOGS, nie wysyła zleceń, nie uczy klasyfikatora i nie modyfikuje zamrożonej kampanii.

Końcowy zestaw obejmujący nowy moduł, niezależną kontrolę oraz dotychczasowy import rzeczywistych pomiarów przeszedł 87 testów w 0,72 s. Wcześniejsze 81 zaliczeń jest podzbiorem tej liczby, nie dodatkowym zestawem. Sprawdzono geometrię biegunową, przejście przez 0/360°, nierówne odstępy czasu, brak kierunku, zmienione dane, przyszłe metadane, próby cofnięcia dostępności, dokładne TLE oraz brak nadpisywania rezultatów. Kontrola 32 załadowanych modułów potwierdziła użycie właściwego środowiska i brak zmiany jego tożsamości.

Osobny [weryfikator](/home/ubuntu/telemetry-yield/work/operations/verify_antenna_direction_audit_v1.py) sprawdził sumy plików, pełny zbiór przydziałów geometrycznych oraz agregaty. Na deterministycznej próbie 64 przydziałów odtworzył 2718 kątów alternatywnym wzorem wektorowym. Maksymalna rozbieżność porównanych podsumowań wyniosła około 7,64 × 10⁻¹⁴ stopnia. Kontrola ta współdzieli propagator SGP4 i parser współrzędnych; nie jest niezależnym pomiarem rzeczywistej orbity ani ustawienia anteny.

Dowody: [wynik audytu](/home/ubuntu/telemetry-yield/work/antenna-direction-context-v1/audit-20260910/summary.json), [protokół](/home/ubuntu/telemetry-yield/work/antenna-direction-context-v1/audit-20260910/protocol.json), [wszystkie cechy](/home/ubuntu/telemetry-yield/work/antenna-direction-context-v1/audit-20260910/assignment-contexts.json), [niezależna kontrola](/home/ubuntu/telemetry-yield/reports/antenna-direction-context-v1-20260910/independent-verification.json), [87 testów](/home/ubuntu/telemetry-yield/reports/antenna-direction-context-v1-final-tests-20260910.xml).

## Dalsza ocena

Kolejny wariant planera powinien jawnie uwzględnić prośby operatorów i niejednoznaczność przelotów mieszanych, a następnie przeliczyć pełną pulę możliwości. Musi pozostać odróżnialny od już zarejestrowanej kampanii. Dopiero porównanie wykonanych odbiorów na nowych danych pozwoli ocenić zmianę liczby użytecznych ramek lub próbek. Obecny moduł przygotowuje cechy i ujawnia problem, lecz nie dowodzi wzrostu precyzji, poprawnej demodulacji czy ważności CRC.

Równoległy eksperyment pogodowy pozostaje oddzielny. Audyt z 20:14 UTC potwierdził 89 z 1132 paczek i 1448 par lokalizacja–uruchomienie modelu GFS. Pokrycie wynosi 402 obserwacje dla wyprzedzenia 1 h i 3151 dla 7 dni; te populacje mogą się pokrywać. Po błędzie połączenia o 20:08 kolektor przeszedł w automatyczny okres oczekiwania. Zarejestrowany test modelu z pogodą nadal nie wystartował, ponieważ wymaga kompletnego zbioru. Nie ma prognoz GFS na pełne 30 dni ani dowodu rzeczywistego przechwycenia tych archiwalnych prognoz przed historycznymi obserwacjami.

## Przypisy

[^1]: SatNOGS Network, operator EU1AEM, [stacja 1710](https://network.satnogs.org/stations/1710/), opis wyposażenia; właściwy audyt korzysta z odpowiedzi API przechwyconej 10 września 2026 o 13:46:04 UTC, a nie daty indeksowania strony.
[^2]: SatNOGS Network, operator EU1AEM, [stacja 2029](https://network.satnogs.org/stations/2029/), opis wyposażenia; zapis API z 10 września 2026 o 13:46:04 UTC.
[^3]: SatNOGS Network, operator VA3HEV, [stacja 4497](https://network.satnogs.org/stations/4497/), opis; lokalny, zweryfikowany zapis API z 10 września 2026 o 13:46:05 UTC. Ponowne otwarcie strony przez wyszukiwarkę nie powiodło się; nie zastępuje to źródłowej odpowiedzi API.
[^4]: SatNOGS Network, operator VA3HEV, [stacja 4690](https://network.satnogs.org/stations/4690/), opis czaszy; zapis API z 10 września 2026 o 13:46:06 UTC.
[^5]: SatNOGS Network, operator SM0TGU, [stacja 2830](https://network.satnogs.org/stations/2830/), opis konfiguracji i ograniczeń zastosowania; zapis API z 10 września 2026 o 13:46:05 UTC.
[^6]: WiMo Antennen und Elektronik GmbH, [TA-1, karta anteny nr 18350](https://www.wimo.com/media/akeneo_connector/media_files/1/8/18350_TA1_8b7b.pdf), s. 1, brak daty wydania; odczyt 10 września 2026. Zastosowanie modelu na stacji 766 potwierdza opis w powiązanej lokalnej odpowiedzi API.

## Sources

1. SatNOGS Network: opisy stacji [1710](https://network.satnogs.org/stations/1710/), [2029](https://network.satnogs.org/stations/2029/), [4497](https://network.satnogs.org/stations/4497/), [4690](https://network.satnogs.org/stations/4690/), [2830](https://network.satnogs.org/stations/2830/) i [766](https://network.satnogs.org/stations/766/). Bieżące opisy właścicieli, nie niezależne pomiary.
2. WiMo: [instrukcja TA-1](https://www.wimo.com/media/akeneo_connector/media_files/1/8/18350_TA1_8b7b.pdf). Dane nominalne producenta; nie charakterystyka całego toru stacji.
3. [Niezmienne wejściowe odpowiedzi API](/home/ubuntu/telemetry-yield/work/prospective-v4h/covariate-evidence/evidence-20260910T134603817441Z.json), [plan źródłowy](/home/ubuntu/telemetry-yield/work/prospective-v4h/plans/plan-20260910T134603817441Z.json) i [potwierdzenie wyniku](/home/ubuntu/telemetry-yield/work/antenna-direction-context-v1/audit-20260910/completed.json). Źródło wszystkich własnych obliczeń tabeli.
4. [Audyt 89 paczek GFS](/home/ubuntu/telemetry-yield/reports/single-run-weather-v1-89-batch-audit-20260910.json). Stan danych pogodowych, bez wyników treningu.

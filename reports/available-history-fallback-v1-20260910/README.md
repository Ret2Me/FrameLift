# Brak historii nowych satelitów i stacji

Zmiana sposobu wykorzystania dostępnej historii nie daje uniwersalnej poprawy. Pomaga w części prób, ale pogarsza inne; nie została włączona do głównego planera ani do zarejestrowanego miesięcznego porównania. Najważniejszy wynik tego eksperymentu to ograniczenie zakresu wcześniejszych obiecujących statystyk: dobry wynik dla znanych par satelita–stacja nie oznacza dobrego przewidywania dla nowych obiektów.

## Hipoteza i implementacja

Dotychczasowa reguła wyznacza osobne częstości sukcesów dla satelity i stacji, każdą stabilizując dziesięcioma umownymi przykładami o częstości globalnej. Ich średnia jest punktem odniesienia dla ostatnich dziesięciu odbiorów pary. Gdy nie ma historii jednego obiektu, jego oszacowanie jest równe częstości globalnej, więc średnia dodatkowo osłabia wpływ historii drugiego obiektu.

Nowy wariant pomija nieobecny składnik tej średniej. Jeżeli znana jest tylko historia satelity, wykorzystuje jego dotychczasowe, już stabilizowane oszacowanie; analogicznie dla stacji. Przy braku obu historii wraca do częstości globalnej. Gdy obie historie istnieją, wynik pozostaje dokładnie taki sam jak wcześniej. Nie zmieniono stałych wygładzania, progu 50%, etykiet ani danych.

Nie jest to naprawa błędu arytmetycznego ani nowa rodzina estymatorów, lecz sprawdzenie konkretnej heurystyki. W próbie nowej stacji kandydat jest praktycznie tą samą regułą co istniejąca historia satelity; przy nowym satelicie odpowiada historii stacji. Różnice rzędu 10⁻¹⁹ w tabelach porównań tych równoważnych wariantów są skutkiem arytmetyki zmiennoprzecinkowej, nie przewagą badawczą.

Kod: [available_history_fallback_v1.py](/home/ubuntu/telemetry-yield/work/operations/available_history_fallback_v1.py). To osobny komponent badawczy. Wykorzystano istniejący mechanizm historii dostępnej w momencie prognozy; nie zmieniano zamrożonej implementacji. Mapa zależności projektu pomogła odnaleźć ten mechanizm, natomiast zgodność ustalono z rzeczywistych plików i testów.

## Dane i protokół

Archiwum zawiera 47 039 obserwacji, 50 satelitów i 532 stacje. Ma 19 274 znane etykiety niezależnie ocenionego sygnału oraz 5570 wyników artefaktu demodulacji przy niezależnie potwierdzonym sygnale. Nie są to dwa rozłączne zbiory. Brak ręcznej oceny nie jest etykietą porażki; sam artefakt bez niezależnej oceny sygnału nie trafia do żadnego z tych dwóch paneli.

Protokół zapisano 10 września 2026 o 18:29:05 UTC; obliczenia zakończono o 18:30:44 UTC. Przed obliczeniem wyników ustalono jedną zmianę algorytmu, wszystkie porównania, próg decyzji i budżet rankingu. Nie uczono parametrów, nie wybierano zwycięzcy na czerwcu ani sierpniu. Oba miesiące były już wcześniej analizowane w projekcie, dlatego są to wyniki rozwojowe, nie nowy niezależny test potwierdzający.

Wykonano 18 porównań: dwa zadania, trzy scenariusze czasu i trzy warunki dostępności historii. Scenariusze obejmują godzinę wyprzedzenia z założonym opóźnieniem etykiety 24 lub 72 godziny oraz 30 dni wyprzedzenia z opóźnieniem 24 godziny. Opóźnienia są założeniami odtworzenia; archiwum nie dokumentuje rzeczywistych pierwszych dat publikacji ocen.

Dla nowych obiektów zastosowano pięć deterministycznych koszyków według reszty z dzielenia identyfikatora przez pięć. Cała historia satelitów albo stacji z ocenianego koszyka jest usunięta, także stare etykiety i późniejsza informacja zwrotna. Każdy przykład jest oceniany raz dla danej osi podziału. Żadne etykiety wyłączonej grupy nie służą do uczenia, kalibracji ani doboru parametrów. Panel bez wymuszonego wyłączenia grup nadal może zawierać naturalnie nowe obiekty; określenie `warm` w nazwach plików nie oznacza, że każdy rekord ma obie historie.

W sierpniu oceniono sygnał dla 5350 obserwacji, 32 satelitów i 166 stacji; było 2931 dodatnich wyników. Demodulację oceniono dla 2031 obserwacji, 24 satelitów i 116 stacji; było 493 dodatnich wyników. Każda metoda, horyzont i oś wyłączenia grup zachowują tę samą listę obserwacji danego zadania. Całe 50 satelitów pozostaje w archiwum, ale nie każdy ma kwalifikowaną etykietę w ocenianym miesiącu.

## Wyniki dla nowych obiektów

Poniższe wartości dotyczą sierpnia, prognozy godzinę przed przelotem i etykiet wcześniejszych odbiorów dostępnych po założonych 24 godzinach. Brier jest średnim kwadratem błędu podanego prawdopodobieństwa; mniej oznacza lepiej. „Dotychczasowa reguła” oznacza `last10_2` przy tej samej definicji etykiety, a nie zamrożony model logistyczny ani najlepszy wcześniejszy model AI.

| Zadanie i brakująca historia | Brier dotychczas | Brier nowego wariantu | Poprawność dotychczas → teraz | Ocena |
|---|---:|---:|---:|---|
| Sygnał, nowy satelita | 0,271494 | 0,286917 | 46,28% → 47,07% | Gorsze prawdopodobieństwa; nadal słaby wynik |
| Sygnał, nowa stacja | 0,215397 | 0,208778 | 66,88% → 67,57% | Niewielka poprawa punktowa |
| Demodulacja, nowy satelita | 0,167225 | 0,159936 | 75,73% → 76,51% | Częściowa poprawa, niska wykrywalność sukcesów |
| Demodulacja, nowa stacja | 0,195244 | 0,208681 | 75,73% → 75,73% | Gorsze prawdopodobieństwa, nadal same wskazania „nie” |

Przy sygnale na nowej stacji precyzja wskazań „tak” wynosi 64,28%, a wykryta część wszystkich sukcesów 91,85%. Z 1070 najwyżej ocenionych obserwacji 901 miało sygnał, wobec 882 dla dotychczasowej reguły. Ten ranking nie uwzględnia konfliktów antenowych, długości odbiorów ani blokerów; nie oznacza 19 dodatkowych odbiorów rzeczywiście wykonanego planu.

Przy demodulacji nowego satelity wariant wskazał 128 sukcesów, z których 72 były trafne: precyzja 56,25%, wykrywalność 14,60% z 493 rzeczywistych sukcesów. Dotychczasowa reguła przy tym progu nie wskazywała żadnego sukcesu. Wynik 76,51% poprawności jest tylko o około 0,79 punktu procentowego lepszy od strategii „zawsze nie”, która ma 75,73%. Z 407 najwyżej ocenionych obserwacji nowy wariant wskazuje 226 dodatnich, wobec 224 wcześniej. Budżet wynosi tutaj zaokrąglone w górę 20%; wcześniejszy eksperyment używał 406, więc tych dwóch rankingów nie należy bezpośrednio zestawiać.

## Różnice między dniami i obiektami

Niepewność liczono przez losowanie całych dni oraz oddzielnie całych wyłączonych obiektów. Przy sygnale na nowej stacji różnica Brier wynosi −0,006618, ale przedział liczony po stacjach obejmuje zero: [−0,019105; 0,005182]. Przy demodulacji nowego satelity różnica wynosi −0,007289, a przedział po satelitach to [−0,015127; 0,008088]. Nie można na tej podstawie uznać poprawy za powtarzalną na kolejnych nowych obiektach.

Pogorszenie jest lepiej widoczne: sygnał dla nowego satelity ma różnicę +0,015423 i przedział po satelitach [0,003656; 0,024056], a demodulacja dla nowej stacji +0,013437 z przedziałem po stacjach [0,002879; 0,024154]. Są to opisowe przedziały bez korekty wielokrotnych porównań, nie formalny dowód przewagi lub szkody w całej populacji SatNOGS.

Wariant 72-godzinnego opóźnienia zachowuje ten sam mieszany obraz. Przy 30 dniach wyprzedzenia wykrywalność demodulacji nowego satelity spada do 9,33%, a precyzja do 47,92%; poprawność 75,53% jest już niższa niż dla strategii „zawsze nie”. Korzyść Brier dla sygnału na nowej stacji jest mniejsza, a przedziały po dniach i stacjach obejmują zero. Horyzont 30 dni jest liczony osobno dla każdego przelotu, nie jest symulacją jednego miesięcznego harmonogramu.

## Weryfikacja

Przeszło 18 nowych przypadków testowych i 30 przypadków wcześniejszego predyktora oraz adaptera, łącznie 48 w jednym uruchomieniu. Kontrola importów wskazała 30 modułów wyłącznie z właściwego zamrożonego środowiska; jego tożsamość nie zmieniła się. Sprawdzono m.in. wyłączenie całej historii grupy, brak wpływu własnego i przyszłego wyniku, oba opóźnienia, brak danych RF, zachowanie etykiet nieznanych i ranking bez rozstrzygania remisów na podstawie wyniku.

Osobny audytor używa wyłącznie biblioteki standardowej, bez importu badanego estymatora i kursora historii. Prostym zliczaniem wcześniejszych danych odtworzył 900 prawdopodobieństw dla 180 wylosowanych prognoz, sprawdził 1260 wartości miar i 180 rankingów. Dla wszystkich rekordów sprawdził identyfikatory, etykiety i czasy względem źródła. Wszystkie kontrole przeszły. Nie przeliczono niezależnie każdego prawdopodobieństwa ani procedury losowania przedziałów.

Dowody: [protokół](/home/ubuntu/telemetry-yield/reports/available-history-fallback-v1-20260910/protocol.json), [wyniki](/home/ubuntu/telemetry-yield/reports/available-history-fallback-v1-20260910/results.json), [audyt niezależny](/home/ubuntu/telemetry-yield/reports/available-history-fallback-v1-20260910/independent-audit.json), [testy](/home/ubuntu/telemetry-yield/reports/available-history-fallback-v1-tests-20260910.xml), [kontrola środowiska](/home/ubuntu/telemetry-yield/reports/available-history-fallback-v1-test-guard-20260910.json).

## Decyzja i pozostałe wymagania

Odrzucono automatyczne zastąpienie głównego modelu tym wariantem. Nie ma jednej poprawy wspólnej dla obu zadań i obu osi nowych obiektów. Kod i wszystkie wyniki, także niekorzystne, pozostają dostępne do odtworzenia. Nie dodawano go po obejrzeniu wyników do zamrożonego protokołu miesięcznego.

Eksperyment nie używa pogody, geometrii ani parametrów anteny. Potwierdza granicę metod czysto historycznych, nie nieprzydatność fizyki łącza. Następny model przenoszenia wiedzy musi być oceniony na całkowicie wyłączonych obiektach i korzystać wyłącznie z parametrów o udokumentowanej dostępności przed prognozą. Wzmocnienie odbiornika zapisane podczas obserwacji nie może być przedstawiane jako zysk anteny ani jako pewna informacja znana miesiąc wcześniej.

Nadal wymagane są rzeczywisty wkład pogody i danych antenowych, sprawdzenie etykiet poza ręcznie ocenionym podzbiorem oraz przyszłe wyniki miesięcznej kampanii. Test przyczynowego zwiększenia liczby unikalnych próbek wymaga wykonania własnego planu na uprawnionych stacjach. W tej pracy nie wykonywano żądań sieciowych, nie używano tokenu API ani nie zlecano obserwacji. Cel publikacyjny pozostaje nieukończony.

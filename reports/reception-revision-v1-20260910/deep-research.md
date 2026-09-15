# Poprawa prognoz odbioru satelitarnego: metody, implementacja i wyniki

## Podsumowanie

Najbardziej uzasadnionym kierunkiem krótkoterminowym jest wzmacnianie prognozy opartej na rzeczywiście dostępnej historii, z osobnym traktowaniem satelity, stacji i nadajnika. Nie ma podstaw, aby zakładać, że większa sieć neuronowa albo uczenie ze wzmocnieniem automatycznie rozwiążą obecne ograniczenia. Korzyść musi zostać wykazana na późniejszych odbiorach, względem mocnej metody historycznej, przy identycznych informacjach wejściowych.

Zaimplementowano pięć wariantów: średnią prognoz historycznych, adaptacyjne ważenie historii, ważenie uwzględniające satelitę i stację, adaptacyjną mieszankę historii z istniejącym modelem drzewiastym oraz jej korektę kalibracyjną. W pilotażu wszystkie warianty oceniono na tych samych rzeczywistych obserwacjach SatNOGS. Następnie rozszerzono eksperyment historyczny tak, aby mechanizm ważenia otrzymywał wszystkie kwalifikujące się wcześniejsze obserwacje w zamrożonej kohorcie.

Pilotaż przyniósł małą poprawę punktową przewidywania artefaktu demodulacji po wykryciu sygnału: błąd Brier spadł z 0,129811 do 0,125139, czyli o około 3,6%, względem dotychczasowej hybrydy. Poprawność odpowiedzi przy progu 50% wzrosła z 80,74% do 81,11%. Przedział niepewności różnicy obejmuje jednak brak poprawy. Dla obecności sygnału nowy wariant nie pokonał najlepszego wcześniejszego kandydata.

Rozszerzenie na pełną kwalifikującą się historię objęło 68 862 prognozy z etykietą sygnału i 49 073 z warunkową etykietą demodulacji, każdorazowo z aktualizacją dopiero po nadejściu informacji zwrotnej. Wybrana na marcu metoda kontekstowa dała dla demodulacji Brier 0,125808 i poprawność 81,11%, ale dla sygnału poprawność spadła do 85,26%. Większa historia nie zapewniła jednoznacznej przewagi nad dotychczasowym rozwiązaniem.

Nie nastąpiła automatyczna podmiana modelu. Nowe komponenty są dostępne jako jawnie wybierane warianty badawcze. Nie zmieniono modelu zarejestrowanej kampanii prospektywnej i nie wysłano nowych zleceń obserwacji do stacji SatNOGS w ramach eksperymentów.

Istotnym wynikiem audytu jest ograniczona możliwość odtworzenia momentu dostępności danych historycznych. Przyjęte opóźnienie etykiety o 24 godziny jest założeniem, nie pomiarem. Dodatkowo część zapisanych TLE ma epokę późniejszą niż symulowany moment prognozy. To wymaga sprawdzenia dat udostępnienia TLE; sama epoka nie rozstrzyga, kiedy elementy orbitalne były dostępne.

## Cel predykcji i znaczenie „precyzji”

System ma wspierać planowanie przyszłych odbiorów, a nie rozpoznawać sygnał na obrazie już wykonanego przelotu. Oba zadania mogą używać uczenia maszynowego, ale mają całkowicie inny zestaw informacji dostępnych w chwili decyzji. Przyszłego waterfallu, liczby zdekodowanych ramek ani oceny obserwacji nie wolno używać jako cech przyszłego przelotu.

W aktualnych danych rozdzielono dwa wyniki. Pierwszy oznacza potwierdzoną obecność sygnału. Drugi oznacza obecność artefaktu demodulacji przy potwierdzonym sygnale. Oficjalna dokumentacja SatNOGS rozróżnia ocenę obecności sygnału na waterfallu, wynik nieznany i awarię wykonania obserwacji. Nie należy zamieniać tych kategorii na jeden automatyczny podział sukces/porażka.[^1]

Artefakty SatNOGS obejmują m.in. ramki demodulowane/dekodowane oraz obrazy. Sam fakt wystąpienia wpisu w liście artefaktów nie jest jednolitym, niezależnie zweryfikowanym testem poprawności CRC ani licznikiem nowych, unikalnych próbek misji. Aktualnego drugiego wyniku nie należy więc opisywać jako uniwersalnego „prawdopodobieństwa bezbłędnego zdekodowania telemetrii”.[^2]

Ocena prawdopodobieństwa i poprawność odpowiedzi tak/nie mierzą różne rzeczy. Brier jest średnią kwadratów różnicy między podanym prawdopodobieństwem a wynikiem 0/1; im mniejszy, tym lepiej. Spadek Brier o 3,6% nie oznacza wzrostu poprawności o 3,6 punktu procentowego. Poprawność przy progu 50% informuje tylko, jak często decyzja otrzymana po przekroczeniu tego progu zgadzała się z etykietą.

Precyzja pozytywnych wskazań odpowiada na inne praktyczne pytanie: jaki odsetek przelotów wskazanych jako udane rzeczywiście miał pozytywną etykietę? Czułość określa, jaką część wszystkich udanych przelotów udało się wskazać. Można zwiększyć pierwszą wielkość, wskazując mniej przelotów, i jednocześnie pogorszyć całkowity uzysk danych. Dlatego zmiany progu muszą być oceniane razem z liczbą wybranych obserwacji i ograniczeniami harmonogramu.

## Najważniejsze wyniki literatury

### Planowanie nie zastępuje estymacji prawdopodobieństwa

Ronen i Ben-Moshe opisują problem przypisywania satelitów do ograniczonych zasobów odbiorczych oraz maksymalizowania unikalnie odebranych wiadomości. Ich prawdopodobieństwa dekodowania pochodzą ze statystyk stacji. Zaproponowane algorytmy obejmują wkład marginalny oparty na wartości Shapleya, porównania sąsiednich par i ważone licytowanie. Monte Carlo przybliża obliczenia Shapleya; nie jest tam wytrenowanym, indywidualnym predyktorem jakości kolejnego przelotu. Przedstawione wyniki są symulacyjne, a założenie niezależności odbiorów autorzy wskazują jako optymistyczne ograniczenie.[^3]

Wniosek projektowy jest następujący: bardziej zaawansowany algorytm wyboru obserwacji nie poprawia sam z siebie jakości wejściowych prawdopodobieństw. Estymację odbioru i optymalizację planu trzeba rozwijać i mierzyć oddzielnie. W zadaniu miesięcznym z jawnymi blokerami sensownym punktem odniesienia pozostaje solver optymalizacyjny, któremu przekazuje się prognozowany uzysk i ograniczenia.

### Istnieje bezpośrednia wcześniejsza praca o historii, pogodzie i geometrii

L2D2, opublikowane na SIGCOMM 2021, łączy planowanie z przewidywaniem jakości łącza. Estymator wykorzystuje położenie orbitalne, pogodę i pomiary specyficzne dla pary satelita–stacja, łącząc drzewa gradientowe, sieć neuronową i regresję. Autorzy badali też przenoszenie modelu na nową stację. Dla pomiarów jakości łączy podano medianę błędu SNR 0,39 dB wobec 2,39 dB dla użytego modelu ITU. Nie jest to jednak wynik klasyfikacji obecności sygnału w SatNOGS: duża symulacja wykorzystuje rozmieszczenie sieci SatNOGS, natomiast zachowanie łączy X-band jest modelowane odrębnie.[^4]

Implikacja dla oryginalności jest jednoznaczna: samo połączenie historii, TLE, pogody i planowania nie stanowi nowego pomysłu. Potencjalny wkład może dotyczyć wiarygodnej estymacji przy brakach danych, opóźnionych i poprawianych etykietach, zmiennym sprzęcie oraz prospektywnej oceny rzeczywistego uzysku. To wymaga własnych wyników, nie tylko nowej integracji komponentów.

### Adaptacja historii i mieszanie metod

McCormick i współautorzy przedstawili dynamiczną regresję logistyczną oraz dynamiczne uśrednianie modeli dla klasyfikacji binarnej. W ich konstrukcji aktualizowane są zarówno parametry modeli, jak i ich znaczenie, a mechanizm zapominania pozwala ograniczać wpływ odległych danych. Zastosowanie empiryczne dotyczyło danych medycznych, nie łączy satelitarnych. Praca uzasadnia kierunek adaptacji, ale nie dostarcza oczekiwanej poprawności dla tego projektu.[^5]

Chernov i Zhdanov analizują prognozowanie z ekspertami przy dyskontowaniu dawnych strat. Korotin, V’yugin i Burnaev badają ważenie ekspertów, gdy informacja zwrotna dociera z opóźnieniem. To rozróżnienie odpowiada praktycznemu problemowi SatNOGS: moment przelotu i moment poznania jego wyniku nie są tym samym. Ich twierdzeń nie można automatycznie przenieść na dowolną implementację z zapominaniem, grupowaniem i korektami etykiet.[^6][^7]

Wniosek inżynierski: zamiast ustalać na zawsze jedno okno historii, można równolegle utrzymywać prognozy z różnych okresów i zwiększać udział tych, które ostatnio dobrze prognozowały. Wynik danego przelotu może zmienić wyłącznie prognozy wydawane po jego otrzymaniu. Przy poprawieniu lub wycofaniu etykiety należy usunąć wcześniejszy wkład, a nie naliczać obserwację ponownie.

### Kalibracja może pomóc, ale również zaszkodzić

Feng i współautorzy proponują bayesowską korektę modeli logistycznych oraz wariant Markowa uwzględniający zmiany rozkładu. Ważną częścią pracy jest kontrolowanie ryzyka nadmiernego aktualizowania modelu, nie tylko szukanie większej poprawności. Badanie pokazuje zastosowania kalibracji i łączenia modeli w danych klinicznych. Nie stanowi dowodu przewagi kalibracji w odbiorach satelitarnych.[^8]

Kull, Silva Filho i Flach pokazują, że źle dobrana transformacja kalibracyjna może pogorszyć pierwotne prawdopodobieństwa. Proponowana przez nich kalibracja beta zawiera odwzorowanie identycznościowe. W obecnym eksperymencie użyto prostszej, jawnie określonej rodziny dziewięciu transformacji logistycznych argumentu logit(p), także zawierającej identyczność. Nie jest to implementacja pełnej kalibracji beta ani MarBLR.[^9]

Wniosek projektowy: „kalibracja” jest nazwą procedury, nie certyfikatem jakości. Musi przejść takie samo porównanie na późniejszych danych jak pozostałe zmiany. Przy małych podzbiorach należy szczególnie uważać na nadmiernie elastyczne krzywe dopasowane do pojedynczych satelitów lub stacji.

### Alternatywne AI i granice gwarancji niepewności

CatBoost wykorzystuje uporządkowane statystyki zmiennych kategorycznych i uporządkowane wzmacnianie drzew, aby ograniczać określony rodzaj przecieku etykiety i przesunięcia predykcji. To potencjalnie sensowny późniejszy konkurent dla danych z wieloma identyfikatorami. Mechanizm opisany w tej pracy nie zastępuje jednak zewnętrznego podziału chronologicznego ani kontroli momentu dostępności danych.[^10]

Predykcja konformalna może wspierać prezentowanie niepewności lub odmowę kategorycznej odpowiedzi. Jej klasyczne gwarancje zależą od założeń dotyczących wymienności danych; zależności czasowe i zmiany warunków wymagają odpowiednich rozszerzeń. Nie jest poprawne dodanie zwykłego narzędzia konformalnego i ogłoszenie „95% pewności dla każdego przyszłego satelity”.[^11]

Uczenie ze wzmocnieniem nie zostało wybrane do obecnego eksperymentu. Jest to decyzja projektowa: obecne dane zawierają wyniki wykonanych odbiorów, ale nie wyniki wszystkich możliwych, niewybranych obserwacji. Nie istnieje zatem gotowy, zweryfikowany strumień nagród dla alternatywnych polityk planowania. Monte Carlo pozostaje przydatne do analizy scenariuszy i odporności planu, a nie jako zamiennik informacji o rzeczywistym odbiorze.

## Pogoda, antena i brakujące parametry

ITU-R P.618-14 rozróżnia wpływ gazów, opadów, chmur, otoczenia anteny, elewacji oraz scyntylacji. Wskazuje rosnące znaczenie części efektów troposferycznych przy wysokich częstotliwościach, a poniżej 1 GHz podkreśla możliwe znaczenie jonosfery. Nie uzasadnia to używania jednej uniwersalnej kary za deszcz dla wszystkich pasm.[^12]

ITU-R P.531-16 opisuje m.in. rotację Faradaya, zależną od częstotliwości, zawartości elektronów i pola magnetycznego wzdłuż drogi propagacji. Zależność od geometrii i polaryzacji jest argumentem za modelowaniem interakcji, nie tylko globalnego wskaźnika aktywności geomagnetycznej. Aktualna wersja dokumentu została zatwierdzona we wrześniu 2025; jej angielski plik udostępniono ponownie w maju 2026.[^13]

Rekomendowany zestaw rozszerzeń dla VHF/UHF obejmuje lokalny poziom zakłóceń, rzeczywistą charakterystykę kierunkową anteny, polaryzację, maskę horyzontu, błędy śledzenia i częstotliwości oraz opis dostępności nadajnika. Dla wysokich częstotliwości rośnie priorytet ilościowych cech opadowych i zapasu łącza. Są to rekomendacje do zweryfikowania przez testy usuwania i dodawania grup cech, nie wykazane w tym eksperymencie źródła poprawy.

Nazwa typu anteny nie jest równoważna zmierzonemu zyskowi w kierunku satelity. Podobnie moc znamionowa nadajnika nie określa całego bilansu łącza bez informacji o polaryzacji, antenie nadawczej, kierunku promieniowania i stanie nadajnika. Dopisywanie domyślnych, arbitralnych wartości mogłoby dawać pozornie precyzyjne, ale systematycznie błędne prognozy.

Parametry mogą być opcjonalne w interfejsie, lecz wymagany jest jawny stan „brak informacji”. Metoda historyczna może działać bez mocy i zysku anteny, bo część ich łącznego wpływu odzwierciedla historia tej samej konfiguracji. Nie oznacza to, że potrafi wyodrębnić przyczynę błędu albo bezpiecznie przenieść taką wiedzę na zupełnie nowy sprzęt. Dodanie poprawnych danych może pomóc; nie ma matematycznej gwarancji, że każda nowa kolumna zwiększy empiryczną trafność.

Prognoza pogody musi pochodzić z wydania dostępnego przed podjęciem decyzji. Open-Meteo rozróżnia dane historyczne, ciągłą serię zszywaną z kolejnych prognoz oraz archiwum pojedynczych wydań. Do odtwarzania konkretnej prognozy właściwe są dane o określonym czasie inicjalizacji i horyzoncie, z dodatkową kontrolą opóźnienia publikacji.[^14] Standardowy interfejs prognoz udostępnia do 16 dni, a nie pewną godzinową prognozę na cały miesiąc.[^15]

Dla dalszej części miesięcznego planu należy więc używać szerszych scenariuszy lub klimatologii, oznaczać większą niepewność i przeliczać przyszłe obserwacje po kolejnych aktualizacjach. Taki plan jest planem kroczącym. Nie należy przedstawiać jego skuteczności jako zweryfikowanej skuteczności jednej, niezmienianej prognozy 30 dni naprzód.

## Zaimplementowana metoda

Bazę stanowi sześć reguł: ostatnich 10 odbiorów pary satelita–stacja, historie 7-, 30- i 90-dniowe tej pary, ostatnich 50 odbiorów oraz ostatnich 10 odbiorów tego samego łącza z uwzględnieniem nadajnika. Przy niedostatku lokalnych obserwacji reguły korzystają ze statystyk bardziej ogólnych. Nie wymagają podania mocy ani charakterystyki anteny.

Każdy ekspert najpierw wydaje prawdopodobieństwo. Dopiero po nadejściu etykiety obliczany jest jego błąd. Starsze błędy mają malejącą wagę z czasem połowicznego zaniku 30 dni, a wagi ekspertów są proporcjonalne do exp(−2 × zdyskontowana suma błędów Brier). To jeden ustalony zestaw parametrów; nie uruchamiano przeszukiwania okien i współczynników na wynikach czerwcowych.

Wariant kontekstowy utrzymuje dodatkowe wagi dla satelity i stacji. Wynik lokalny jest łączony z wynikiem globalnym w proporcji zależnej od liczby dostępnych etykiet: n/(n+20). Nowa stacja lub satelita nie otrzymują w ten sposób nieuzasadnionego zaufania do kilku przypadków. Licznik n oznacza dostępne rekordy informacji zwrotnej, nie liczbę niezależnych prób ani efektywną liczebność statystyczną.

Hybryda dodaje istniejący model drzew gradientowych jako siódmego eksperta. Eksperyment nie trenuje go ponownie i nie zmienia jego wcześniejszych predykcji. Kalibrowana hybryda waży dziewięć map postaci sigmoid(a + b × logit(p)), dla a∈{−0,5;0;0,5} i b∈{0,75;1;1,25}. Połowę początkowej masy otrzymuje brak korekty, a aktualizacja korzysta ze straty logarytmicznej.

Wydane prognozy są niezmienne. Powtórzenie tej samej etykiety nie nalicza jej dwa razy. Korekta zastępuje poprzedni wkład, a wycofanie etykiety go usuwa. Wiek obserwacji liczony jest od końca przelotu, a nie od późnego przesłania pliku. Interfejs nie pobiera danych, nie dobiera sam metody produkcyjnej i nie zleca obserwacji.

Zaimplementowana mieszanka jest własnym wariantem inżynierskim inspirowanym literaturą. Nie przypisuje się jej formalnych gwarancji DMA, MarBLR, Fixed Share ani innych algorytmów, których pełnych założeń i konstrukcji nie odtworzono. Rozrzut między ekspertami także nie jest automatycznie przedziałem ufności dla prawdziwego prawdopodobieństwa.

## Konstrukcja eksperymentów

Źródłem jest zamrożony prefiks 154 547 unikalnych obserwacji, obejmujący 1583 satelity i 698 stacji. Znana etykieta sygnału występuje w 70 655 rekordach, a warunkowy wynik demodulacji w 50 501. To nie są trzy rozłączne zbiory. Obserwacje bez kwalifikującej się etykiety nie stają się przykładami negatywnymi i nie wolno sumować obu zadań jako liczby unikalnych przelotów.

Oceniane panele obejmują 1–14 marca i 1–14 czerwca 2026. Dla sygnału zawierają odpowiednio 1473 i 1099 obserwacji; dla warunkowej demodulacji 883 i 810. Oba okresy były już wykorzystywane we wcześniejszym rozwoju projektu. Są to zatem eksperymenty rozwojowe, a nie ponownie „nietknięty” test końcowy.

Identyfikatory, etykiety i momenty prognoz porównano z istniejącymi, zabezpieczonymi sumami kontrolnymi artefaktami. W pilotażu uczenie wag zaczyna się od nowa w każdym okresie i korzysta wyłącznie z wcześniej zakończonych obserwacji panelu. Bazowe reguły mają dostęp do większej historii, ale mechanizm mieszający otrzymuje wtedy tylko informację zwrotną z panelu. Ten szczegół jest ważny: pilotaż nie jest treningiem wag na 154 tysiącach przykładów.

W rozszerzeniu historycznym usunięto to ograniczenie. Każdy kwalifikujący się rekord sprzed 15 czerwca najpierw otrzymuje prognozę sześciu reguł, a potem jego wynik trafia do kolejki z opóźnieniem 24 godzin. Stan jest utrzymywany od początku historii, przez marzec do czerwca. Do uczenia nie wykorzystuje się prognoz wyznaczonych na tych samych etykietach, na których dopasowano model bazowy; wykorzystane reguły są obliczane wyłącznie z wcześniejszej historii.

Wybór nowego kandydata odbywa się według marcowego Brier, z ustaloną kolejnością rozstrzygania remisów. Zapis wyboru powstaje przed obliczaniem nowych predykcji czerwcowych. Punktami odniesienia są najlepsza marcowa reguła spośród wszystkich 19 reguł historycznych, najlepszy wcześniejszy kandydat oraz aktualnie wskazany wariant v3. Nie ogranicza się porównania do najsłabszej prostej metody.

Bramka dopuszczenia wymaga poprawy czerwcowego Brier o co najmniej 0,001, braku spadku poprawności przy progu 50% i ujemnej górnej granicy 95-procentowego przedziału różnicy wobec obu głównych konkurentów. Przedział obliczany jest przez 1000 losowań bloków dni. To kontrola rozwojowa, nie dowód publikacyjny: 14 dni, wspólne stacje i satelity, wiele wcześniejszych eksperymentów oraz brak korekty wszystkich porównań ograniczają siłę wnioskowania.

## Wyniki pilotażu

Poniższe wyniki dotyczą czerwca. „Poprawność” oznacza próg 50%; Brier jest błędem probabilistycznym, nie procentem poprawnych decyzji. Dwa wskazane warianty zostały wybrane na marcu, a nie na podstawie najlepszej wartości w tej tabeli.

| Zadanie i metoda | Liczba | Brier ↓ | Poprawność | Precyzja wskazań pozytywnych | Czułość |
|---|---:|---:|---:|---:|---:|
| Sygnał: obecna historia last10_2 | 1099 | 0,103398 | 85,81% | 84,75% | 93,59% |
| Sygnał: najmocniejsza marcowa historia link_last10_2 | 1099 | 0,104742 | 85,71% | 84,73% | 93,44% |
| Sygnał: wcześniejsza hybryda blend75 | 1099 | 0,094813 | 87,17% | 85,71% | 94,78% |
| Sygnał: nowa dynamic_hybrid, wybrana na marcu | 1099 | 0,098061 | 86,72% | 85,71% | 93,89% |
| Demodulacja warunkowa: last10_2 | 810 | 0,137993 | 80,37% | 86,20% | 84,13% |
| Demodulacja warunkowa: link_last10_2 | 810 | 0,137116 | 80,37% | 86,20% | 84,13% |
| Demodulacja warunkowa: obecna hybryda blend75 | 810 | 0,129811 | 80,74% | 87,12% | 83,58% |
| Demodulacja warunkowa: nowa dynamic_hybrid, wybrana na marcu | 810 | 0,125139 | 81,11% | 87,77% | 83,39% |

Przy demodulacji różnica Brier względem obecnej hybrydy wynosi −0,004672, z przedziałem od −0,011618 do +0,001995. Ujemna wartość oznacza poprawę, ale zakres dopuszcza również niewielkie pogorszenie. W porównaniu z najmocniejszą marcową regułą historyczną różnica wynosi −0,011977, z przedziałem od −0,018078 do −0,005417. Nowa metoda poprawia więc ten prosty punkt odniesienia w pilotażu, ale nie spełnia ostrzejszej bramki względem wcześniejszej hybrydy.

Dla sygnału nowa hybryda uzyskuje lepszy Brier od aktualnie wybranej historii last10_2, lecz gorszy od wcześniejszego blend75: różnica względem blend75 wynosi +0,003248. Nie ma uzasadnienia, by przedstawiać ten wynik jako bezwarunkowy postęp najlepszego osiągalnego modelu.

Wśród wyników dodatkowych wyróżnia się kontekstowa historia: dla demodulacji Brier 0,123399 i poprawność 81,60%, bez udziału modelu AI. Jest to jednak wariant zauważony po obejrzeniu czerwca; nie wolno po fakcie ogłosić go zwycięzcą wcześniej ustalonego wyboru. Stanowi przesłankę do odrębnego eksperymentu, a nie gotowy wynik końcowej walidacji.

Kalibrowana hybryda nie przyniosła jednoznacznej przewagi. Jej Brier dla demodulacji wyniósł 0,125688 wobec 0,125139 bez korekty. To praktyczny przykład, dlaczego dodatkowa warstwa dopasowania nie powinna być włączana automatycznie tylko dlatego, że w literaturze istnieją jej skuteczne zastosowania.

Opóźnienie informacji zwrotnej dla nowych wag do 72 godzin zmieniło Brier wybranej hybrydy na 0,096532 dla sygnału i 0,125946 dla demodulacji. Wyłączenie aktualizacji wag w obrębie panelu dało odpowiednio 0,100255 i 0,125291. Nie jest to pełny test opóźnienia całego systemu: bazowe prognozy historyczne w tym porównaniu nadal zakładają 24-godzinne opóźnienie.

## Wyniki rozszerzenia na pełną historię

Powtórny eksperyment zakończył się po około 206 sekundach. Zamiast uczyć wagi wyłącznie na małych panelach, wygenerował kolejno 68 862 prognozy historyczne dla sygnału oraz 49 073 dla demodulacji. Są to prognozy i późniejsze aktualizacje w dwóch zadaniach, nie 117 935 rozłącznych, unikalnych obserwacji. Nie wykorzystano rekordów po granicy 15 czerwca; stąd liczebności są mniejsze od pełnej liczby etykiet kohorty.

Przed pierwszą prognozą czerwcową dostępnych było 63 329 wyników sygnału i 44 846 warunkowych wyników demodulacji. Przed ostatnią odpowiednio 68 326 i 48 826. Dzięki temu można odróżnić wielkość zbioru rzeczywiście dostępnego do uczenia w konkretnym momencie od końcowej liczby przejrzanych wierszy.

W obu zadaniach wynik marcowy wybrał context_history. Nie zmieniono sześciu ekspertów, 30-dniowego zapominania, tempa uczenia ani siły powrotu do globalnej historii. Porównanie pełnego strumienia potwierdziło zgodność wszystkich 19 bazowych reguł z wcześniejszymi predykcjami dla każdego ocenianego przelotu.

| Zadanie i metoda | Brier ↓ | Poprawność | Precyzja wskazań pozytywnych | Czułość |
|---|---:|---:|---:|---:|
| Sygnał: obecna historia last10_2 | 0,103398 | 85,81% | 84,75% | 93,59% |
| Sygnał: kontekstowa historia, pełny strumień | 0,100877 | 85,26% | 84,91% | 92,25% |
| Demodulacja: obecna hybryda blend75 | 0,129811 | 80,74% | 87,12% | 83,58% |
| Demodulacja: kontekstowa historia, pełny strumień | 0,125808 | 81,11% | 86,77% | 84,69% |

Dla sygnału spadek Brier względem obecnej historii wynosi około 2,4%, ale poprawność maleje o około 0,55 punktu procentowego. Dla demodulacji spadek Brier względem obecnej hybrydy wynosi około 3,1%, a poprawność rośnie o około 0,37 punktu. Jednocześnie precyzja pozytywnych wskazań nieznacznie spada, a czułość rośnie. Nie jest zatem prawdziwe stwierdzenie, że każdy aspekt „precyzji” został poprawiony.

Różnica Brier nowej historii względem hybrydy dla demodulacji wynosi −0,004003, z przedziałem od −0,010022 do +0,002446. Wobec najlepszej marcowej reguły historycznej jest to −0,011308, z przedziałem od −0,015610 do −0,007126. Wyniki nadal nie uzasadniają automatycznego dopuszczenia zamiast najlepszego wcześniejszego kandydata.

Globalne ważenie historii bez rozróżnienia satelity i stacji uzyskało dla demodulacji Brier 0,138772, czyli gorzej niż zwykła średnia ekspertów, która osiągnęła 0,126047. To istotny wynik negatywny: dostarczenie dużej liczby etykiet może wzmacniać metodę dominującą w całej sieci, niekoniecznie odpowiednią dla każdej konkretnej stacji. Interpretacja ta jest hipotezą zgodną z porównaniem, nie niezależnie dowiedzioną przyczyną.

Czerwcowy panel sygnału obejmuje 19 satelitów i 45 stacji, a panel demodulacji 11 satelitów i 46 stacji. Duża historia uczenia nie usuwa ograniczenia związanego z małą liczbą dni i obiektów w zbiorze porównawczym. Konieczne jest poszerzenie przyszłej walidacji, a nie tylko zwiększenie liczby historycznych aktualizacji.

## Audyt i ograniczenia

W marcowym panelu sygnału 810 z 1473 obserwacji, a w czerwcowym 451 z 1099 ma epokę TLE późniejszą niż symulowany moment prognozy. Dla paneli warunkowej demodulacji są to odpowiednio 432 z 883 i 283 z 810. Epoka orbitalna nie jest znacznikiem publikacji, dlatego liczby te nie są dowodem użycia przyszłego TLE. Pokazują jednak, że potrzebne jest odrębne potwierdzenie jego dostępności w deklarowanym czasie.

Także zgodność sum kontrolnych i brak wprowadzania przyszłych etykiet nie udowadniają, że każdy historyczny parametr sprzętu, nadajnika i geometrii był dostępny w momencie symulowanej decyzji. Dla pełnej walidacji operacyjnej potrzebne są wersje konfiguracji oraz archiwalne daty przechwycenia. Używanie dzisiejszego profilu anteny do wyjaśniania dawnych obserwacji należy jawnie oznaczyć albo wyłączyć.

Nowe reguły czysto historyczne nie korzystają z wieku TLE ani cech geometrii w swoich sześciu prawdopodobieństwach. Nie rozwiązują jednak pozostałych problemów: nieznanego rzeczywistego opóźnienia etykiet, niepełnego pokrycia czasowego pobranych danych, zmian definicji etykiet i selekcji obserwacji przez dotychczasowy harmonogram. Dlatego także ich wyniki wymagają późniejszej walidacji na nowych danych.

Testy jednostkowe sprawdzają m.in. niezmienność wcześniejszej prognozy po odwróceniu przyszłej etykiety, poprawki, wycofania, duplikaty, brak informacji zwrotnej, opóźnienia, nieznane grupy oraz zachowanie po serializacji. Dodatkowy audyt wylicza końcowe wagi pilotażowej hybrydy niezależnie, bez używania przyrostowych funkcji aktualizacji. Testy syntetyczne służą sprawdzeniu implementacji; wszystkie wartości skuteczności w tabeli pochodzą z rzeczywistych etykiet SatNOGS.

Rozszerzony zestaw regresyjny początkowo zakończył się wynikiem 72 poprawnych testów i jednego błędu w istniejącym teście v3. Ten test izolował katalog wyników, ale odczytywał prawdziwy katalog wcześniejszych etapów; obecność ukończonego etapu 50 000 zmieniała jego założenia. Zamrożonego testu ani kodu v3 nie przepisano. Kontrola z odizolowanym także katalogiem wejściowym przeszła poprawnie, odtwarzając zamierzoną sytuację testową. Powtórny zestaw bez tego zależnego od środowiska testu dał 72 zaliczenia; nie przedstawia się go jako pełnego, bezwarunkowo zielonego zestawu v3.

## Konsekwencje dla dalszego rozwoju

Najwyższy priorytet ma teraz zwiększenie jakości informacji, a nie liczby parametrów modelu. Potrzebny jest rejestr wyników z rzeczywistym czasem ich otrzymania, rozróżnieniem źródła etykiety i wersji dekodera oraz powiązaniem konfiguracji stacji z okresem jej obowiązywania. Do badania poprawnego dekodowania należy utworzyć osobny wynik oparty na sprawdzonych ramkach, a do optymalizacji próbek — identyfikatorach treści i liczbie nowych danych.

Drugim priorytetem jest eksperyment z archiwalnymi prognozami pogody i informacją o antenach, porównujący identyczne obserwacje z tymi cechami i bez nich. Podział po czasie powinien być oddzielony od podziałów na nieznane satelity i stacje. Należy raportować osobno działanie bez lokalnej historii oraz po pierwszych kilku rzeczywiście otrzymanych wynikach.

Trzecim priorytetem jest powiązanie prognozy z użytecznością planera. Dla jednej obserwacji można szacować oczekiwany uzysk, ale sukces całego miesięcznego planu zależy także od konfliktów, blokerów, priorytetów i duplikacji danych. Sama lepsza klasyfikacja nie dowodzi większej liczby odebranych próbek. Do takiego twierdzenia potrzebne są rzeczywiste wyniki wybranego planu i uczciwy punkt odniesienia.

Potencjalnie wartościowy wkład naukowy obejmuje wspólny protokół opóźnionych etykiet, wersjonowanych TLE i sprzętu, adaptacyjnej historii oraz rzeczywistej oceny prospektywnej. Obecny eksperyment dostarcza działającej implementacji i wstępnych wyników, ale nie ustanawia pierwszeństwa ani publikacyjnej przewagi. Nie wykazano jeszcze generalizacji nowych mieszanek na niewidziane stacje lub satelity ani wzrostu miesięcznego uzysku.

## Artefakty i odtwarzalność

Implementacja znajduje się w modułach [history_ensemble.py](/home/ubuntu/telemetry-yield/src/telemetry_yield/planning/history_ensemble.py) i [reception_revision.py](/home/ubuntu/telemetry-yield/src/telemetry_yield/planning/reception_revision.py). Pilotaż opisują [protokół](/home/ubuntu/telemetry-yield/reports/reception-revision-v1-20260910/protocol.json), [wyniki](/home/ubuntu/telemetry-yield/reports/reception-revision-v1-20260910/results.json) oraz [audyt dodatkowy](/home/ubuntu/telemetry-yield/reports/reception-revision-v1-20260910/supplementary-audit.json). Rozszerzenie posiada osobny [protokół](/home/ubuntu/telemetry-yield/reports/reception-revision-full-history-v1-20260910/protocol.json) i [wyniki](/home/ubuntu/telemetry-yield/reports/reception-revision-full-history-v1-20260910/results.json). Tabele liczbowe udostępniono także w [CSV](/home/ubuntu/telemetry-yield/reports/reception-revision-v1-20260910/metrics.csv).

Identyfikator użytej kohorty to `7b1870083a6988722795a98260dbc604401ae02ab566408320649b59c4c44ab2`. Pliki wyboru kandydata zawierają zapisane decyzje marcowe. Predykcje i ślady aktualizacji są zachowane, a zakończone artefakty mają sumy kontrolne. Końcowe zbiory testowe i zarezerwowane grupy nie były używane w opisanych eksperymentach.

## Przypisy

[^1]: SatNOGS, [Operation](https://wiki.satnogs.org/Operation), aktualizacja 28 sierpnia 2026, sekcje Observations ratings i Vetting artifacts.
[^2]: SatNOGS, [Artifacts](https://wiki.satnogs.org/Artifacts), 21 sierpnia 2020, opis ramek i obrazów.
[^3]: Rony Ronen, Boaz Ben-Moshe, [Maximizing Nanosatellite Throughput via Dynamic Scheduling and Distributed Ground Stations](https://pmc.ncbi.nlm.nih.gov/articles/PMC12737175/), Sensors 25(24), 7538, 2025; DOI 10.3390/s25247538, sekcje 3.2.2, 4 i 7.
[^4]: Deepak Vasisht, Jayanth Shenoy, Ranveer Chandra, [L2D2: Low Latency Distributed Downlink for Low Earth Orbit Satellites](https://conferences.sigcomm.org/sigcomm/2021/files/papers/3452296.3472932.pdf), SIGCOMM 2021, sekcje 3.2, 4.2–4.4 i 6.1; DOI 10.1145/3452296.3472932.
[^5]: Tyler H. McCormick, Adrian E. Raftery, David Madigan, Randall S. Burd, [Dynamic Logistic Regression and Dynamic Model Averaging for Binary Classification](https://sites.stat.washington.edu/people/raftery/Research/PDF/McCormick2012.pdf), Biometrics 68(1), 23–30, 2012; DOI 10.1111/j.1541-0420.2011.01645.x.
[^6]: Alexey Chernov, Fedor Zhdanov, [Prediction with Expert Advice under Discounted Loss](https://arxiv.org/abs/1005.1918), 2010.
[^7]: Alexander Korotin, Vladimir V’yugin, Evgeny Burnaev, [Adaptive Hedging under Delayed Feedback](https://arxiv.org/abs/1902.10433), wersja z 22 czerwca 2019.
[^8]: Jean Feng, Alexej Gossmann, Berkman Sahiner, Romain Pirracchio, [Bayesian logistic regression for online recalibration and revision of risk prediction models with performance guarantees](https://escholarship.org/content/qt54z772qd/qt54z772qd.pdf), JAMIA 29(5), 841–852, 2022; DOI 10.1093/jamia/ocab280.
[^9]: Meelis Kull, Telmo Silva Filho, Peter Flach, [Beta calibration: a well-founded and easily implemented improvement on logistic calibration for binary classifiers](https://proceedings.mlr.press/v54/kull17a.html), AISTATS 2017, PMLR 54, 623–631.
[^10]: Liudmila Prokhorenkova i współautorzy, [CatBoost: unbiased boosting with categorical features](https://papers.neurips.cc/paper_files/paper/2018/file/14491b756b3a51daac41c24863285549-Paper.pdf), NeurIPS 2018, sekcje 3–5.
[^11]: Anastasios N. Angelopoulos, Stephen Bates, [A Gentle Introduction to Conformal Prediction and Distribution-Free Uncertainty Quantification](https://arxiv.org/abs/2107.07511), 2021, wersja 6; sekcje dotyczące przesunięcia rozkładu i założeń pokrycia.
[^12]: ITU-R, [P.618-14 — Propagation data and prediction methods required for the design of Earth-space telecommunication systems](https://www.itu.int/dms_pubrec/itu-r/rec/p/R-REC-P.618-14-202308-I!!PDF-E.pdf), sierpień 2023, Annex 1, Introduction; poprawki redakcyjne 2024.
[^13]: ITU-R, [P.531-16 — Ionospheric propagation data and prediction methods required for the design of satellite networks and systems](https://www.itu.int/dms_pubrec/itu-r/rec/p/R-REC-P.531-16-202509-I!!PDF-E.pdf), zatwierdzenie wrzesień 2025, sekcja 4.3; plik opublikowany w maju 2026.
[^14]: Open-Meteo, [Historical Forecast API](https://open-meteo.com/en/docs/historical-forecast-api), sekcje porównujące archiwa i Single Runs, dostęp 10 września 2026.
[^15]: Open-Meteo, [Weather Forecast API](https://open-meteo.com/en/docs), horyzont prognozy, dostęp 10 września 2026.

## Sources

1. SatNOGS. [Operation](https://wiki.satnogs.org/Operation). 2026. Definicje statusów, obecności sygnału i oceny artefaktów.
2. SatNOGS. [Artifacts](https://wiki.satnogs.org/Artifacts). 2020. Rodzaje wyników odbioru; ograniczenia znaczenia listy artefaktów.
3. Ronen R., Ben-Moshe B. [Maximizing Nanosatellite Throughput via Dynamic Scheduling and Distributed Ground Stations](https://www.mdpi.com/1424-8220/25/24/7538). Sensors 25(24):7538, 2025. DOI 10.3390/s25247538. Pełny tekst zweryfikowany w PMC.
4. Vasisht D., Shenoy J., Chandra R. [L2D2: Low Latency Distributed Downlink for Low Earth Orbit Satellites](https://conferences.sigcomm.org/sigcomm/2021/files/papers/3452296.3472932.pdf). ACM SIGCOMM, 2021. DOI 10.1145/3452296.3472932.
5. McCormick T.H., Raftery A.E., Madigan D., Burd R.S. [Dynamic Logistic Regression and Dynamic Model Averaging for Binary Classification](https://sites.stat.washington.edu/people/raftery/Research/PDF/McCormick2012.pdf). Biometrics 68:23–30, 2012. DOI 10.1111/j.1541-0420.2011.01645.x.
6. Chernov A., Zhdanov F. [Prediction with Expert Advice under Discounted Loss](https://arxiv.org/abs/1005.1918). 2010. Dyskontowanie strat ekspertów.
7. Korotin A., V’yugin V., Burnaev E. [Adaptive Hedging under Delayed Feedback](https://arxiv.org/abs/1902.10433). 2019. Ważenie ekspertów przy opóźnionych wynikach.
8. Feng J., Gossmann A., Sahiner B., Pirracchio R. [Bayesian logistic regression for online recalibration and revision of risk prediction models with performance guarantees](https://escholarship.org/content/qt54z772qd/qt54z772qd.pdf). JAMIA 29(5):841–852, 2022. DOI 10.1093/jamia/ocab280.
9. Kull M., Silva Filho T., Flach P. [Beta calibration](https://proceedings.mlr.press/v54/kull17a.html). AISTATS 2017, PMLR 54:623–631. Tożsamość w rodzinie kalibracyjnej i ryzyko pogorszenia prognoz.
10. Prokhorenkova L., Gusev G., Vorobev A., Dorogush A.V., Gulin A. [CatBoost: unbiased boosting with categorical features](https://papers.neurips.cc/paper_files/paper/2018/file/14491b756b3a51daac41c24863285549-Paper.pdf). NeurIPS, 2018.
11. Angelopoulos A.N., Bates S. [A Gentle Introduction to Conformal Prediction and Distribution-Free Uncertainty Quantification](https://arxiv.org/abs/2107.07511). 2021, wersja 6. Założenia gwarancji pokrycia i dane zależne.
12. ITU-R. [Recommendation P.618-14](https://www.itu.int/rec/R-REC-P.618-14-202308-I/en). 2023, poprawki 2024. Propagacja Ziemia–kosmos, zależność od pasma i geometrii.
13. ITU-R. [Recommendation P.531-16](https://www.itu.int/rec/R-REC-P.531-16-202509-I/en). 2025, plik angielski 2026. Propagacja jonosferyczna i rotacja Faradaya.
14. Open-Meteo. [Historical Forecast API](https://open-meteo.com/en/docs/historical-forecast-api). Dokumentacja bieżąca, dostęp 10 września 2026. Różnice między archiwami prognoz.
15. Open-Meteo. [Weather Forecast API](https://open-meteo.com/en/docs). Dokumentacja bieżąca, dostęp 10 września 2026. Zakres horyzontu prognoz.

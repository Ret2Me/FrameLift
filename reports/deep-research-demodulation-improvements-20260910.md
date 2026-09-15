# Kierunki rozwoju odbiornika telemetrycznego

## Wnioski i rekomendacja

Najbardziej obiecującym kierunkiem jest odbiornik, który dopasowuje się do rzeczywistych zniekształceń nagrania, zachowuje informację o niepewności symboli i wykorzystuje kontekst czasowy całego przelotu. Dla archiwalnego audio pierwszeństwo mają synchronizacja offline oraz detekcja sekwencji uwzględniająca skorelowane zakłócenia. Dla surowego IQ największą zmianą jakościową będzie zachowanie fazy przez koherentny tor CPM/GMSK, zamiast natychmiastowego sprowadzania sygnału do wyjścia dyskryminatora FM.

To rekomendacja badawcza, a nie obietnica określonego wzrostu liczby ramek. Podstawowe składniki są znane: NASA opisywała ponowne przetwarzanie telemetrii z wykorzystaniem przyszłych fragmentów nagrania już w 1993 roku; ViterbiNet i Meta-ViterbiNet wykorzystują uczenie detektora i adaptację do zmiennego kanału; istnieją także koherentne odbiorniki GMSK sprawdzone na prawdziwej misji księżycowej. Samo połączenie słów „AI”, „Viterbi” i „offline” nie stanowi więc nowości naukowej.[^1][^2][^3][^4]

Potencjalnie wartościowym wkładem jest wykazanie, że **konkretny model niedoskonałości rzeczywistych archiwów** odzyskuje dodatkowe, zweryfikowane pakiety ponad mocny zestaw istniejących metod — na nowych stacjach, przelotach i satelitach. Szczególnie interesująca pozostaje hipoteza, że charakterystyka toru odbiorczego i kompresji audio może być estymowana z poprawnie odebranych pakietów, a następnie bez przekazywania ich treści wykorzystana do innych emisji.

| Priorytet | Usprawnienie | Audio po FM | Surowe IQ | Co ma poprawić |
|---|---|---|---|---|
| P1 | Zegar i synchronizacja estymowane na całym przelocie | Tak | Tak, także częstotliwość i faza | Utrata początku transmisji, dryf i utrata synchronizacji |
| P1 | Model kanału i skorelowanego szumu w detektorze sekwencji | Tak | Tak, po odpowiednim modelowaniu | Zniekształcenia impulsów, zależne błędy sąsiednich symboli |
| P1 dla IQ | Koherentny CPM/GMSK z miękkim wyjściem | Nie dla utraconej fazy RF | Tak | Strata informacji w prostym dyskryminatorze |
| P2 | Synchronizacja wspomagana rzeczywistym FEC | Zależnie od zachowanej modulacji i kodu | Tak | Praca poniżej progu zwykłej pętli synchronizacji |
| P2 | Uczenie lokalnej metryki detektora, nie generatora telemetrii | Tak | Tak | Niedopasowanie prostego modelu do rzeczywistego toru |
| P2 badawcze | Model niepewności związanej z kodekiem | Tak | Nie jako cecha surowego IQ | Nierówna wiarygodność próbek po kompresji |
| P2 warunkowe | Łączenie różnych odbiorów tej samej emisji | Miękkie decyzje, warunkowo | Także koherentnie | Niezależne zaniki i lokalne zakłócenia |
| P3 | Separacja nakładających się transmisji | Ograniczona przez FM | Preferowane | Zakłócenia współkanałowe |

Priorytety są oceną projektową. „Tak” oznacza możliwość skonstruowania eksperymentu, nie udowodniony zysk. Stan literatury i odczytanych źródeł odpowiada 10 września 2026 roku. Nie jest to formalny dowód pierwszeństwa ani kompletny systematyczny przegląd wszystkich publikacji.

## Punkt wyjścia i granice dowodów

Zapisane wyniki poprzedniej kampanii obejmują 197 ukończonych porównań tego samego PCM: 2011 par obserwacja–PDU dla portfolio projektu i 1108 dla porównywanego komponentu gr-satellites. Portfolio ma 904 pary własne i pomija jedną parę komponentu odniesienia. To dane o jednym satelicie CANVAS i jednej rodzinie modulacji/framingu, nie wynik uniwersalnego odbiornika. Pozostałych wybranych obserwacji nie wolno dopisywać do mianownika udanych analiz.[^5]

Wynik ten nie mierzy jeszcze efektu nowego uczonego detektora sekwencji. Nowa gałąź jest osobnym eksperymentem: trójwspółczynnikowy model impulsu, detekcja Viterbiego i transfer parametrów między rozłącznymi oknami. Jej bieżący interfejs zwraca twarde decyzje ±1, **nie skalibrowane LLR**. Odbiornik koherentny IQ, probabilistyczne wygładzanie zegara, model szumu zależny od kodeka i turbo-synchronizacja są kolejnymi propozycjami, a nie ukończonymi funkcjami tej wersji.[^6]

Należy rozdzielać trzy rezultaty: pakiet dodatkowy wobec jednego uruchomienia dekodera, pakiet dodatkowy wobec mocnego portfolio oraz pakiet nieobecny w pobranym archiwum. Żaden z nich samodzielnie nie dowodzi, że danej informacji nigdy nie odebrała żadna stacja. Przedmiotem porównania powinien być odtwarzalny program i konfiguracja, a historyczny wynik stacji pozostawać osobnym punktem odniesienia.

### Co naprawdę znajduje się w OGG

Rozszerzenie pliku nie określa warstwy sygnału. W analizowanej ścieżce FSK mono OGG przechowuje sygnał po demodulacji FM; dokumentacja gr-satellites opisuje go jako przebieg NRZ, podczas gdy wejście IQ zawiera jeszcze zmodulowany sygnał FSK. W tej reprezentacji można poprawiać filtrację, czas próbkowania i decyzje symbolowe, ale nie ma kompletu oryginalnej amplitudy i fazy RF.[^7]

Sygnał analityczny uzyskany z takiego audio nie odtwarza utraconego zespolonego pomiaru radiowego. Jednocześnie nie należy uogólniać tego ograniczenia na każdy plik mono: inne tryby zapisu mogą zachowywać sygnał pasmowy, a dwukanałowy WAV może zawierać IQ. Każdy rekord musi mieć jawne pole opisujące reprezentację i wcześniejsze transformacje.

## Usprawnienia dla OGG i WAV

### 1. Synchronizacja na całym przelocie zamiast niezależnych okien

W odbiorniku offline mocniejszy fragment znajdujący się później może pomóc w odczycie wcześniejszego, słabszego fragmentu. Buffered Telemetry Demodulator NASA został zaprojektowany właśnie do ponownego przetwarzania zapisanych próbek po uzyskaniu synchronizacji, z estymatami stanu sygnału przenoszonymi wstecz. Jest to bezpośredni wcześniejszy dorobek wobec ogólnego pomysłu „wróć do początku po złapaniu sygnału”.[^1]

Proponowane rozwinięcie projektu: utrzymywać stan zegara obejmujący przesunięcie symbolu, błąd jego okresu i wolny dryf, wraz z niepewnością. Poprawne pakiety oraz niezależne miary kształtu impulsów dostarczają obserwacji. Wygładzanie w obu kierunkach tworzy kilka wiarygodnych trajektorii próbkowania, a nie jedną sztywną wartość baudrate dla całego pliku. Dla odrębnych burstów trzeba dopuścić reset fazy zegara; ciągłość częstotliwości nie oznacza ciągłości fazy.

Eksperyment powinien porównywać te same okna w czterech wariantach: niezależny bank, obecny Gardner, estymacja korzystająca tylko z przeszłości i wygładzanie korzystające także z przyszłości. Trzeba mierzyć odzysk przy początku i końcu emisji, w krótkich zanikach i po rzeczywistych przerwach. Parametry i regułę resetu ustala się na danych rozwojowych, nie po obejrzeniu dodatkowych ramek w teście.

Wersja odporna powinna umieć zrezygnować z transferu. Mocny pakiet kilkadziesiąt sekund wcześniej może opisywać już inny stan nadajnika albo odbiornika. Jeżeli niepewność predykcji rośnie, należy rozszerzyć zbiór hipotez lub wrócić do lokalnego odbioru, zamiast wymuszać błędną synchronizację.

### 2. Detektor uczący się także zależności między zakłóceniami

Obecny model sekwencyjny minimalizuje sumę kwadratów różnic między próbką i przewidywanym sygnałem. Ta metryka odpowiada szczególnemu modelowi niezależnego szumu Gaussa o stałej wariancji. Filtry, niedopasowanie impulsu i tor kompresji mogą sprawiać, że kolejne reszty są zależne. To należy zmierzyć, nie założyć na podstawie samego rozszerzenia OGG.

Detekcja Viterbiego z pamięcią szumu jest znana: Kavčić i Moura opisali model Markowa, także z szumem zależnym od sygnału, oraz metryki wykorzystujące lokalne statystyki zakłóceń. Pokazali również kompromisy związane z krótszą autoregresyjną aproksymacją. Ich zastosowaniem motywującym były kanały zapisu danych, a nie SatNOGS — przeniesienie do post-FM audio jest tutaj hipotezą inżynierską.[^8]

Proponuję zacząć od reszt na poprawnych pakietach: autokorelacji, rozkładu amplitud i stabilności między oknami. Następnie porównać model bez pamięci z AR(1) i AR(2), stosując ten sam filtr wybielający do obserwacji **i przewidywanego sygnału**. Samo „wybielenie audio” przy pozostawieniu starego modelu impulsu zmieniłoby zadanie i mogłoby pogorszyć wynik.

Druga oś to zachowanie dwóch lub większej liczby próbek na symbol w estymacji impulsu. Ułamkowo próbkowana equalizacja GMSK była badana co najmniej na początku lat 2000.; nie jest nowym algorytmem. Może jednak ograniczyć wrażliwość implementacji na moment próbkowania. Nie każda konfiguracja na tym skorzysta, dlatego potrzebna jest osobna ablacja „więcej próbek” versus „lepszy model szumu”, przy tym samym budżecie hipotez.[^9]

Najważniejszy warunek awansu: zysk liczby poprawnych pakietów, nie wyłącznie mniejszy błąd dopasowania na ramkach treningowych. Możliwe jest idealne dopasowanie silnych kotwic bez poprawy słabych celów. Trzeba oceniać różne treści pakietów i różne odcinki czasu; zachować także przypadki, w których modeli nie udało się nauczyć.

### 3. Miękkie decyzje i synchronizacja wspomagana kodowaniem

Detektor zwracający jedynie znak usuwa informację, czy symbol był oczywisty, czy bardzo niepewny. Kolejna wersja powinna udostępnić miękką informację z BCJR albo odpowiednio zweryfikowanej aproksymacji soft-output. W protokołach z kodowaniem korekcyjnym umożliwia to pełniejszą współpracę demodulatora i dekodera.

Code-aided synchronization nie jest nowym pomysłem: Ali, Wasenmüller i Wehn opisali w 2013 roku implementację sprzętową, w której wyniki kolejnych iteracji dekodera poprawiają synchronizację. To argument za wykonalnością takiego toru, ale nie dowód zysku dla każdej transmisji satelitarnej.[^10]

Integrację należy zacząć od jednego jawnego profilu kodowanego: znany kod, kolejność przeplotu i randomizacji, znaczniki synchronizacji oraz reguła końcowej kontroli. „CCSDS” to rodzina standardów, a nie jedna kombinacja modulacji i FEC. Z kolei samo AX.25 z FCS nie daje tych samych informacji zwrotnych co LDPC, turbo lub kod splotowy. Nie wolno uznać dowolnego szukania bitów do przejścia CRC za równoważne dekodowaniu FEC.

Test powinien obejmować osobno idealnie zsynchronizowane symbole, zaburzenia zegara, utratę synchronizacji i pełne wyszukiwanie emisji w szumie. Dzięki temu będzie wiadomo, czy zysk pochodzi z lepszego demodulatora, kodu korekcyjnego czy samego odnalezienia pakietu. Niezależna kontrola otrzymanych bajtów i limit iteracji muszą pozostać obowiązkowe.

### 4. Model błędu zależny od kodeka

Vorbis opisuje widmo przez składowe floor i residue, kwantyzację wektorową oraz transformację MDCT z nakładaniem bloków. Parametry kodeka mogą zatem dostarczyć kontekstu do badania błędu po rekonstrukcji. Nie są jednak gotową mapą wariancji szumu RF; floor opisuje reprezentację widmową sygnału, nie poziom zakłóceń radiowych.[^11]

Własna hipoteza badawcza: wybrane cechy bloku Vorbis, lokalna energia i reszty modelu impulsowego pomagają przewidzieć wiarygodność symboli lepiej niż jedna globalna wariancja. Mały model mógłby modyfikować wagi metryki detektora, zamiast próbować generować „czyste audio”. Taki eksperyment jest bardziej precyzyjny niż ogólne odszumianie siecią neuronową.

Najpierw potrzebne są pary **tego samego** czystego PCM i jego zakodowanych wersji, z dokładnym wyrównaniem próbek. Na danych rozwojowych można zmieniać jakość kompresji oraz kształt impulsu, a końcowy test przeprowadzić na niewidzianych transmisjach i rzeczywistych OGG. Oryginalnego OGG nie należy ponownie kompresować i przedstawiać tego jako niezależnego pomiaru straty pierwszego kodowania.

Warunek falsyfikacji jest prosty: jeśli informacja z kodeka nie poprawia odzysku ponad model korzystający wyłącznie z PCM i jego reszt, nie ma podstaw do utrzymywania tej złożoności. W odczytanych źródłach nie znalazło się bezpośrednie potwierdzenie zysku takiego odbiornika na SatNOGS. Jest to kandydat do eksperymentu i dalszego sprawdzenia pierwszeństwa, nie gotowe odkrycie.

### 5. Uczenie małej części detektora

ViterbiNet zastępuje uczonym modelem część obliczeń zależną od kanału, zachowując strukturę algorytmu Viterbiego. Meta-ViterbiNet rozwija adaptację do zmiennego kanału, korzystając z wiedzy o kodowaniu i decyzji odbiornika. Obie prace pokazują, że hybryda modelu fizycznego i uczenia jest pełnoprawnym, ale już istniejącym kierunkiem.[^2][^3]

Dla projektu proponuję dopiero po klasycznym modelu AR przetestować małą funkcję lokalnej metryki: wejściem są fragment symboli, przewidywanie kanału i kilka miar jakości; wyjściem koszt hipotezy albo skalibrowana niepewność. Model nie powinien dostawać z archiwum oczekiwanej treści pakietu, numeru obserwacji jako skrótu do etykiety ani informacji o końcowym wyniku celu. Także uczenie wyłącznie z CRC-poprawnych ramek może preferować łatwiejsze warunki.

Ważne ostrzeżenie przynosi preprint Luostariego i współautorów z sierpnia 2026 roku: w ich pomiarach OFDM etykiety kanału wyprowadzone z zaszumionego wejścia powodowały problem skorelowanego błędu nadzoru; samo zwiększanie udziału danych rzeczywistych go nie usuwało. Etykiety bitowe po kontroli CRC zachowywały się korzystniej. Dotyczyło to konkretnego systemu SISO/16-QAM, nie GMSK, a artykuł był zgłoszony do publikacji, więc przenosimy ostrzeżenie metodologiczne, nie jego wynik liczbowy.[^12]

Eksperyment: porównać ten sam detektor z metryką Gaussa, AR, odporną metryką klasyczną oraz małym modelem uczonym. Ujednolicić dane, zakres kontekstu i liczbę prób. Jeśli neuronowa wersja przegrywa poza znaną stacją albo wymaga znacznie większego kosztu dla pomijalnego zysku, powinna pozostać wariantem badawczym.

## Usprawnienia odblokowywane przez IQ

### 6. Koherentny CPM/GMSK i odporność na parametry nadajnika

Zachowanie IQ pozwala wykorzystać pamięć fazy modulacji i tworzyć miękkie decyzje przed nieodwracalnym uproszczeniem reprezentacji. Istnieją odbiorniki CPM łączące rozkład Laurenta z probabilistycznym śledzeniem fazy i przebiegiem forward–backward. Są również metody odporne na nieznany indeks modulacji; to ważny punkt odniesienia dla satelitów o niedokładnej dewiacji, a nie nowość wynikająca z samego dodania estymacji parametrów.[^13][^14]

Najbardziej użytecznym przykładem orbitalnym jest Longjiang: opublikowano tor koherentnego GMSK ze znacznikiem ASM, filtrem związanym z reprezentacją Laurenta i kodowaniem turbo oraz analizę rzeczywistych odbiorów z wielu stacji. Nie można przenosić jego progu odbioru na CANVAS: inna szybkość i kodowanie zmieniają problem. Można natomiast użyć tej pracy jako sprawdzalnej architektury odniesienia dla drugiej rodziny sygnałów.[^4]

Plan dla projektu powinien zaczynać się od dwóch torów z identycznego IQ: obecnego FM→PCM i koherentnego CPM. Potem dokładamy estymację BT/indeksu modulacji, lokalnej częstotliwości i dryfu zegara. Różnica wyniku powinna być mierzona przy identycznym wycinku, paśmie wejściowym i danych odniesienia trzymanych wyłącznie w evaluatorze. Dodatkowa analiza przy równym koszcie wykaże, ile przewagi wynika z większej liczby obliczeń.

Koherentny tor może przegrać, gdy błędnie oszacuje fazę albo przyjmie nieprawidłowy model modulacji. Trzeba więc zachować gałąź niekoherentną i raportować zestawy pakietów każdej z osobna. Przenoszenie jednej ciągłej fazy przez przerwy w nadawaniu wymaga dowodu, że nadajnik jej nie resetuje.

### 7. Wspólna estymacja czasu, nośnej i początku pakietu

Hosseini i Perrins przedstawili wspólną synchronizację burst-mode CPM z wykorzystaniem znanej, specjalnie dobranej preambuły. To wskazuje możliwy kierunek dla krótkich transmisji, ale równocześnie ograniczenie: historyczny nadajnik ma już ustaloną preambułę, której odbiornik archiwalny nie może przeprojektować.[^15]

Wdrożenie powinno wykorzystywać wyłącznie rzeczywiste stałe elementy protokołu: ASM, dozwolone preambuły i znane relacje kodowania. W przypadku G3RUH trzeba respektować stan scramblera oraz NRZI; bajt flagi HDLC nie zawsze oznacza stały, identyczny fragment fizycznego przebiegu. Zysk z wykorzystania znanej preambuły trzeba oddzielić od zysku pozyskanego dzięki treści już zdekodowanego pakietu.

Dobrym eksperymentem jest usunięcie silnych kotwic z części syntetycznych nagrań. Pozwoli to zbadać, czy nowy tor sam odnajduje słabą emisję, czy jedynie pomaga po pierwszym sukcesie. W prawdziwym korpusie trzeba osobno raportować obserwacje z zerem ramek wszystkich bazowych gałęzi: obecny transfer uczony na poprawnych pakietach nie ma w nich skąd wziąć kotwicy.

### 8. Łączenie odbiorów i separacja zakłóceń

Quasar pokazał koherentne łączenie satelitarnych odbiorów z wielu tanich stacji. Praca wykorzystywała synchronizację czasu, częstotliwości i fazy, informacje o ruchu satelity oraz sygnały pomocnicze. Nie jest to dowód, że da się po prostu zsumować przypadkowe historyczne OGG albo IQ bez wystarczających metadanych.[^16]

Najpierw należy znaleźć rzeczywiście jednoczesne odbiory **tej samej emisji**, a potem porównać: najlepszą stację, sumę zbiorów jej i innych poprawnych ramek, łączenie miękkich decyzji oraz koherentne IQ. Warunkiem wartościowego wyniku jest odzysk ponad zwykłą sumę już odebranych pakietów, nie tylko dłuższe pokrycie przelotu. Różne hipotezy demodulacji tego samego pliku nie są niezależnymi stacjami: sumowanie ich LLR jak niezależnych pomiarów sztucznie zwiększyłoby pewność.

Drugim kierunkiem jest separacja zakłóceń współkanałowych. RF Challenge wykazuje potencjał sieci UNet/WaveNet, lecz przy konkretnych mieszaninach, osobnym uczeniu modeli i upraszczających założeniach synchronizacji. Autorzy ujawnili też przypadek przecieku między sygnałami zbioru walidacyjnego i testowego w konkursie. Wynik konkursowy bez analizy konstrukcji danych nie jest wystarczającą podstawą do wyboru architektury.[^17]

W projekcie warto zacząć od kontrolowanego sumowania znanego IQ z niezależnie nagranym zakłóceniem, a następnie od prawdziwych kolizji. Jeśli silniejszy pakiet jest poprawnie odczytany, można badać rekonstrukcję jego fali i odejmowanie z IQ, z kontrolą reszty i zachowaniem oryginału. Odejmowanie z wyjścia FM nie jest równoważne odejmowaniu fal radiowych, bo dyskryminator jest nieliniowy. Modele generujące estetyczny spektrogram bez poprawy BER i liczby ramek nie spełniają celu.

### Co odłożyć

UAMP dla CPM jest interesującą nowszą propozycją łączącą equalizację, detekcję i dekodowanie. W odczytanym preprincie testowano jednak między innymi czterowartościowy CPM, idealną synchronizację, kanały selektywne i prefiks cykliczny. Nie jest to bezpośredni zamiennik dla istniejącego GMSK/AX.25 bez takiej struktury. Należy wrócić do niego dopiero po wykazaniu, że dłuższa pamięć kanału faktycznie ogranicza obecny odbiornik.[^18]

Podobnie klasyfikator „wszystkich modulacji” nie rozwiązuje automatycznie synchronizacji i framingu. Cechy cyklostacjonarne mogą pomagać w wyborze rodziny hipotez, a badania klasyfikacji pokazują znaczenie generalizacji między różnymi rozkładami danych. Nie są jednak dowodem poprawnego odzysku telemetrii. Klasyfikator powinien mieć wyjście „nie wiem” i bezpieczną ścieżkę przeszukiwania pozostałych profili.[^19]

## Zbiory i plan uczciwej oceny

### RML24 z Scientific Data

Artykuł opisuje hybrydowy zbiór: rzeczywisty tor RF oraz modelowane zaburzenia, a nie rzeczywiste łącze z orbitującym satelitą. Deklaruje 1 386 000 rekordów, 22 typy modulacji i okna 2048 próbek IQ przy 1 MHz; to 2,048 ms, nie automatycznie kompletna ramka AX.25 lub CCSDS. Wariant PKL zawiera bity odniesienia. Szczegółowe parametry CFO, Dopplera i szumu fazy nie mają indywidualnych etykiet potrzebnych do bezpośredniego pomiaru błędu ich estymacji.[^20]

Repozytorium autorów ostrzega natomiast, że udostępniona wersja zawiera 21 modulacji ze względu na problemy z SOQPSK-PM, i rekomenduje format PKL. Należy więc zamrozić konkretną wersję i zinwentaryzować zawartość plików; liczby z artykułu nie zastępują manifestu faktycznie analizowanych rekordów. Nie należy też interpretować pola „Code rate” z README jako potwierdzonego profilu kodu korekcyjnego bez sprawdzenia semantyki danych.[^21]

Metadane Zenodo wskazują dwa alternatywne ZIP-y, każdy około 20 GB. Dla testu BER nie ma powodu automatycznie pobierać obu. PKL należy traktować jako format wymagający ostrożnej konwersji: dane po weryfikacji można jednorazowo przenieść do neutralnej reprezentacji, a właściwy benchmark utrzymać w Ruście. W tym przeglądzie nie wykonano ponownego pobrania ani inspekcji wnętrza dużych archiwów.[^22]

Zastosowanie RML24: rozwój modułów demodulacji, test błędów bitowych i odporności na różne modulacje. Zastosowanie prawdziwego SatNOGS/Polyitan: odzysk całych ramek, akwizycja w długim nagraniu, fałszywe akceptacje i koszt operacyjny. Te zbiory odpowiadają na różne pytania; dobry wynik na jednym nie zastępuje drugiego.

### Podział danych i kontrola przecieków

Jednostką podziału musi być co najmniej pełne nagranie, a przy kilku stacjach ten sam przelot lub zdarzenie transmisyjne. Losowe rozdzielanie nakładających się wycinków nie tworzy niezależnego testu. Najnowszy przykład z RF DroneRF pokazuje, jak wynik klasyfikatora może załamać się po przejściu na podział po nagraniach; jest to ostrzeżenie metodologiczne, nie oszacowanie skali błędu naszego dekodera.[^23]

Potrzebne są trzy rozłączne role: dane do implementacji, dane do wyboru parametrów i końcowy test bez dalszego strojenia. Cztery dotychczasowe obserwacje rozwojowe mogą służyć do szybkiej regresji i debugowania, ale nie stanowią nowego holdoutu. Po przejrzeniu wyników wcześniejsza kampania również nie jest już nietkniętym testem dla kolejnych pomysłów.

W RML24 trzeba dodatkowo sprawdzić, czy warianty SNR tej samej nadanej sekwencji, powtarzające się fragmenty i skorelowane rekordy nie trafiają do różnych części zbioru. Jeśli metadane nie pozwalają odtworzyć grupowania, ograniczenie należy jawnie opisać, a nie nazywać losowego podziału testem niezależności fizycznej.

### Metryki i mocny punkt odniesienia

Główny wynik: dodatkowe poprawne pakiety i ich bajty, po deduplikacji z zachowaniem tożsamości emisji. Uzupełnienia: odsetek uratowanych obserwacji zerowych, pakiety utracone względem każdej referencji, czas CPU i ścienny, szczyt pamięci oraz real-time factor. BER jest właściwy tam, gdzie dostępny jest rzeczywiście znany strumień nadany; samo porównanie do niekompletnego archiwum nie dostarcza takiej prawdy.

Porównania powinny objąć obecne portfolio oraz dobrze skonfigurowany, przypięty wersją gr-satellites i przynajmniej jedną dodatkową implementację dla danego profilu. Dokumentacja gr-satellites pokazuje, że ustawienia synchronizacji są istotne; jego domyślne uruchomienie nie wyznacza granicy osiągalnej czułości. Należy pokazać zarówno wynik maksymalnego odzysku offline, jak i wynik przy równym budżecie obliczeń.[^7]

Nie wolno przypisywać całego wzrostu systemu ostatniemu modułowi. Wymagana jest ablacja: bazowe gałęzie; model kanału; pamięć szumu; więcej próbek na symbol; transfer czasowy; miękkie decyzje; komponent uczony. Pierwszy niezbędny wariant kontrolny dla obecnej gałęzi to zwykły slicer na dokładnie tym samym przeniesionym banku czasów symboli, z porównaniem zerowego oraz nauczonego progu zależnego od wzmocnienia. Bez niego nie wiadomo, jaka część zysku wynika z MLSE, a jaka z innych hipotez synchronizacji i progów. Dla każdej wersji trzeba zapisać próby bez zysku i błędy wykonania. Poprawa w jednym wybranym pliku jest demonstracją, nie dowodem średniego efektu.

### Fałszywe ramki i niezależna kontrola

CRC ogranicza przypadkową akceptację, ale przy rozbudowanym przeszukiwaniu nie zastępuje testu całego systemu na negatywach. Ta sama implementacja CRC uruchomiona drugi raz wykrywa błędy programistyczne tylko w ograniczonym zakresie; niezależna implementacja jest cenna, lecz nadal nie tworzy drugiego niezależnego dowodu radiowego. Mocniejsze potwierdzenie może pochodzić z innej stacji albo zgodności z niezależnie znaną transmisją.

Negatywy powinny obejmować rzeczywisty szum odbiornika, RFI, niewłaściwe profile i fragmenty bez emisji, także z modelem nauczonym na poprawnej kotwicy. Sam test „brak kotwicy → nie uruchomiono MLSE → zero ramek” nie sprawdza ryzyka nowego detektora.

Przy założeniu stałej częstości zdarzeń Poissona zero fałszywych akceptacji przez T godzin daje jednostronną górną granicę 95% równą −ln(0,05)/T, około 3/T. Stąd 20 godzin bez błędu oznacza granicę około 0,15/h, a nie dowód „nigdy nie tworzy fałszywek”; dla granicy 0,001/h potrzeba około 3000 godzin przy zerze zdarzeń. To obliczenie planistyczne na podstawie modelu NIST; zależności i zmienność rzeczywistego RFI mogą wymagać ostrożniejszej analizy.[^24]

## Pierwsza kontrola wykonalności

Zamrożona eksperymentalna gałąź została sprawdzona na czterech wcześniej znanych nagraniach rozwojowych: 14936407, 14936415, 14936424 i 14936444. Odtworzono całe OGG; SHA256 wejściowego audio i dokładne zestawy ramek czterech bazowych wariantów były zgodne z wcześniejszym eksperymentem. Suma par obserwacja–PDU wzrosła z 78 do 112, czyli o 43,6%, a wspólna unia różnych PDU z 65 do 88: **23 dodatkowe różne pakiety, 6072 bajty bez FCS**. Nie usunięto żadnej ramki bazowej.[^6]

Wszystkie dodatkowe PDU mają dokładne potwierdzenie bajtowe we wcześniej pobranych wynikach archiwalnych, odczytanych dopiero przy ocenie zakończonego dekodowania. To potwierdza poprawność dodatkowego odzysku z tych plików, ale nie oznacza informacji nigdy wcześniej nieodebranej w sieci. Nie jest to również nowy niezależny holdout ani izolowany efekt samego MLSE: nowa gałąź zmienia także bank próbkowania i sposób użycia parametrów modelu.[^6]

Na osobnym syntetycznym interfejsie symbolowym MLSE odzyskał 64/64 różnych pakietów wobec 28/64 dla slicera. Pierwszy pełny test PCM nie uzyskał kotwic i nie pokrył transferu; jego wynik zachowano. Osobno oznaczony prostszy generator PCM sprawdził rzeczywiste uruchomienie transferu w czterech przypadkach, bez obcych ramek i bez dodatkowego zysku nad baseline. Wyniki te uzasadniają kontynuację, ale nie zastępują ablacji ani testu fałszywych akceptacji na dużej ekspozycji rzeczywistego szumu.[^6]

## Proponowany program prac

Pierwszy etap powinien odizolować przyczynę już zaobserwowanego zysku rozwojowego i sprawdzić jego powtarzalność. Potrzebne są opisany wariant slicera na identycznym banku, znane syntetyczne pakiety z innymi treściami kotwicy i celu oraz nowe nagrania do oceny poza użytymi czterema plikami. Wynik negatywny oznacza, że nie wykazano korzyści z danej konfiguracji. Trzeba oddzielnie sprawdzić dostępność kotwic, niedopasowanie fazy i normalizacji, zakres modelu oraz poprawność implementacji; sam brak zysku nie wyklucza ISI jako ograniczenia.

Drugi etap to diagnostyka reszt i synchronizacji. Jeśli błędy zależą od pozycji w pakiecie albo nagle rosną po utracie zegara, pierwszeństwo ma wygładzanie i ponowna akwizycja. Jeśli przy poprawnym zegarze widać powtarzalną korelację reszt, pierwszeństwo mają metryka AR i estymacja impulsu z większej liczby próbek. Dopiero potwierdzona zależność od kompresji uzasadnia osobny model kodeka.

Trzeci etap powinien uruchomić niezależny tor IQ na jednej dobrze opisanej rodzinie CPM/GMSK oraz jednej rodzinie z jawnym FEC. RML24 może służyć do testów modułów, a prawdziwe IQ z satelity do walidacji końcowej. Dane z kilku stacji mają sens dopiero po potwierdzeniu, że przechowują tę samą emisję z wystarczającą informacją do wyrównania.

Przed dużą kampanią należy zamrozić hipotezę główną, wersję programu, konfigurację, grupy danych, regułę akceptacji i sposób liczenia kosztu. Awans usprawnienia powinien wymagać efektu utrzymującego się poza pojedynczą stacją oraz kontroli błędnych akceptacji, nie jedynie dodatniej sumy ramek wymuszonej przez dołączenie kolejnej gałęzi. Szczegółowe progi wdrożeniowe trzeba zdefiniować przed końcowym testem z uwzględnieniem kosztu błędnego pakietu w stacji.

Najmocniejsza teza ewentualnego artykułu brzmi: **adaptacja modelu kanału i niepewności do rzeczywistego przelotu daje powtarzalny dodatkowy odzysk danych ponad dobrze skonfigurowane odbiorniki, przy jawnie zmierzonym koszcie i ryzyku fałszywych akceptacji**. Obecnie jest to kierunek do zweryfikowania. Literatura uzasadnia jego mechanizmy, ale nie dostarcza za projekt wyniku na jego korpusie.

## Źródła

[^1]: H. Tsou, B. Shah, R. Lee, S. Hinedi. [A Functional Description of the Buffered Telemetry Demodulator (BTD)](https://ntrs.nasa.gov/citations/19930015478). NASA/JPL, TDA Progress Report 42-112, 15.02.1993, s. 50–73. [Pełny tekst NASA](https://ntrs.nasa.gov/api/citations/19930015478/downloads/19930015478.pdf). Opis architektury, nie współczesny benchmark SatNOGS.
[^2]: N. Shlezinger, N. Farsad, Y. C. Eldar, A. J. Goldsmith. [ViterbiNet: A Deep Learning Based Viterbi Algorithm for Symbol Detection](https://www.weizmann.ac.il/math/yonina/sites/math.yonina/files/publications/viterbinet.pdf). IEEE Transactions on Wireless Communications 19(5), 3319–3331, 2020. DOI: 10.1109/TWC.2020.2972352; sekcja IV i Algorithm 2 opisują adaptację na podstawie zdekodowanych bloków.
[^3]: T. Raviv, S. Park, N. Shlezinger, O. Simeone, Y. C. Eldar, J. Kang. [Meta-ViterbiNet: Online Meta-Learned Viterbi Equalization for Non-Stationary Channels](https://arxiv.org/abs/2103.13483). Wersja autorska, 24.03.2021. Wyniki dotyczą badanych modeli kanału, nie archiwum OGG.
[^4]: M. Wei, C. Hu, D. Estévez, M. Tai, Y. Zhao, J. Huang, C. Bassa, T. J. Dijkema, X. Cao, F. Wang. [Design and flight results of the VHF/UHF communication system of Longjiang lunar microsatellites](https://www.nature.com/articles/s41467-020-17272-8). Nature Communications 11, 3425, 09.07.2020. DOI: 10.1038/s41467-020-17272-8. [Kod wskazany przez autorów](https://github.com/bg2bhc/gr-dslwp); dostępność całego wskazanego archiwum IQ nie została tutaj potwierdzona.
[^5]: Telemetry Yield. [Frozen SatNOGS CANVAS waveform holdout — measured results](/home/ubuntu/telemetry-yield/work/satnogs-holdout-20260908-v1/analysis/results.md) i [analysis.json](/home/ubuntu/telemetry-yield/work/satnogs-holdout-20260908-v1/analysis/analysis.json). Lokalny wynik kampanii zakończonej 09.09.2026; nie recenzowana publikacja.
[^6]: Telemetry Yield. [Adaptive sequence receiver v1 — development protocol](/home/ubuntu/telemetry-yield/docs/adaptive-sequence-receiver-v1.md) oraz [Adaptacyjny odbiornik sekwencyjny — implementacja i test rozwojowy](/home/ubuntu/telemetry-yield/reports/adaptive-sequence-development-20260910.md), 10.09.2026. Lokalna specyfikacja, wyniki i weryfikacja; szczegółowe manifesty i dane w linkowanym raporcie. Kontekst dodatkowy: dostarczone `spec-badawcza-v3.md`, wersja 3.0 z 29.08.2026, oraz `satnogs-observations-no-sx.json`, eksport Polyitan z 01.09.2026; dokumenty kontekstowe, nie dowód prawdziwości każdej zawartej w nich tezy.
[^7]: D. Estévez i współtwórcy gr-satellites. [gr_satellites command line tool](https://gr-satellites.readthedocs.io/en/latest/command_line.html), dokumentacja 5.10.0-git, odczyt 10.09.2026; sekcje „Real or IQ input”, „FSK demodulation and IQ input” i „Dump internal signals”. [Źródło demodulatora FSK](https://github.com/daniestevez/gr-satellites/blob/main/python/components/demodulators/fsk_demodulator.py). Bieżąca dokumentacja nie określa wersji historycznie użytej przez wszystkie stacje.
[^8]: A. Kavčić, J. M. F. Moura. [The Viterbi Algorithm and Markov Noise Memory](https://users.ece.cmu.edu/~moura/papers/kavcic_viterbinoisememory.pdf). IEEE Transactions on Information Theory 46(1), 291–301, styczeń 2000. DOI: 10.1109/18.817531. Szczególnie model szumu i sekcja VI.A o aproksymacji AR.
[^9]: S. Colonnese, G. Panci, P. Campisi, G. Scarano. [Fractionally spaced Bussgang equalization for GMSK modulated signals](https://iris.uniroma1.it/handle/11573/250226), 2003; rekord i abstrakt autorów w repozytorium Sapienza. M. Sirbu, J. Mannerkoski, Y. Zhang, V. Koivunen. [Feasibility of fractionally spaced blind equalization with GMSK modulated signals](https://research.aalto.fi/en/publications/feasibility-of-fractionally-spaced-blind-equalization-with-gmsk-m/), ECCTD 2001, s. 45–48; rekord bibliograficzny. Źródła ustalają wcześniejszy dorobek, nie ilościowy zysk dla naszego audio.
[^10]: I. Ali, U. Wasenmüller, N. Wehn. [A code-aided synchronization IP core for iterative channel decoders](https://ars.copernicus.org/articles/11/137/2013/ars-11-137-2013.html). Advances in Radio Science 11, 137–142, 04.07.2013. DOI: 10.5194/ars-11-137-2013.
[^11]: Xiph.Org. [Vorbis I specification](https://www.xiph.org/vorbis/doc/Vorbis_I_spec.html), specyfikacja techniczna, odczyt 10.09.2026; sekcje 1.2.4–1.3.2 oraz opis syntezy floor/residue i MDCT. Nie zawiera dowodu skuteczności codec-aware demodulacji SatNOGS.
[^12]: R. Luostari, D. Korpi, O. Tirkkonen, H. Holma. [Input-Correlated Supervision Noise Limits the Benefits of OTA Training for Learned Receivers](https://arxiv.org/html/2608.12918v1). Preprint arXiv:2608.12918v1, 13.08.2026, zgłoszony do IEEE; sekcje III–V, w szczególności ograniczenia V-F.
[^13]: A. Barbieri, G. Colavolpe. [Soft-Output Detection of CPM Signals Transmitted Over Channels Affected by Phase Noise](https://www.eurasip.org/Proceedings/Eusipco/Eusipco2006/papers/1568981456.pdf). EUSIPCO, Florencja, 4–8.09.2006. Detekcja SISO, dekompozycja Laurenta i modelowanie fazy.
[^14]: M. Messai, G. Colavolpe, K. Amis, F. Guilloud. [Robust Detection of Binary CPMs With Unknown Modulation Index](https://www.tlc.unipr.it/people/colavolpe/papers/j61.pdf). IEEE Communications Letters 19(3), 339–342, marzec 2015. DOI: 10.1109/LCOMM.2015.2390230.
[^15]: E. Hosseini, E. Perrins. [Timing, Carrier, and Frame Synchronization of Burst-Mode CPM](https://arxiv.org/pdf/1310.0757). IEEE Transactions on Communications 61(12), 5125–5138, grudzień 2013; odczytana wersja autorska arXiv v3 z 23.01.2014. DOI: 10.1109/TCOMM.2013.111613.130667.
[^16]: V. Singh, A. Prabhakara, D. Zhang, O. Yağan, S. Kumar. [A Community-Driven Approach to Democratize Access to Satellite Ground Stations](https://www.witechlab.com/papers/quasar-mobicom2021.pdf). ACM MobiCom, 25–29.10.2021. DOI: 10.1145/3447993.3448630; system Quasar.
[^17]: A. Lancho, A. Weiss, G. C. F. Lee, T. Jayashankar, B. G. Kurien, Y. Polyanskiy, G. W. Wornell. [RF Challenge: The Data-Driven Radio Frequency Signal Separation Challenge](https://arxiv.org/html/2409.08839v3). Wersja autorska v3 z 28.07.2025; IEEE Open Journal of the Communications Society, 2025. DOI: 10.1109/OJCOMS.2025.3556319. Sekcje IV.A–IV.B i przypis 14 dokumentują założenia testów oraz ujawniony przeciek konkursowy.
[^18]: Z. Liu, Y. Song, Q. Guo, P. Sun, K. Gong, Z. Wang. [Iterative Equalization of CPM With Unitary Approximate Message Passing](https://arxiv.org/pdf/2408.07385). Preprint arXiv:2408.07385, 14.08.2024. W tym przeglądzie nie potwierdzono późniejszej publikacji czasopiśmiennej.
[^19]: J. A. Snoap, J. A. Latshaw, D. C. Popescu, C. M. Spooner. [Robust Classification of Digitally Modulated Signals Using Capsule Networks and Cyclic Cumulant Features](https://arxiv.org/abs/2211.00232). IEEE MILCOM 2022; DOI: 10.1109/MILCOM55135.2022.10017507. Dotyczy klasyfikacji modulacji, nie odzysku ramek.
[^20]: Y. Zhang, B. Zang, H. Ji, L. Li, S. Li, L. Chen. [Cognitive Radio for Satellite TT & C System: A General Dataset Using Software-defined Radio](https://www.nature.com/articles/s41597-026-07182-7). Scientific Data 13, 860, 2026. DOI: 10.1038/s41597-026-07182-7; sekcje Dataset Generation, Data Record i etykiety. Pierwsza publikacja 10.04.2026; version of record 09.06.2026.
[^21]: Autorzy RML24 / yiwawa. [RML24-Cognitive-Radio-for-Satellite](https://github.com/yiwawa/RML24-Cognitive-Radio-for-Satellite), README, odczyt 10.09.2026. [Zmiana README z 16.04.2026](https://github.com/yiwawa/RML24-Cognitive-Radio-for-Satellite/commit/5efe95e6a8baf05ea626c8175a69de68cf8ccacf). Ostrzeżenie o 21 dostępnych modulacjach i rekomendacja PKL.
[^22]: Autorzy RML24. [RML24-Cognitive-Radio-for-Satellite — rekord Zenodo](https://zenodo.org/records/17800058), DOI: 10.5281/zenodo.17800058, data publikacji rekordu 03.12.2025. [Metadane API](https://zenodo.org/api/records/17800058), odczyt 10.09.2026: RML24_IQdata.zip 20 073 680 616 B; pkl format.zip 20 506 729 517 B. Weryfikacja metadanych, nie wnętrza archiwów.
[^23]: D. Shulman. [How Much Do RF Drone Benchmarks Overstate? A Controlled Study and Theory of Data Leakage in UAV Signal Identification](https://arxiv.org/abs/2607.01025). Preprint arXiv:2607.01025, 01.07.2026. Kontekst: klasyfikacja RF i podział po nagraniach, nie demodulator SatNOGS.
[^24]: NIST/SEMATECH. [e-Handbook of Statistical Methods: Constant repair rate (HPP/exponential) model](https://www.itl.nist.gov/div898/handbook/apr/section4/apr451.htm), sekcja 8.4.5.1, przypadek zero failures; odczyt 10.09.2026. Zastosowanie wzoru do fałszywych akceptacji jest obliczeniem własnym pod jawnym założeniem procesu Poissona.

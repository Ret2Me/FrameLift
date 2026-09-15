# Spójność etykiet i precyzja prognoz odbioru satelitarnego

## Podsumowanie

Najsilniejsza poprawa w nowym eksperymencie pochodzi z ujednolicenia znaczenia danych historycznych i ocenianego wyniku, a nie z większej sieci neuronowej. Historia demodulacji zawierała zarówno artefakty bez ręcznej oceny sygnału, jak i ocenione przeloty. Tymczasem prospektywny test warunkowej demodulacji wymaga niezależnie potwierdzonego sygnału. Te populacje mają bardzo różne udziały dodatnich wyników.

Wprowadzono osobny wariant, który dla tego konkretnego zadania korzysta wyłącznie z wcześniejszych przelotów z potwierdzonym sygnałem. Na 2031 rzeczywistych obserwacjach z sierpnia 2026 metoda wybrana na czerwcu osiągnęła 84,15% poprawności przy progu 50%, wobec 62,53% dla historycznego punktu odniesienia zachowującego szerokie etykiety. Precyzja pozytywnych wskazań wyniosła 70,31%, wobec 37,62%. Błąd prawdopodobieństwa Brier zmalał z 0,216711 do 0,106192.

Nie jest to pomiar 84-procentowej skuteczności całego systemu ani prawdopodobieństwo poprawnej odpowiedzi dla dowolnego satelity. Wynik dotyczy obecności artefaktu demodulacji w podzbiorze z niezależnie ocenionym sygnałem. Archiwum było wcześniej używane badawczo, więc wynik pozostaje rozwojowy, mimo zachowania kolejności czasowej w nowym eksperymencie.

Nowa metoda nie rozwiązuje problemu braku historii. W próbach całkowitego wyłączenia stacji lub satelity przewiduje przy progu 50% same niepowodzenia. Jej 75,73% poprawności w tych próbach jest wtedy wyłącznie wynikiem przewagi liczebnej klasy negatywnej. Z tego powodu udostępniono ją jako jawnie wybierany wariant badawczy, bez automatycznego zastąpienia modelu zarejestrowanej kampanii.

## Literatura i wybór kierunku

### Estymacja odbioru a układanie harmonogramu

Artykuł Ronena i Ben-Moshe'a rozdziela wybór przelotu od estymacji skuteczności stacji. Monte Carlo służy tam przybliżaniu wkładu stacji do wspólnego odbioru przez losowanie kolejności stacji; prawdopodobieństwa odbioru są wejściem opartym na statystykach. Taka symulacja nie naprawia sama jakości danych uczących predyktora.[^1]

L2D2 pokazuje wcześniejsze wykorzystanie historii, geometrii i pogody do modelowania łącza, w tym przenoszenia wiedzy pomiędzy stacjami. Pomiar jakości łącza w wyższych pasmach i ocena planowania na danych amatorskich nie są jednak identyczne z binarnym przewidywaniem artefaktów SatNOGS. Wyników SNR lub przepływności z tej pracy nie należy przeliczać na procent poprawności naszego klasyfikatora.[^2]

Wniosek projektowy: Monte Carlo i uczenie ze wzmocnieniem pozostają narzędziami do innych elementów zadania, ale nie są pierwszym wyborem do usuwania wykrytej niespójności populacji. Najpierw trzeba określić, co oznacza sukces i na jakiej podstawie wynik jest znany.

### Brak etykiety nie oznacza porażki

SatNOGS rozróżnia ręczną ocenę waterfallu, automatyczny status obserwacji i artefakty. „Unknown” nie jest pewnym brakiem sygnału, a „failed” może oznaczać problem wykonania lub brak poprawnie dostarczonych artefaktów. Istniejący normalizator słusznie nie zamieniał wszystkich tych przypadków na jedną klasę negatywną.[^3]

Badania Bekker, Robberechts i Davisa pokazują, że sposób wybierania dodatnich przykładów do oznaczenia jest istotną częścią problemu uczenia z danych dodatnich i nieoznaczonych. Nie można bez sprawdzenia zakładać, że dostępne etykiety stanowią losową próbę.[^4] Praca Coudraya i współautorów analizuje warunki uczenia przy selekcji zależnej od cech.[^5]

To uzasadnia sprawdzenie selekcji etykiet w naszym archiwum, ale nie dowodzi konkretnego mechanizmu błędu w SatNOGS. Nie wdrożono automatycznego modelu PU ani wag odwrotności prawdopodobieństwa oceny: nie mamy wiarygodnych wartości tego prawdopodobieństwa, a założenie losowej oceny nie zostało potwierdzone. Najpierw wykonano kontrolowane porównanie dwóch definicji historii przy niezmienionych przykładach oceny.

### Kalibracja procentów

Kull, Silva Filho i Flach proponują kalibrację beta, obejmującą również odwzorowanie pozostawiające poprawne prawdopodobieństwa bez zmian. Jest to niewielki model korekty procentów, a nie nowa duża sieć. W tej implementacji użyto monotonicznego odwzorowania oraz małej, stałej kary za odejście od braku korekty; ta kara jest naszą adaptacją, nie dokładnym odtworzeniem algorytmu autorów.[^6]

Kalibracja jest użyteczna, kiedy wynik dobrze porządkuje przykłady, lecz podaje błędne procenty. Sama monotoniczna korekta nie poprawia kolejności przelotów. Eksperyment potwierdził potrzebę porównania jej z prostym punktem odniesienia: poprawiała szeroką historię, ale nie pokonała najlepszego wariantu ze zgodną definicją etykiet.

Szerszy przegląd 15 źródeł, obejmujący adaptacyjne ważenie historii, modele drzewiaste, propagację, jonosferę i ograniczenia archiwalnej pogody, znajduje się w [raporcie poprzedniej rewizji](/home/ubuntu/telemetry-yield/reports/reception-revision-v1-20260910/deep-research.md). Nowy eksperyment uzupełnia tamte próby, a nie zastępuje ich inną interpretacją dawnych wyników.

## Wykryta różnica populacji

Źródłowy plik zawiera 47 039 obserwacji, 50 satelitów i 532 stacje. Nie każda obserwacja ma etykietę interesującego wyniku. Poniższe liczby opisują całe zamrożone archiwum, nie sam sierpniowy panel oceny.

| Historia warunkowego wyniku | Znane etykiety | Dodatnie | Udział dodatnich |
|---|---:|---:|---:|
| Dotychczasowa, również artefakty bez ręcznej oceny | 13 588 | 9163 | 67,43% |
| Tylko przeloty z ręcznie potwierdzonym sygnałem | 5570 | 1145 | 20,56% |

Różnicę stanowi 8018 dodatnich artefaktów bez ręcznej oceny sygnału. Nie są automatycznie błędnymi rekordami. Zostały zachowane w danych i w oryginalnej kampanii. Nowy predyktor po prostu nie używa ich jako przykładów zadania zdefiniowanego na ręcznie ocenionym podzbiorze.

Ten zabieg nie dowodzi, że nowy procent jest prawdziwym prawdopodobieństwem demodulacji w całej sieci. Formalnie odróżniamy wynik warunkowy przy sygnale od wyniku warunkowego przy sygnale i dostępnej niezależnej ocenie. Zrównanie ich wymaga dodatkowych założeń o procesie oceniania. Różnice pomiędzy satelitami, stacjami, dekoderami oraz aktywnością osób oceniających mogą nadal wpływać na rezultat.

Z tego powodu adapter używa jawnej nazwy `p_demodulation_artifact_given_independently_reviewed_signal`. Nie udostępnia tego wyniku pod nazwą bezwarunkowego sukcesu i nie mnoży go automatycznie przez dotychczasową prognozę sygnału. Nie jest też testem CRC ani licznikiem nowych, niepowtarzających się próbek.

## Implementacja

Zachowano istniejące, deterministyczne reguły historyczne. Oszacowanie dla pary satelita–stacja korzysta z ostatnich dziesięciu kwalifikowanych odbiorów. Przy małej liczbie przykładów jest przyciągane do ostrożniejszego oszacowania z historii satelity, stacji i całej dostępnej sieci. Osobny wariant doprecyzowuje wynik historią nadajnika.

Główne porównanie `reviewed_link` z `legacy_link` zachowuje identyczny algorytm i zmienia wyłącznie kwalifikację historii. Dodatkowo porównano prostszą regułę `reviewed_last10` oraz kalibrację beta każdej z trzech reguł. Kalibratory uczono tylko na marcowych prognozach wygenerowanych z wcześniejszej historii. Wybór spośród sześciu kandydatów oparto wyłącznie na czerwcowym Brier; sierpniowe wyniki wyliczono później bez ponownego dopasowania.

Model nie wymaga mocy nadajnika ani specyfikacji anteny. W tym eksperymencie nie używa w ogóle tych cech, pogody ani geometrii, aby oddzielić wpływ etykiet od pozostałych zmian. Nie jest to dowód, że te parametry są nieprzydatne. Rozszerzenie fizyczne wymaga osobnego porównania i wiarygodnego ustalenia czasu dostępności każdej cechy.

Implementacja badawcza: [conditional_target_alignment_v1.py](/home/ubuntu/telemetry-yield/work/operations/conditional_target_alignment_v1.py). Adapter do predykcji na rzeczywiście przechwyconej historii: [reviewed_history_candidate_v1.py](/home/ubuntu/telemetry-yield/work/operations/reviewed_history_candidate_v1.py). Adapter odrzuca próbę cofnięcia daty prognozy przed odczyt historii, duplikaty, sprzeczne etykiety oraz jeszcze niezakończone odbiory. Jawnie zgłasza brak historii satelity, stacji lub małą liczbę lokalnych wyników.

Nie zmieniono kodu ani modelu zamrożonej kampanii, wcześniej zapisanych prognoz, reguł etykietowania ani harmonogramów SatNOGS. Nowy adapter jest dostępny opt-in; nie został automatycznie włączony do codziennego planera.

## Protokół i wyniki

Protokół nowego eksperymentu zapisano 10 września 2026 o 17:28:40 UTC. Sierpień pozostaje późniejszym okresem względem kalibracji i wyboru w tym eksperymencie, ale był widziany podczas wcześniejszych badań projektu. Wszystkie przedstawione rezultaty są zatem rozwojowe. To odtworzenie historyczne na prawdziwych etykietach, nie sztucznie wylosowane sukcesy, i nie prognozy rzeczywiście zapisane przed sierpniowymi przelotami.

Informacja zwrotna staje się dostępna dopiero po końcu wcześniejszego przelotu i założonym opóźnieniu 24 lub 72 godzin. Pierwotnych dat publikacji ocen nie mamy. Testy opóźnienia pokazują wrażliwość na to założenie, ale nie zastępują dat przechwycenia wyników.

Panel sierpniowy obejmuje 2031 znanych wyników, 493 dodatnie i 1538 ujemnych, 24 satelity, 116 stacji oraz 31 dni. Każda metoda jest oceniana na dokładnie tych samych identyfikatorach. Sama strategia „zawsze nie” ma tu 75,73% poprawności, ale nie wskaże żadnego udanego odbioru.

| Metoda, wyprzedzenie 1 h | Poprawność przy 50% | Precyzja wskazań „tak” | Wykryta część wszystkich sukcesów | Brier — mniej lepiej |
|---|---:|---:|---:|---:|
| Historia łącza, dotychczasowe szerokie etykiety | 62,53% | 37,62% | 82,56% | 0,216711 |
| Ten sam algorytm, wyłącznie oceniony sygnał | 84,19% | 70,00% | 61,05% | 0,107028 |
| Ostatnie 10 odbiorów pary, oceniony sygnał; wybór na czerwcu | 84,15% | 70,31% | 60,04% | 0,106192 |
| Wybrana reguła z dodatkową kalibracją beta | 83,65% | 69,98% | 57,20% | 0,107507 |

Zmiana zmniejsza liczbę fałszywych obietnic sukcesu, ale przy tym samym progu pomija więcej rzeczywistych sukcesów. Przydatność dla planowania zależy więc od dostępnego czasu odbioru i kosztu pominięć. Nie wolno interpretować samego wzrostu precyzji jako automatycznego zwiększenia liczby próbek.

Główna różnica Brier, przy identycznym algorytmie, wyniosła −0,109683. Opisowy 95-procentowy przedział z losowania całych dni to [−0,124215; −0,094982]. Jest to wyraźny efekt w tym podzbiorze. Przedział nie uwzględnia całej wcześniejszej selekcji badawczej ani wielokrotnego sprawdzania wariantów i nie jest dowodem przewagi nad wszystkimi wcześniejszymi modelami projektu.

### Odległość do przelotu

| Wyprzedzenie i dostępność wcześniejszych etykiet | Poprawność wybranej reguły | Precyzja wskazań „tak” | Brier |
|---|---:|---:|---:|
| 1 h, wynik po 24 h | 84,15% | 70,31% | 0,106192 |
| 1 h, wynik po 72 h | 83,70% | 70,45% | 0,109936 |
| 7 dni, wynik po 24 h | 82,67% | 70,09% | 0,120102 |
| 30 dni, wynik po 24 h | 81,78% | 73,56% | 0,141446 |

We wszystkich czterech scenariuszach czerwiec wybierał tę samą prostą regułę. Wynik miesięczny jest słabszy: jego czułość wynosi tylko 38,95%. Każda prognoza ma tu indywidualny termin 30 dni przed przelotem; nie jest to test jednego globalnego planu sporządzonego na początku miesiąca. Nie sprawdzono w tej tabeli nowych TLE, zmian pogody, awarii ani konfliktów antenowych.

### Jednakowa liczba wybranych odbiorów

Dodatkowo, już po obejrzeniu wyników głównych, porównano najwyżej ocenione 406 obserwacji — około 20% tego samego panelu. Przy wyprzedzeniu godziny dotychczasowa historia wskazała 223 dodatnie, a wybrana nowa reguła 284, czyli precyzję 54,93% i 69,95%. Przy wyprzedzeniu 30 dni było to odpowiednio 215 i 245.

To pokazuje poprawę uporządkowania przykładów, a nie tylko zmianę liczby wskazań przy progu 50%. Jest jednak analizą dodatkową, bez uwzględnienia czasu trwania, konfliktów, priorytetów i blokerów. Nie jest dowodem 61 dodatkowych odbiorów w rzeczywiście wykonanym planie. Kalibracja beta nie zmieniła rankingu porównywanych reguł. Szczegóły: [equal-budget-exploratory.json](/home/ubuntu/telemetry-yield/reports/conditional-target-alignment-v1-20260910/equal-budget-exploratory.json).

### Nowe satelity i stacje

Wykonano pięć rozłącznych podziałów według identyfikatora satelity oraz oddzielnie według stacji. Wszystkie etykiety grup z ocenianego koszyka wyłączano z historii, także wcześniejsze i z tego samego miesiąca. W tych próbach oceniano stałe reguły bez kalibracji i wyboru metod na wyłączonych grupach.

Bez historii satelity Brier nowej reguły wynosi 0,167225, a bez historii stacji 0,195244. W drugim przypadku prosta częstość globalna dla poprawnie zdefiniowanej populacji jest lepsza: 0,188777. W obu próbach przy progu 50% nowa reguła nie wskazuje ani jednego sukcesu. Raportowanie tu samego „75,73% skuteczności” byłoby mylące.

Wynik ogranicza zakres wdrożenia. Dla całkowicie nowych obiektów potrzebny jest lepszy model przenoszenia wiedzy, wiarygodna fizyka łącza lub jawne oznaczanie braku wystarczających informacji. Nie należy po prostu podawać mniej pewnego procentu i twierdzić, że generalizacja została rozwiązana.

## Weryfikacja implementacji

Pierwszy zestaw nowego eksperymentu przeszedł 26 przypadków testowych. Łącznie z regresją istniejącego pilota i bramki dokładnych okien uzyskano 96 zaliczeń w 41,98 s. Następnie 30 przypadków obejmujących te same 26 oraz cztery nowe testy adaptera przeszło w 0,67 s; nie są to łącznie 126 różne testy.

Testy sprawdzają brak wpływu przyszłej i własnej etykiety na bieżącą prognozę, opóźnienia, duplikaty, nieznane wyniki, całkowite wyłączenie grup, zgodność adaptera przechwyconych danych z odtworzeniem, brak obowiązkowych cech RF i niemożność cofania czasu dostępności. Kontrole importów potwierdziły właściwe zamrożone zależności i niezmienność środowiska kampanii.

Niezależny skrypt, bez używania implementacji kursora historii, reguł ani miar z eksperymentu, odtworzył 900 prawdopodobieństw z prostego przeglądu danych oraz 294 wartości miar. Próbę 180 rekordów wybrano stałym ziarnem 42. Potwierdzono identyczne 2031 identyfikatorów sierpniowych we wszystkich scenariuszach. Ten audyt nie sprawdza niezależnie optymalizatora kalibracji ani procedury losowania przedziałów; zakres kontroli jest zapisany jawnie.

Dowody: [wyniki](/home/ubuntu/telemetry-yield/reports/conditional-target-alignment-v1-20260910/results.json), [protokół](/home/ubuntu/telemetry-yield/reports/conditional-target-alignment-v1-20260910/protocol.json), [audyt niezależny](/home/ubuntu/telemetry-yield/reports/conditional-target-alignment-v1-20260910/independent-audit.json) i [regresja 96 przypadków](/home/ubuntu/telemetry-yield/reports/conditional-target-alignment-v1-combined-tests-20260910.xml). Pliki źródłowe i wyniki eksperymentu mają zapisane sumy kontrolne; pełne prognozy zachowano.

## Wniosek wdrożeniowy i kolejny eksperyment

Najbardziej uzasadniony nowy kandydat to obecnie prosta historia z poprawnie określoną populacją, bez dodatkowej kalibracji beta. Nie ma podstaw do włączania kalibracji tylko dlatego, że jest metodą z literatury. Nie wykazano też w tej próbie poprawy przewidywania obecności sygnału — dotyczy ona oddzielnego, warunkowego wyniku artefaktów.

Następna ocena powinna zapisywać prognozy starego i nowego wariantu przed tymi samymi przyszłymi przelotami, a następnie oceniać je na niezależnie pozyskanych wynikach. Dwa już zarejestrowane przyszłe przeloty porównujące zamrożony model z trzema dotychczasowymi regułami mają oddzielny [pilot](/home/ubuntu/telemetry-yield/reports/paired-exact-job-v1-20260910/README.md); nie dopisano nowej metody do wcześniejszych prognoz z fikcyjną datą. Ich automatyczne porównanie jest ustawione na 11 września 2026 o 23:00 UTC i nie stanowi miesięcznej walidacji nowego kandydata.

Do estymacji dla całej sieci potrzebna jest próbka obejmująca także nieocenione i nieudane wykonania, z niezależnym sprawdzeniem obecności sygnału i ważności ramek. Dopiero taka próbka pozwoli sprawdzić, czy selekcja ręcznych ocen zmienia prawdopodobieństwa poza podzbiorem ocenionym. Równolegle należy utrzymać oddzielne testy nowych stacji i satelitów, zamiast ukrywać je w średniej z łatwiejszych, znanych par.

Wyniki są inżynieryjnie istotnym sygnałem poprawy jakości prognozy dla konkretnego celu. Nie ustanawiają nowej ogólnej metody uczenia, pierwszeństwa względem literatury ani zakończonego sukcesu publikacyjnego. Wartością badawczą może być rzetelne wykazanie wpływu procesu etykietowania i ocena przyszłego uzysku, jeśli dalsze doświadczenia to potwierdzą.

## Przypisy

[^1]: Rony Ronen, Boaz Ben-Moshe, [Maximizing Nanosatellite Throughput via Dynamic Scheduling and Distributed Ground Stations](https://pmc.ncbi.nlm.nih.gov/articles/PMC12737175/), Sensors 25(24):7538, 11 grudnia 2025, DOI 10.3390/s25247538, sekcja 4.1.
[^2]: Deepak Vasisht, Jayanth Shenoy, Ranveer Chandra, [L2D2: Low Latency Distributed Downlink for Low Earth Orbit Satellites](https://conferences.sigcomm.org/sigcomm/2021/files/papers/3452296.3472932.pdf), ACM SIGCOMM 2021, DOI 10.1145/3452296.3472932, sekcje 3.2, 4 i 6.1.
[^3]: SatNOGS, [Operation](https://wiki.satnogs.org/Operation), aktualizacja 28 sierpnia 2026, sekcje Observations ratings i Vetting artifacts; dostęp 10 września 2026.
[^4]: Jessa Bekker, Pieter Robberechts, Jesse Davis, [Beyond the Selected Completely At Random Assumption for Learning from Positive and Unlabeled Data](https://arxiv.org/html/1809.03207v4), ECML PKDD 2019, wersja z 28 czerwca 2019.
[^5]: Olivier Coudray i współautorzy, [Risk Bounds for Positive-Unlabeled Learning Under the Selected At Random Assumption](https://jmlr.org/papers/v24/22-067.html), Journal of Machine Learning Research 24(107):1–31, 2023.
[^6]: Meelis Kull, Telmo Silva Filho, Peter Flach, [Beta calibration: a well-founded and easily implemented improvement on logistic calibration for binary classifiers](https://proceedings.mlr.press/v54/kull17a.html), AISTATS 2017, PMLR 54:623–631.

## Sources

1. Ronen R., Ben-Moshe B. [Maximizing Nanosatellite Throughput via Dynamic Scheduling and Distributed Ground Stations](https://pmc.ncbi.nlm.nih.gov/articles/PMC12737175/). Sensors, 2025. Rozdzielenie estymacji prawdopodobieństwa od kooperacyjnego planowania i Monte Carlo.
2. Vasisht D., Shenoy J., Chandra R. [L2D2](https://conferences.sigcomm.org/sigcomm/2021/files/papers/3452296.3472932.pdf). ACM SIGCOMM, 2021. Historia, pogoda, geometria i przenoszenie wiedzy o łączach.
3. SatNOGS. [Operation](https://wiki.satnogs.org/Operation). 2026. Znaczenie ręcznych i automatycznych ocen obserwacji.
4. Bekker J., Robberechts P., Davis J. [Beyond the Selected Completely At Random Assumption](https://arxiv.org/html/1809.03207v4). ECML PKDD, 2019. Mechanizm selekcji oznaczonych dodatnich przykładów.
5. Coudray O. i współautorzy. [Risk Bounds for Positive-Unlabeled Learning Under the Selected At Random Assumption](https://jmlr.org/papers/v24/22-067.html). JMLR, 2023. Założenia i ograniczenia uczenia z częściowo oznaczonych danych.
6. Kull M., Silva Filho T., Flach P. [Beta calibration](https://proceedings.mlr.press/v54/kull17a.html). AISTATS, 2017. Parametryczna rodzina korekt prawdopodobieństwa.
7. Wewnętrzny eksperyment `conditional-target-alignment-v1`, [protokół i źródło danych](/home/ubuntu/telemetry-yield/reports/conditional-target-alignment-v1-20260910/protocol.json), [wyniki](/home/ubuntu/telemetry-yield/reports/conditional-target-alignment-v1-20260910/results.json), 10 września 2026. Wszystkie własne liczby w raporcie; niezależny audyt i pełne prognozy w tym samym katalogu.

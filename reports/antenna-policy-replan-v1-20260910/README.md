# Pełne przeliczenie planu z ograniczeniami kierunku anten

## Stan eksperymentu

Porównanie zostało zarejestrowane 10 września 2026 o 20:30:59 UTC, uruchomione o 20:32:49 UTC i zakończone o 20:46:38 UTC. Odtworzono pełną pulę 39 837 możliwości dla 50 satelitów i 12 stacji. Wszystkie 12 208 pierwotnych przydziałów zostało odtworzonych dokładnie, łącznie z prawdopodobieństwami i metadanymi. Wariant bazowy wybrał ten sam harmonogram i osiągnął tę samą wartość celu co plan źródłowy.

Łagodniejsze ograniczenie usuwa wszystkie wybrane okna całkowicie po odradzanej stronie przy spadku modelowej wartości celu o 0,06856%. Główny, ostrożny wariant usuwa także okna mieszane, kosztem 1,84582% tej wartości. Niezależna kontrola zakończona o 20:48:56 UTC potwierdziła geometrię, populację, raportowane liczby i spełnienie ograniczeń. Są to wyniki obliczeniowego porównania przyszłych planów, nie rezultaty rzeczywiście wykonanych odbiorów.

Badanie wynika z [audytu kierunków anten](/home/ubuntu/telemetry-yield/reports/antenna-direction-context-v1-20260910/README.md), który znalazł 89 zaplanowanych okien całkowicie po stronie nieba odradzanej przez operatorów stacji 1710 i 2029. Poprzedni audyt nie odrzucał przydziałów ani nie optymalizował nowego harmonogramu. Bieżące porównanie rozwiązało ponownie problem na pełnej puli, umożliwiając wybór okien nieobecnych w oryginalnym harmonogramie.

## Wyniki pełnego porównania

| Wariant | Dopuszczalne możliwości | Wybrane okna | Wybrane całkowicie przeciwne | Wybrane mieszane | Spadek modelowej wartości celu |
|---|---:|---:|---:|---:|---:|
| Dotychczasowy | 39 837 | 12 208 | 89 | 243 | — |
| Ostrożny, wariant główny | 34 976 | 12 435 | 0 | 0 | 1,84582% |
| Odrzucający tylko całkowicie przeciwne | 38 339 | 12 306 | 0 | 241 | 0,06856% |

Cała pula zawiera 1498 okien całkowicie po stronie przeciwnej, 1505 całkowicie po zalecanej i 3363 mieszane na dwóch ograniczonych stacjach. Pozostałe 33 471 możliwości dotyczą stacji bez zastosowanego w tym eksperymencie ograniczenia strony nieba. Wszystkie trzy rozwiązania obejmują 50 satelitów i 12 stacji; minimum faktycznie wybranych okien na satelitę wynosi odpowiednio 22, 23 i 22. Żadnego celu nie pominięto, aby uzyskać łatwiejszy wynik.

Ostrożna reguła zachowuje 11 787 oryginalnych przydziałów, usuwa 421 i dodaje 648 alternatyw. Łagodniejsza zachowuje 12 108, usuwa 100 i dodaje 198. Liczba usuniętych może być większa niż liczba bezpośrednio zabronionych wpisów w starym planie: ponowna globalna optymalizacja zmienia także inne przydziały w połączonych konfliktach.

Większa liczba okien nie oznacza większej liczby użytecznych danych. Średnia długość okna maleje z 491,12 s do 484,06 s w wariancie ostrożnym i 489,43 s w łagodniejszym. Modelowa oczekiwana liczba próbek, jeszcze przed uwzględnieniem ostrożności w celu optymalizacji, maleje odpowiednio o 1,60172% i 0,02957%. To nadal przewidywania tego samego modelu, a nie liczniki odebranych ramek.

Wszystkie trzy przebiegi zakończyły się statusem `optimal` i raportowaną luką optymalności 0,0. Nie jest to dowód, że model prawdopodobieństwa lub deklaracje anten są doskonałym opisem rzeczywistości. Pokazuje jedynie optimum dla zadanej puli, celu i ograniczeń w użytym solverze. Mały koszt łagodniejszej reguły jest obiecującym wynikiem inżynieryjnym, lecz ta reguła nadal dopuszcza przeloty mieszane i nie powinna być opisywana jako pełne rozwiązanie niejednoznaczności instrukcji operatora.

## Stałe wejścia i porównywane reguły

We wszystkich wariantach pozostają te same 50 satelitów, 12 stacji, dokładne zapisane TLE, częstotliwości, nadajniki, priorytety, model prawdopodobieństwa i historia. Horyzont obejmuje 13 września–14 października 2026. Sprawdzono zgodność odtworzonych danych wejściowych z sumą kontrolną konfiguracji użytej w oryginalnym planie. Różnica `last_seen` w czterech późniejszych odpowiedziach stacji dotyczyła wyłącznie znacznika aktywności; nie zmieniała współrzędnych, sprzętu ani opisu. Odtworzenie używa niezmiennych wcześniejszych odpowiedzi powiązanych z archiwum prognoz.

| Wariant | Dopuszczalność okien na stacjach z ograniczeniem wschód/zachód |
|---|---|
| `baseline` | Bez dodatkowego ograniczenia kierunku; odtworzenie dotychczasowej metody |
| `strict_hemisphere`, wariant główny | Co najmniej jedna próbka po stronie zalecanej i żadna po przeciwnej; okna mieszane i wyłącznie graniczne odrzucone |
| `reject_wholly_opposite`, analiza wrażliwości | Odrzucone tylko okna ze wszystkimi niegranicznymi próbkami po stronie przeciwnej |

Reguły ustalono przed przebiegiem porównawczym; nie będą wybierane na podstawie korzystniejszego wyniku. Opisy właścicieli nie definiują matematycznie przelotów mieszanych, dlatego obie reguły są jawnymi interpretacjami inżynieryjnymi, nie zatwierdzoną polityką operatora. Na stacjach bez jednoznacznego ograniczenia strony nieba ten eksperyment nie dodaje ograniczenia. Nie obejmuje jeszcze rozcinania okien na krótsze odcinki ani pełnego rozwiązania problemu ostrzeżenia „tylko wykrywanie sygnału” dla stacji 2830.

Geometria kierunkowa jest próbkowana co 2 sekundy. Brak próbki po stronie przeciwnej nie jest dowodem matematycznym ciągłego zachowania między próbkami. Nie zakłada się żadnej szerokości wiązki, tłumienia czy zerowego prawdopodobieństwa odbioru. Wpływ dodatkowej informacji izolowany jest przez zmianę dopuszczalności, bez zmiany procentów wyjściowych modelu.

## Odtworzenie i zabezpieczenia

Generator korzysta z istniejącego, zamrożonego `OpportunityBuilder` i tego samego propagatora. Wszystkie 50 satelitów jest przeliczanych dla całego miesiąca i wszystkich stacji. Ukończone fragmenty puli są zapisywane osobno z sumami kontrolnymi; można wznowić pracę bez ponownego przeliczania poprawnych fragmentów. Niekompletny zapis wymaga przeglądu, zamiast automatycznego nadpisania.

Przed porównaniem wymagane są 39 837 odtworzone możliwości, czyli tyle samo co w pierwotnym przebiegu. Każdy z 12 208 oryginalnych przydziałów musi być odtworzony dokładnie, łącznie z prawdopodobieństwami i metadanymi. Kontrola ta nie dowodzi niezależnie kompletności astronomicznej wszystkich możliwych przelotów, ale zabezpiecza porównanie przed przypadkową zmianą implementacji lub znanych wejść.

Trzy warianty używają tego samego optymalizatora SciPy/HiGHS, parametru ostrożności 0,25, limitu 120 sekund na rozwiązanie i wymagania potwierdzonego optimum. Zachowane są minima obserwacji wszystkich 50 satelitów, ograniczenia odbiorników, wyłączność transmisji i blokery. W tym konkretnym, niezmiennym pliku kontrolnym lista blokerów jest pusta; nie oznacza to, że rzeczywiste stacje nie mają konserwacji lub innych zobowiązań. Nie wolno przedstawiać tego przebiegu jako planu z kompletnym kalendarzem operatorów.

Każde rozwiązanie jest sprawdzane dodatkowym przeglądem zdarzeń czasowych, niezależnym od budowy ograniczeń optymalizatora. Kontrola obejmuje nakładanie okien, pojemność zasobów, wszystkie minima i maksima satelitów, blokery oraz sumę celu. Odtworzony wariant bazowy musi osiągnąć ten sam cel co oryginalny plan. Dodanie ograniczenia do tej samej puli nie może zwiększyć udowodnionego optimum tego niezmienionego celu.

Porównanie może wykazać, ile ograniczenia kosztują w jednostkach celu modelu i jakie przydziały zostają zastąpione. Nie może wykazać dodatkowych rzeczywiście odebranych próbek: oryginalne wartości `expected_unique_samples` są modelem uzysku, nie pomiarem nowych, niepowtarzających się ramek. Mniejszy wynik modelowego celu może towarzyszyć poprawie praktycznej zgodności z konfiguracją anten.

## Testy i uruchomienie

Zestaw przed uruchomieniem przeszedł 81 przypadków w 1,09 s: 21 dotyczących nowych reguł, rozwiązania i kontroli ograniczeń oraz 60 istniejących testów kierunku anten i niezależnego weryfikatora. Końcowy zestaw z dodatkowym niezależnym audytorem obejmuje 111 przypadków i przeszedł w 1,57 s. Te liczby się pokrywają i nie należy ich sumować. W pierwszym przebiegu fixture testu blokera przekazywał opis w pozycyjnym polu identyfikatora zasobu; poprawiono fixture na jawny argument `reason`. Nie zmieniono pod ten test definicji blokera ani kodu kampanii.

Przykład syntetyczny sprawdza ważną różnicę: usunięcie niedopuszczalnego przydziału ze starego planu pozostawia pusty harmonogram, natomiast ponowna optymalizacja pełnej puli wybiera poprawną alternatywę. Inne przypadki obejmują przylegające okna, konflikt stacji, konflikt satelity, zasób o pojemności dwóch odbiorów, niespełnione minimum satelity oraz odmowę przyjęcia nieudowodnionego optimum. Nie są to testy procentowej skuteczności odbioru.

Proces działał z limitem jednego rdzenia, 4 GiB pamięci i dwóch godzin, z obniżonym priorytetem. Wykorzystał około 13 min 30 s czasu procesora i maksymalnie 768,6 MiB pamięci według systemd. Dopuszczono wyłącznie lokalne gniazda UNIX; obliczenia nie potrzebowały internetu. Zadanie nie jest codziennym planowaniem operacyjnym i nie ma własnego timera. Nie zmieniło głównej bazy, rejestru kampanii ani harmonogramów SatNOGS. Nie pobrało nowych etykiet i nie uczyło modelu.

Niezależny [weryfikator wyników](/home/ubuntu/telemetry-yield/work/operations/verify_antenna_policy_replan_v1.py) sprawdził pełną wymaganą listę 111 plików, powiązania protokołu i wszystkich fragmentów puli, dokładne odtworzenie starych przydziałów oraz zgodność raportowanych miar. Ponownie obliczył 1 869 301 pozycji dla wszystkich 6366 okien z ograniczeniem kierunku. Nie korzysta z klasyfikatora strony, reguł dopuszczalności ani funkcji sprawdzania rozwiązania z wykonawcy eksperymentu. Konflikty weryfikuje kolejką najwcześniej kończących się okien. Współdzieli zamrożony SGP4 i parser współrzędnych; nie jest niezależnym pomiarem orbity, anteny ani osobnym dowodem optymalności solvera.

Po zakończeniu potwierdzono niezmienność głównego rejestru kampanii (`a7f5370ff6e844ef980c104d795f93c5bc468e059516ef83956d4002f7a57722`) i konfiguracji (`f07c157858b85c55bb25b62df50695bd93678a8b9fa16c80b717279903c53977`). Nowe warianty pozostają oddzielnymi, zapisanymi przed przelotami planami badawczymi. Nie podmieniono nimi zarejestrowanej metody głównej.

Dowody i pliki:

- [protokół](/home/ubuntu/telemetry-yield/work/antenna-policy-replan-v1/registered-20260910/protocol.json), [kontrola wejść](/home/ubuntu/telemetry-yield/work/antenna-policy-replan-v1/registered-20260910/preflight.json);
- [implementacja](/home/ubuntu/telemetry-yield/work/operations/antenna_policy_replan_v1.py), [testy](/home/ubuntu/telemetry-yield/work/operations/test_antenna_policy_replan_v1.py), [wynik 81 testów](/home/ubuntu/telemetry-yield/reports/antenna-policy-replan-v1-preflight-tests-20260910.xml);
- [sumy kodu, protokołu i usługi](/home/ubuntu/telemetry-yield/reports/antenna-policy-replan-v1-20260910/tooling.sha256), [usługa obliczeniowa](/home/ubuntu/.config/systemd/user/telemetry-yield-antenna-replan-v1.service).
- [wyniki trzech metod](/home/ubuntu/telemetry-yield/work/antenna-policy-replan-v1/registered-20260910/results.json), [potwierdzenie zakończenia](/home/ubuntu/telemetry-yield/work/antenna-policy-replan-v1/registered-20260910/completed.json), [niezależna weryfikacja](/home/ubuntu/telemetry-yield/reports/antenna-policy-replan-v1-20260910/independent-verification.json), [111 testów](/home/ubuntu/telemetry-yield/reports/antenna-policy-replan-v1-full-tests-20260910.xml).

## Dalsze warunki wyniku publikowalnego

To zakończone porównanie obliczeniowe, nie zakończona miesięczna kampania. Nadal trzeba ocenić przyszłe odbiory, jawnie rozdzielić wykrycie sygnału od artefaktu demodulacji i poprawnych ramek oraz zachować testy nowych satelitów i stacji. Nie ma w tym przebiegu wyniku „nowy model jest o X% trafniejszy”. Dane pogodowe pozostają przedmiotem oddzielnego, wcześniej zarejestrowanego eksperymentu.

W kolejnej wersji danych sprzętowych trzeba też odróżnić zmianę konfiguracji od aktualizacji znacznika aktywności. Dokładna suma całej odpowiedzi jest właściwym dowodem pochodzenia, ale sama zmiana `last_seen` nie zmienia kierunku anteny. Obecny rejestr interpretacji jest celowo związany z konkretnym zapisanym snapshotem; jego użycie do ciągłego planowania wymaga osobno przetestowanego porównania stabilnych pól sprzętowych. Nie należy po prostu wyłączyć kontroli wersji ani przenieść dzisiejszej konfiguracji do dawnych obserwacji.

## Nawigacja w kodzie

Graphify wskazał istniejące `OpportunityBuilder`, `DynamicObservationPlanner`, `MilpScheduler` i `Blocker`, co pozwoliło wykorzystać zamrożony generator i optymalizator. Graf jest wskazówką nawigacyjną, nie źródłem bieżącego stanu usług. Zapytanie miało budżet około 1800 tokenów wyjścia i zostało przycięte; nie wykonywano nowej ekstrakcji LLM, więc jej koszt wyniósł zero tokenów. Dokładny koszt całej sesji nie jest tu wyznaczany.

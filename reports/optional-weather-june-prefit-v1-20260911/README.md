# Trening odbioru z opcjonalną pogodą

## Wynik

Ukończono osobno zarejestrowany trening na całym czerwcowym zbiorze: **44 warianty i 88 zapisanych modeli**, po jednym modelu skalibrowanej historii i jednym modelu historii z pogodą dla każdego wariantu. Wszystkie 88 modeli rzeczywiście dopasowano, zamiast zastępować niedostateczne dane pustym modelem. Sierpień nie trafił do uczenia, doboru ustawień ani oceny w tym uruchomieniu. Zamrożony model głównej kampanii nie został zastąpiony.

Wstępny wynik **nie potwierdza korzyści z dodania pogody**: na czerwcowym zbiorze służącym do wyboru ustawień model pogodowy miał większy błąd prawdopodobieństwa od porównywalnej kalibracji historii w 40 z 44 wariantów, a mniejszy w czterech. Wszystkie cztery warianty bez wykluczania grup wypadały gorzej z pogodą. Nie są to 44 niezależne powtórzenia: podziały wykorzystują nakładające się obserwacje. Nie należy przeliczać tej proporcji na szansę, że pogoda jest użyteczna lub nieużyteczna.

To wynik doboru ustawień, **nie końcowa skuteczność na sierpniu ani prospektywne potwierdzenie**. Wszystkie modele i rozstrzygnięcia zostały zachowane. Nie dostrojono ich ponownie po obejrzeniu tych zestawień. Kolejny etap powinien ocenić ustalone warianty na pełnym sierpniu, a nie wymuszać dodatni wynik pogody.

## Dane i zakres uczenia

Źródłem pozostaje niezmieniona kohorta **47 039 obserwacji, 50 satelitów i 532 stacji**. Do tworzenia historycznego kontekstu przed lipcem kwalifikuje się 28 935 obserwacji. Czerwiec obejmuje 7134 obserwacje z 44 satelitów i 249 stacji. Nie jest więc prawdą, że wszystkie 50 satelitów wystąpiło w czerwcowym treningu. Pełna kohorta oraz późniejsza walidacja pozostają większe niż pojedynczy miesiąc uczenia.

| Zadanie i wyprzedzenie | Znane etykiety użyte w pełnym czerwcowym dopasowaniu | Dodatnie etykiety | Przypadki z co najmniej jedną wartością pogody |
|---|---:|---:|---:|
| Pojawienie się sygnału, 1 godzina | 2999 | 1876 | 2636 |
| Pojawienie się sygnału, 7 dni | 2999 | 1876 | 2320 |
| Demodulowane dane przy niezależnie potwierdzonym sygnale, 1 godzina | 1216 | 298 | 1093 |
| Demodulowane dane przy niezależnie potwierdzonym sygnale, 7 dni | 1216 | 298 | 983 |

Wiersze tabeli nie są rozłącznymi zbiorami. Tego samego odbioru nie wolno liczyć ponownie jako nowej obserwacji tylko dlatego, że ma drugie wyprzedzenie. Zadanie warunkowe ma osobną populację etykiet. Dodatni artefakt bez niezależnej oceny sygnału nie jest automatycznie uznawany za błędny; nie należy jednak do tego konkretnego zadania warunkowego.

W godzinowym zadaniu sygnału zachowano 141 obserwacji z udokumentowanym brakiem prognozy u dostawcy oraz 176 bez wcześniejszej kwalifikowanej lokalizacji stacji. Dodatkowo 46 obserwacji dopasowano do poprawnych pakietów, lecz wszystkie ich wartości pogody były puste. W godzinowym zadaniu warunkowym analogiczne liczby wynoszą 49, 56 i 18. Takie przypadki nie zostały usunięte ani zamienione w nieudany odbiór.

Prognozy pochodzą z audytowanego eksportu obejmującego wszystkie 141 117 par obserwacja–wyprzedzenie, z którego wybiera się cały czerwiec. W tej zapisanej wersji archiwum wszystkie czerwcowe zapytania miały rozstrzygnięcie: poprawne dane, udokumentowana niedostępność lub brak wcześniejszych metadanych lokalizacji. Sierpień pozostaje niekompletny. Kopia eksportu nie jest aktualizowana w miejscu wraz z działającym kolektorem.

## Metoda

Punktem wyjścia jest prawdopodobieństwo z wcześniejszej historii odbiorów, dopasowane do przewidywanego zadania. Model bez pogody koryguje jego skalę prostą regresją logistyczną. Model pogodowy dodaje temperaturę, wilgotność względną, ciśnienie powierzchniowe, prędkość wiatru i opad. Kary za wielkość współczynników ograniczają nadmierne odchodzenie od historii; współczynnik historii nie może być ujemny.

Średnie i skale cech są obliczane wyłącznie z części uczącej. Pojedyncze braki otrzymują średnią uczącą i osobną informację o braku. Cechy nigdy nieobserwowane w uczeniu pozostają wyłączone. Jeżeli nie ma żadnej użytecznej cechy pogodowej znanej podczas dopasowania, wariant pogodowy korzysta z porównywalnej kalibracji historii. Tę samą zasadę stosuje się podczas wyboru ustawień i późniejszej predykcji.

Wewnętrzny zbiór uczący obejmuje wyniki dostępne według założonego opóźnienia najpóźniej 5 czerwca o 00:00 UTC. Dobór ustawień wykorzystuje przeloty od 12 czerwca. Zachowuje to odstęp również dla prognoz wydawanych siedem dni wcześniej. Następnie wybrane ustawienia są dopasowywane na wszystkich kwalifikujących się czerwcowych etykietach. Porównuje się wcześniej ustalone trzy siły regularyzacji i liniowy lub kwadratowy wpływ cech pogodowych, z jawną regułą rozstrzygania remisów.

Każde z dwóch zadań i dwóch wyprzedzeń ma jeden wariant bez wykluczenia grup, pięć wariantów wykluczających grupy satelitów i pięć wykluczających grupy stacji. Podział grup to reszta z dzielenia identyfikatora przez pięć. Wszystkie etykiety wykluczonej grupy są pomijane w historii, dopasowaniu i doborze ustawień. To przygotowanie do późniejszej oceny nowych grup, a nie już uzyskany wynik tej oceny.

## Czerwcowy wynik wyboru ustawień

Poniższa miara opisuje błąd przypisanych prawdopodobieństw — im mniejsza, tym lepiej. Nie jest procentem poprawnych decyzji. Wszystkie wartości pochodzą z wewnętrznej walidacji czerwcowej, używanej do wyboru ustawień.

| Zadanie i wyprzedzenie, bez wykluczania grup | Kalibracja historii | Historia z pogodą | Różnica: pogoda minus historia |
|---|---:|---:|---:|
| Sygnał, 1 godzina | 0,139753 | 0,141565 | +0,001812 |
| Sygnał, 7 dni | 0,168120 | 0,168349 | +0,000229 |
| Dane przy potwierdzonym sygnale, 1 godzina | 0,104351 | 0,113242 | +0,008891 |
| Dane przy potwierdzonym sygnale, 7 dni | 0,145363 | 0,151856 | +0,006493 |

W tych czterech wariantach wybrano liniową korektę pogodową i najsilniejszą z badanych kar, 0,1. Nie dowodzi to, że pogoda nigdy nie może poprawić predykcji. Oznacza, że ta konkretna reprezentacja i procedura nie pokazały oczekiwanej przewagi w dostępnej czerwcowej walidacji. Nie ma podstaw do automatycznego promowania modelu pogodowego ani do przenoszenia wcześniejszych 84,15% z innego eksperymentu na ten wynik.

## Kontrole i odtwarzalność

Przed rejestracją przeszły **143 testy regresyjne w 310,20 s**, obejmujące trening, etykiety, historię i audyt pogody. Kontrola importów potwierdziła 30 modułów z właściwego zamrożonego środowiska, zero spoza niego oraz niezmienioną tożsamość źródeł. Wcześniejsza próba 49 testów została zachowana i nie jest dodatkowym rozłącznym pomiarem. Osobny kontroler przeszedł 11 testów w 4,24 s, z 28 modułami właściwego środowiska.

Eksperyment zarejestrowano 11 września 2026 o **01:44:22.066564 UTC**, rozpoczęto dopasowanie o **01:45:06.513172 UTC**, a potwierdzenie ukończenia zapisano o **01:57:23.503907 UTC**. Uruchomienie miało niski priorytet i ograniczoną liczbę wątków bibliotek numerycznych. Nie wykonywało zapytań sieciowych ani nie zlecało obserwacji.

Ponowny odczyt sprawdził całe, dokładne zestawienie 46 plików wynikowych: 44 zestawy modeli, inwentarz populacji i znacznik startu. Osobny kontroler odtworzył z danych źródłowych członkostwo wszystkich 44 wariantów, czas dostępności wyników oraz średnie i skale cech wszystkich 88 modeli. Dla 968 predykcji kontrolnych niezależny rachunek logistyczny zgadzał się z zapisanym modelem z maksymalną różnicą **2,22 × 10⁻¹⁶**. To sprawdzenie rachunku, nie 968 nowych odbiorów ani miara trafności.

Kontroler potwierdził, że wszystkie wyniki użyte do dopasowania poprzedzają najwcześniejsze sierpniowe terminy predykcji: 31 lipca 23:02:59 UTC dla godziny oraz 25 lipca 00:02:59 UTC dla siedmiu dni. Nie odtwarzał niezależnie optymalizatora ani całego kursora historii; dzieli też z badanym systemem czytnik integralności. Zakres niezależnej kontroli jest zatem ograniczony i jawny.

Skrót protokołu: `03ad2b7e1c9502bb58b770bc9f3f8c8c6c68084e17486366bda897de286d5626`. Skrót potwierdzenia ukończenia: `9d8a983ff19fda773ef5b1046c9a52c5ddc8ca7773c3a669f627269ebc7d466f`. Czytnik wymaga obu skrótów. Pliki kodu, testów, protokołu i wyników tego eksperymentu nie powinny być zmieniane w miejscu.

## Relacja z główną kampanią

Nie zmieniono kodu, protokołu ani bramki wcześniejszego eksperymentu wymagającego wszystkich 1132 poprawnych pakietów. Nowy trening jest innym, jawnym eksperymentem uwzględniającym udokumentowane braki; nie stanowi twierdzenia, że pierwotna bramka została spełniona. Nie podmieniono zamrożonego modelu ani konfiguracji miesięcznej kampanii.

W czasie tej pracy działający osobno główny planer dopisał czwarty wpis do swojego dziennika: dzienną przebudowę z 12 216 przydziałami, zatwierdzoną o 01:40:42.809069 UTC, przed startem treningu. Pierwsze trzy wpisy mają dokładnie wcześniejszy skrót `a7f5370ff6e844ef980c104d795f93c5bc468e059516ef83956d4002f7a57722`; nie zostały nadpisane. Nowy skrót całego dziennika to `559473652f478b51ca1d68d16544306296f18bc6fc11b89ec9cc7bab4efa7ca2`. Nie należy więc opisywać całego bieżącego dziennika jako niezmiennego tylko dlatego, że eksperyment nie ma do niego zapisu.

Potwierdzono aktywne harmonogramy miesięcznej oceny i osobnej kampanii nowych stacji, z pierwszymi uruchomieniami 13 września. Nie oznacza to wykonanych 30 dni, osiągniętych bramek jakości ani wykonania obserwacji przez operatorów. Główna kampania nadal działa w trybie shadow; bez danych o wykonaniu planu nie powstaje dowód rzeczywistego przyrostu odebranych próbek.

## Ograniczenia i następny etap

Historyczne prognozy pobrano po dawnych terminach predykcji. Założenia 12 godzin dostępności publikacji GFS, 24 godzin dostępności wcześniejszych metadanych stacji i 24 godzin opóźnienia etykiet pozostają założeniami. Brak prognozy udokumentowany dziś nie jest dowodem, że ten sam brak występował historycznie. Trening nie wykorzystuje sztucznej pogody na trzydzieści dni, późniejszej reanalizy, wymyślonego zysku anteny ani temperatury szumowej.

Następnym potrzebnym krokiem jest implementacja osobnego czytnika i wykonania oceny zapisanych modeli na **całym sierpniu dopiero po rozstrzygnięciu wszystkich pierwotnych zapytań pogodowych**. Musi on sprawdzić niezmienność czerwcowych cech i parametrów, zachować identyczne identyfikatory między metodami oraz nie ponawiać doboru ustawień na sierpniu. To wykonanie nie jest jeszcze wdrożone przez sam ten trening. Zbiór sierpniowy był wcześniej oglądany w innych pracach rozwojowych; również nowej analizy nie wolno przedstawiać jako świeżego niezależnego potwierdzenia.

Cel publikacyjny pozostaje otwarty: brakuje końcowego porównania pogody, zakończonej oceny przyszłych przelotów i 30-dniowej kampanii spełniającej wszystkie wymagane bramki. Dotychczasowe wykorzystanie danych antenowych w osobnym planerze pozostaje osobnym wynikiem; tutaj trenowano wyłącznie korektę pogodową względem historii.

## Artefakty

- [Zarejestrowany protokół](/home/ubuntu/telemetry-yield/work/optional-weather-june-prefit-v1/registered-20260911/protocol.json), [potwierdzenie ukończenia](/home/ubuntu/telemetry-yield/work/optional-weather-june-prefit-v1/registered-20260911/completed.json).
- [Zestawienie czerwcowych danych i wyników doboru ustawień](/home/ubuntu/telemetry-yield/reports/optional-weather-june-prefit-v1-20260911/training-summary.json), [szczegółowa kontrola 88 modeli](/home/ubuntu/telemetry-yield/reports/optional-weather-june-prefit-v1-20260911/independent-verification.json).
- [Implementacja treningu](/home/ubuntu/telemetry-yield/work/operations/optional_weather_june_prefit_v1.py), [testy treningu](/home/ubuntu/telemetry-yield/work/operations/test_optional_weather_june_prefit_v1.py), [kontroler](/home/ubuntu/telemetry-yield/work/operations/verify_optional_weather_prefit_v1.py).
- [143 testy regresyjne](/home/ubuntu/telemetry-yield/reports/optional-weather-june-prefit-v1-release-20260911.xml), [11 testów kontrolera](/home/ubuntu/telemetry-yield/reports/optional-weather-prefit-verifier-v1-20260911.xml).

Graphify pomogło odnaleźć powiązania historii, kohorty, pogody i walidacji. Zapytanie `weather history calibration validation training cohort leakage` odnalazło 381 węzłów; pokazano 46, a 335 ucięto limitem. Nowe skrypty operacyjne sprawdzono bezpośrednio, ponieważ nie są jeszcze opisane przez stary graf. Nie wykonano nowej ekstrakcji LLM; zero tokenów ekstrakcji nie oznacza zerowego kosztu pracy.

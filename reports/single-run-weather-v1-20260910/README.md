# Pogoda dostępna w chwili prognozowania

Wdrożono osobny moduł pobierania pojedynczych wydań modelu GFS oraz przygotowano zakres dla całej zamrożonej kohorty 50 satelitów. Pierwsza część danych została rzeczywiście pobrana i sprawdzona. Nie przeprowadzono jeszcze treningu z tymi nowymi cechami ani nie wykazano wzrostu skuteczności odbioru.

Pobieranie nie jest obecnie uruchomione w tle: dwie próby zakończyły się przekroczeniem czasu połączenia, a diagnostyczne żądanie HEAD do głównej ścieżki hosta Single Runs zwróciło 10 września 2026 o 19:04:43 UTC HTTP 503. To nie jest dowód trwałej awarii całego Open-Meteo ani przekroczenia limitu konta. Ukończone partie pozostają na dysku i są weryfikowane przed pominięciem przy wznowieniu.

## Dlaczego zmieniono źródło

Dotychczasowy Historical Forecast API tworzy ciągłą serię z początkowych fragmentów kolejnych uruchomień modelu. Taki szereg nie jest pełną prognozą dostępną w jednej wcześniejszej chwili decyzyjnej. Do porównywania wyprzedzeń potrzebne są pojedyncze wydania. [Dokumentacja Historical Forecast](https://open-meteo.com/en/docs/historical-forecast-api).

Single Runs API pozwala wskazać czas inicjalizacji modelu parametrem `run`. Ten czas nie jest czasem publikacji: obliczenia i udostępnianie wymagają dodatkowego czasu. Wybrano jawnie `gfs_global` oraz wyłącznie zakres archiwum od 2 kwietnia 2026. Nie zastąpiono wcześniejszych braków później przeliczonymi hindcastami ECMWF ani reanalizą. [Dokumentacja Single Runs](https://open-meteo.com/en/docs/single-runs-api).

W odtworzeniu historycznym przyjęto konserwatywne, ale nadal niezweryfikowane indywidualnie opóźnienie publikacji 12 godzin od inicjalizacji. Rzeczywista dostępność na serwerach może się różnić; nawet ukończenie pobierania i konwersji przez dostawcę nie jest tożsame z dostępnością przez API. [Dokumentacja czasów dostępności](https://open-meteo.com/en/docs/model-updates).

## Dwie różne osie czasu

Każda odpowiedź zachowuje czas uruchomienia modelu, początek żądania, rzeczywisty czas otrzymania danych oraz oddzielną, jawnie nazwaną hipotezę dawnej dostępności. Domyślny adapter operacyjny dopuszcza dane dopiero po faktycznym lokalnym otrzymaniu. Pobranie we wrześniu nie pozwala twierdzić, że system posiadał te dane w czerwcu.

Użycie wcześniejszego czasu w odtworzeniu wymaga jawnego wyboru `historical_assumption`. Wynik nadal ma flagę `historical_publication_time_verified=false`. To poprawia kontrolę nad pochodzeniem cech w porównaniu z serią zszywaną, ale nie zastępuje dowodu rzeczywistego przechwycenia prognozy przed dawną obserwacją.

Współrzędne stacji są pobierane z wcześniejszej obserwacji z poprawnie rozpoznanymi metadanymi klienta. Dla tych metadanych przyjęto osobno dostępność po końcu wcześniejszego przelotu plus 24 godziny. Aktualna lokalizacja ocenianej obserwacji nie służy do jej własnej prognozy. To model przenoszenia ostatniej znanej lokalizacji, nie dowód, że stacja od tego czasu się nie przemieściła ani że historyczne metadane rzeczywiście opublikowano po 24 godzinach.

## Zakres całej kohorty

Plan obejmuje wszystkie 47 039 rekordów, 50 satelitów i 532 stacje. Nie filtruje według obecności sygnału, artefaktów, powodzenia ani etykiet. Ten sam rekord może pojawić się w kilku horyzontach, więc poniższych liczebności nie należy sumować jako unikalnych obserwacji.

| Wyprzedzenie | Zaplanowano pobranie | Przed początkiem archiwum | Brak wcześniejszej zweryfikowanej lokalizacji | Poza obsługiwanym horyzontem |
|---|---:|---:|---:|---:|
| 1 godzina | 24 645 | 21 801 | 593 | 0 |
| 7 dni | 23 066 | 21 801 | 2172 | 0 |
| 30 dni | 0 | 0 | 0 | 47 039 |

Ostatni wiersz oznacza brak godzinowej prognozy z tego źródła na miesiąc naprzód, a nie brak pogody ani zerowe prawdopodobieństwo odbioru. Nie zastosowano cichej ekstrapolacji. Dla planowania miesięcznego nadal potrzebne są odpowiednio oznaczone informacje klimatyczne lub sezonowe oraz aktualizacja planu wraz ze zbliżaniem się przelotu.

Po połączeniu identycznych zapytań plan zawiera 19 389 par lokalizacja–wydanie modelu, pogrupowanych w 1132 partie po maksymalnie 20 lokalizacji. Pobierane są temperatura, wilgotność względna, ciśnienie przy powierzchni, wiatr i opad. Surowe jednostki są sprawdzane; dopiero adapter przelicza hPa na kPa. Wartości brakujące nie są zamieniane na zero. Zachowane są zarówno współrzędne żądane, jak i rzeczywistego punktu siatki modelu.

## Faktycznie pobrane dane

Audyt potwierdził 35 ukończonych partii, zawierających 586 par lokalizacja–wydanie modelu. Ich godziny odpowiadają 1388 unikalnym obserwacjom z siedmiodniowym wyprzedzeniem, obejmującym 34 satelity i 91 stacji. Dla wszystkich tych 1388 obserwacji istnieje komplet pięciu wartości pogodowych.

Na tym etapie nie pobrano jeszcze pasujących wydań dla horyzontu jednogodzinnego. Wynika to z ustalonej kolejności według czasu inicjalizacji modelu: rozpoczęto od starszych wydań majowych obsługujących prognozy czerwcowych przelotów z tygodniowym wyprzedzeniem. Nie wybrano korzystniejszych wyników odbioru ani lepiej rokujących satelitów.

Pozostaje 1097 partii. Ich liczba nie jest równa liczbie naliczanych jednostek API: dostawca rozróżnia żądania, liczbę zmiennych, okres i lokalizacje. Darmowy interfejs podaje m.in. limit 10 000 wywołań na dobę; dalsze pobieranie musi uwzględniać również pozostały ruch projektu, zamiast wielokrotnie uruchamiać cały pozostały zakres bez kontroli. Nie wykupiono subskrypcji ani nie użyto klucza dostępu. [Limity i zasady naliczania Open-Meteo](https://open-meteo.com/en/pricing).

Dokładne pliki, sumy kontrolne i pokrycie: [audyt akwizycji](/home/ubuntu/telemetry-yield/reports/single-run-weather-v1-20260910/acquisition-audit-20260910.json). Aktualny [plan](/home/ubuntu/telemetry-yield/work/single-run-weather-v1/cohort-plan-v1b-20260910.json) i [powiązanie kolekcji z planem](/home/ubuntu/telemetry-yield/work/single-run-weather-v1/collection-v1b-20260910/binding.json) pozwalają wznowić brakujące zapytania bez ponownego pobierania ukończonych partii. Progi czasowe nie są cofane podczas wznowienia.

## Testy i rzeczywista próba integracyjna

Pierwsza próba wielolokalizacyjna ujawniła, że API liczy kolejne godziny od inicjalizacji, a nie od północy. Walidator odrzucił taką odpowiedź; nie weszła do danych uczących. Poprawiono obliczanie potrzebnej długości prognozy i sprawdzanie osi czasu oraz dodano przypadki inicjalizacji 00, 06, 12 i 18 UTC. Pierwszy plan pozostawiono jako nieaktywny; obowiązuje plik z oznaczeniem `v1b` i nową sumą kontrolną implementacji.

Aktualny zestaw przeszedł 50 przypadków: 23 dotyczące nowego modułu i 27 istniejącego importera zweryfikowanych danych antenowych. Sprawdzono m.in. brak użycia własnej lub przyszłej lokalizacji, brak zależności planu od etykiet, nieobsługiwany horyzont miesięczny, daty i jednostki, zniekształcone odpowiedzi, brakujące wartości, integralność zapisu oraz wznowienie bez kolejnego HTTP dla ukończonej partii. Kontrola importów wykazała 30 modułów wyłącznie z właściwego zamrożonego środowiska i jego niezmienność.

Rzeczywisty audyt ponownie wyprowadza przetworzone wartości z surowych odpowiedzi, sprawdza powiązanie lokalizacji i wydania z planem, godziny docelowych obserwacji oraz zadeklarowane opóźnienia. Nie jest niezależnym pomiarem trafności meteorologicznej GFS ani walidacją skuteczności odbioru satelitów.

Kod: [moduł pobierania i adapter](/home/ubuntu/telemetry-yield/work/operations/single_run_weather_v1.py), [audyt kohorty](/home/ubuntu/telemetry-yield/work/operations/audit_single_run_weather_v1.py). Dowody: [50 testów](/home/ubuntu/telemetry-yield/reports/single-run-weather-v1-release-tests-20260910.xml), [kontrola środowiska](/home/ubuntu/telemetry-yield/reports/single-run-weather-v1-release-test-guard-20260910.json).

## Dane antenowe pozostają oddzielnym wymaganiem

Ponowny przegląd pierwotnego zbioru wykazał 46 471 rekordów z temperaturą z dotychczasowego archiwum pogody, 47 039 z historycznym Kp oraz 39 350 z ustawieniem wzmocnienia odbiornika. Jednocześnie w historycznych wierszach jest zero wartości typu anteny, zysku anteny i temperatury szumowej. Bieżące profile stacji przechwytywane dla przyszłej kampanii nie zostały cofnięte do tych dawnych wierszy.

Wzmocnienie RF odbiornika w dB nie jest zyskiem anteny w dBi. Istniejący importer potrafi przyjąć specyfikację z powiązanym źródłem, zakresem częstotliwości i potwierdzeniem operatora, ale samo przejście testów importera nie stanowi pomiaru sprzętu. W tej pracy nie dopisano brakujących wartości ani fikcyjnych potwierdzeń.

Główny model, zarejestrowany miesięczny eksperyment i harmonogramy SatNOGS pozostają bez zmian. Nie użyto tokenu SatNOGS, nie wysłano zleceń obserwacji i nie ingerowano w inne badania. Cel publikacyjny pozostaje nieukończony: potrzebne są pełniejsze dane, porównanie modeli na tych samych przykładach, wiarygodne informacje antenowe oraz przyszłe wyniki kampanii.

## Źródła

1. Open-Meteo. [Historical Forecast API](https://open-meteo.com/en/docs/historical-forecast-api). Dokumentacja odczytana 10 września 2026; znaczenie zszywanej serii pogodowej.
2. Open-Meteo. [Single Runs API](https://open-meteo.com/en/docs/single-runs-api). Dokumentacja odczytana 10 września 2026; inicjalizacja, dostępność archiwum i odrębne wydania.
3. Open-Meteo. [Model Updates and Data Availability](https://open-meteo.com/en/docs/model-updates). Dokumentacja odczytana 10 września 2026; rozróżnienie czasu inicjalizacji, przetworzenia i dostępności.
4. Open-Meteo. [Pricing](https://open-meteo.com/en/pricing). Odczyt 10 września 2026; limity i naliczanie API. Dane GFS pozyskano przez Open-Meteo; zachowano surowe odpowiedzi, a przetwarzanie obejmuje walidację, wybór godzin i konwersję ciśnienia.
5. Własny [audyt danych](/home/ubuntu/telemetry-yield/reports/single-run-weather-v1-20260910/acquisition-audit-20260910.json). Źródło wszystkich liczebności dotyczących wykonanej akwizycji; niezero zliczane pola źródłowe zapisano w `original_dataset_nonmissing_fields`, nieobecne liczniki oznaczają zero.

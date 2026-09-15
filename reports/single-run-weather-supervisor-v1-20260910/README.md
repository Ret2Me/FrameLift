# Automatyczne wznawianie archiwalnych prognoz GFS

Stan wdrożenia: 10 września 2026, około 19:32 UTC. To kontynuacja pozyskiwania danych, nie nowy eksperyment predykcyjny ani zmiana zarejestrowanej kampanii.

## Co rzeczywiście działa

Dodano oddzielny nadzorca `work/operations/single_run_weather_supervisor_v1.py` oraz usługę i timer użytkownika `telemetry-yield-single-run-weather-v1`. Timer jest włączony i czeka na pierwszą próbę o **19:35 UTC**; kolejne wybudzenia przypadają na minuty 05 i 35. Wybudzenie nie oznacza zapytania HTTP, jeśli trwa przerwa, wyczerpano limit, wykryto problem integralności lub zbiór jest kompletny.

Rzeczywiste uruchomienie kontrolne usługi zakończyło się poprawnie **19:30:41 UTC**, zwracając `cooldown`, `not_before=2026-09-10T19:35:00+00:00`, `http_started=false`. Po nim dziennik zawierał wyłącznie **36 wcześniejszych rezerwacji**, żadnej nowej próby HTTP. Usługa jednorazowa jest pomiędzy uruchomieniami nieaktywna; aktywny jest timer. `Linger=yes` pozwala menedżerowi użytkownika działać bez interaktywnego logowania. Zapisane limity przetrwają restart, o ile zachowany zostanie katalog projektu.

Na tę chwilę nadal mamy **35 z 1132 partii**, **586 par lokalizacja–uruchomienie prognozy**. Poprzedni audyt wykazał zgodność prognoz siedmiodniowych dla **1388 unikalnych obserwacji, 34 satelitów i 91 stacji**. Pokrycie prognoz jednogodzinnych w pobranej części wynosiło jeszcze zero, ponieważ kolejność kolekcji zaczyna się od wcześniejszych uruchomień dla prognoz siedmiodniowych. Nie jest to liczba nowych etykiet odbioru ani wynik treningu.

## Mechanizm

- Trwały dziennik rezerwuje szacowany koszt **przed** zapytaniem; nie zwraca go po błędzie. Obejmuje wszystkie kolejne uruchomienia tego nadzorcy. Wczytano 586 jednostek z 35 potwierdzeń i dodatkowy ostrożny zapas 128 jednostek na wcześniejsze nieudane próby oraz diagnostykę, razem 714. Zapas nie jest pomiarem rachunku dostawcy.
- Lokalne okna kroczące: 300 jednostek/min, 3000/h, 7500/dobę i 150000/30 dni; odstęp co najmniej 6,1 s; do 500 lokalizacji w jednym cyklu. Koszt uwzględnia liczbę lokalizacji, zmiennych i długość prognozy. Są to konserwatywne szacunki, nie niezależnie potwierdzone naliczanie dostawcy. Inny ruch Open-Meteo na serwerze nie jest kompletnie zinwentaryzowany, więc pozostawiony zapas nie gwarantuje zgodności całego serwera.
- Błędy połączenia oraz HTTP 408/429/500/502/503/504 powodują rosnące przerwy 30 min–6 h; dłuższy `Retry-After` ma pierwszeństwo. Dla 429 minimum wynosi godzinę. Początkowy licznik uwzględnia dwie wcześniejsze przerwane próby; kolejny błąd wydłuży przerwę do dwóch godzin. To nie jest omijanie ograniczeń przez zmianę hosta, IP lub uwierzytelnienia.
- Blokada pliku wyklucza dwa równoczesne cykle nadzorcy. Usługa ma limit 2 GiB pamięci, 1 rdzenia CPU, 64 zadań i 45 minut. Nie zarządza procesami innych badań.
- Oryginalny kolektor nadal sprawdza i pomija ukończone partie. Niepełne odpowiedzi pozostają na dysku; błąd formatu, sumy kontrolnej lub niedozwolony kod HTTP zatrzymuje automatyczne pobieranie do sprawdzenia. Nic nie jest automatycznie kasowane ani uznawane za poprawne przez rozluźnienie parsera.
- Po uzyskaniu wszystkich partii wymagany jest pełny audyt pozyskanych danych. Jego błąd nie może ustawić stanu ukończenia. Ukończenie pobierania samo w sobie nie oznacza ukończenia celu naukowego.

Lokalne ograniczenia pozostawiają zapas względem publicznych limitów darmowego API; zasady i zależność kosztu od rozmiaru zapytania opisuje [Open-Meteo Pricing](https://open-meteo.com/en/pricing). Nie zakupiono płatnego dostępu i nie użyto żadnego klucza API.

## Zachowana tożsamość danych i ograniczenia badawcze

Nadzorca dekoruje wyłącznie transport HTTP w trakcie zablokowanego cyklu i zawsze przywraca oryginalny opener. Nie zmienia kodu przypiętego kolektora, URL, parsera, zestawu zmiennych, jednostek, populacji docelowej ani planu `cohort-plan-v1b-20260910.json`. Zarejestrowany protokół i manifest kontrolują sumy kodu, testów, jednostek systemd, planu i wcześniejszego audytu. Zmiana któregokolwiek przypiętego pliku zatrzyma usługę przed HTTP.

`request_started_at` w kolektorze oznacza rozpoczęcie obsługi próby i może poprzedzać krótki odstęp wymuszony przez nadzorcę; zdarzenie `http_reserved` ma osobny czas bezpośrednio przed transportem. Dopiero `retrieved_at` jest rzeczywistym momentem uzyskania kompletnej odpowiedzi. Otrzymanie samych nagłówków nie jest potwierdzeniem kompletnego pobrania.

Wsteczne dołączenie prognoz nadal wymaga jawnych założeń o dostępności: 12 h od inicjalizacji prognozy i 24 h od wcześniejszej obserwacji dostarczającej lokalizację stacji. To nie dowodzi, że te pliki były lokalnie dostępne przed historyczną predykcją. Inicjalizacja modelu nie jest czasem publikacji; rozróżnia je [dokumentacja aktualizacji modeli](https://open-meteo.com/en/docs/model-updates). Prognozy miesięczne pozostają poza horyzontem tego źródła. Nie zastępujemy braków pogodą z przyszłości lub reanalizą pod nazwą prognozy.

Zamrożone dane, model i konfiguracja kampanii mają niezmienione SHA-256:

| Obiekt | SHA-256 |
|---|---|
| Kohorta 47039 obserwacji | `491d10f985b7e4de290ddfcc334487a6dfabcaf3f08f035c7f12959842fdf702` |
| Model wdrożony | `ffec494a356b51e67480c412c0266e46dea1d9fb4ffc0bf466903692098032c1` |
| Konfiguracja kampanii | `f07c157858b85c55bb25b62df50695bd93678a8b9fa16c80b717279903c53977` |

Nie przeprowadzono nowego treningu i **nie wykazano jeszcze, że dodatkowa pogoda poprawia precyzję**. Nie uzupełniono też brakujących pomiarów zysku anteny ani temperatury szumowej fikcyjnymi wartościami. Miesięczna walidacja prospektywna i uprawnienia do rzeczywistego sterowania stacjami pozostają osobnymi wymaganiami. Nie wykonywano żądań do SatNOGS ani modyfikacji zadań stacji.

## Testy i ślady

**65 testów przeszło w 0,89 s**: 42 przypadki nadzorcy i 23 istniejące przypadki kolektora. Obejmują wszystkie okna limitów, restart i brak ponownego pobierania poprawnej partii, rezerwację przed błędem sieci, `Retry-After`, blokadę równoległości, odmowę nieznanych lub uwierzytelnionych żądań, trwałe przerwy, zachowanie niepełnej odpowiedzi, kontrolę zmian kodu/danych oraz błędy końcowego audytu. Zapytania w testach były zastępowane lokalnymi odpowiedziami; nie testowały dostępności serwera pogodowego. Strażnik testów potwierdził 28 modułów projektu załadowanych wyłącznie z zamrożonego środowiska. Wcześniejsze wykonanie 63 testów jest osobnym, nieostatecznym śladem — wyników nie sumujemy.

- Kod: `work/operations/single_run_weather_supervisor_v1.py`.
- Protokół, stan i dziennik: `work/single-run-weather-v1/supervisor-v1/`.
- Wynik testów: `reports/single-run-weather-supervisor-v1-release-tests-20260910.xml`.
- Kontrola środowiska: `reports/single-run-weather-supervisor-v1-release-test-guard-20260910.json`.
- Manifest wdrożenia: `reports/single-run-weather-supervisor-v1-20260910/tooling.sha256` — 12 plików, sprawdzany przed usługą.
- Poprzedni audyt kolekcji: `reports/single-run-weather-v1-20260910/acquisition-audit-20260910.json`.

Graphify posłużył do odnalezienia istniejącej obsługi limitów i wznowień; nie traktowano starszego grafu jako dowodu bieżącego działania. Nowa implementacja jest oddzielna, ponieważ wcześniejsze reguły ruchu SatNOGS i innych kolekcji pogodowych nie określają limitów tego archiwum. Następny krok: odczytać rzeczywisty wynik próby zaplanowanej na 19:35, następnie po ukończeniu kolekcji porównać warianty z pogodą i bez niej na tych samych, poprawnie rozdzielonych czasowo przykładach.

# Dodatkowe unikalne obserwacje — eksperyment z 5 września 2026

Uruchomiono dwa niezależne procesy: trening porównawczy na już pobranym archiwum oraz dalsze pobieranie SatNOGS z automatycznym treningiem po jego zakończeniu. Żaden z nich nie zmienia zamrożonego modelu V4 ani jego miesięcznej kampanii.

## Dane i porównanie

- Archiwum lokalne: 19 740 dodatkowych unikalnych identyfikatorów poza surowym zbiorem V4; 15 859 obserwacji po normalizacji i wykluczeniach.
- To nie jest 15 859 dodatkowych przykładów do każdego zadania: część nie ma etykiety, a satelity/stacje testowe oraz późniejsze daty pozostają poza uczeniem.
- Pobieranie: 82 satelity, w tym 30 nieobecnych w objętym inwentaryzacją archiwum; 8 z nich zarezerwowano do testów. Brak obserwacji nie powoduje automatycznej zamiany satelity na łatwiejszy.
- Historyczne uzupełnienie: 1–7 marca i 1–7 czerwca 2026 dla satelitów spoza końcowej kohorty V4. Nowy test czasowy: 1–4 września, dla wszystkich wybranych obiektów.
- Dwa ramiona: ta sama procedura i kandydaci uczeni na dotychczasowych danych oraz na danych poszerzonych. Dobór modelu wyłącznie na tym samym czerwcowo-lipcowym panelu rozwojowym.
- Kandydaci: regresja logistyczna oraz modele drzewiaste z historią wcześniejszych odbiorów, z pogodą i bez niej; równolegle wariant ograniczający dominację najliczniejszych satelitów.
- Po pobraniu i pierwszym porównaniu zaplanowano uzupełnienie historycznej pogody z Open-Meteo i Kp z GFZ oraz osobny wariant treningu. Brak danych pozostaje jawny. Dane antenowe nie są odtwarzane z dzisiejszej konfiguracji.

## Ochrona oceny

Wrześniowy test nie jest używany do uczenia, strojenia, historii ani automatycznego obliczania wyników. Osiem nowych satelitów oraz dziesięć poprzednich satelitów testowych pozostaje poza uczeniem; około 20% stacji wydziela stała reguła oparta wyłącznie na ich identyfikatorach.

Sierpień był już oglądany: jego ponowne wyniki są diagnostyczne, nie potwierdzające. Wrześniowe obserwacje pobieramy po fakcie, więc nawet ich późniejsza niezależna ocena nie zastąpi miesięcznej kampanii prospektywnej. Wynik dekodowania pozostaje wskaźnikiem obecności pliku demodulacji, nie poprawnością CRC. Historia zakłada dostępność wcześniejszych etykiet po zakończeniu odbioru; opóźnienia w ocenianiu i przesyłaniu danych wymagają osobnego audytu przed wdrożeniem.

## Artefakty i śledzenie

- `protocol.json`: zamrożone zasady, podziały grup i sumy kontrolne źródeł.
- `snapshot-manifest.json`: wznawialny postęp pobierania, liczby odpowiedzi i pozostałe zapytania.
- `local/`, `expanded/`, `weather/`: wyniki kolejnych etapów; `complete.json` powstaje dopiero po zakończeniu etapu.
- `*-development.json`: postęp dopasowania kandydatów; nie jest wynikiem niezależnego testu.
- `*-training-ids.json`: identyfikatory faktycznie użyte w końcowym dopasowaniu danego modelu badawczego.
- `input-and-training-audit.json`: kontrola unikalności, sum kontrolnych i dostępnych rejestrów uczenia.
- Zapisane modele `.pkl` są wyłącznie lokalnymi artefaktami badawczymi. Nie należy wczytywać plików pickle z niezaufanych źródeł.

Procesy działają w usługach użytkownika `telemetry-yield-unique-local.service` i `telemetry-yield-unique-expand.service`. Pobieranie korzysta z istniejącego prywatnego tokenu i współdzielonego ograniczenia tempa; token nie trafia do raportów. Przejściowy błąd drugiej usługi powoduje ponowienie po 15 minutach z zachowaniem postępu.

Kontrole startowe: 9 testów przeszło, w tym odwrócenie etykiet testowych bez zmiany cech, wykluczenie nieznanych etykiet, rozdzielenie grup i dat oraz dopasowanie wszystkich pięciu kandydatów na kontrolnej próbce.

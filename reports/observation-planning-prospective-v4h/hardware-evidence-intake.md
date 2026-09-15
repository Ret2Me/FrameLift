# Weryfikacja dowodów antenowych

## Stan źródeł

Ponowna kontrola nie dostarczyła brakujących historycznych charakterystyk anten. Sprawdzono pierwszy rekord zamrożonej kohorty, obserwację 12296860 z 1 września 2025, bez dobierania przykładu według wyniku odbioru. Bieżąca odpowiedź API z 10 września 2026 nie zawiera pola historycznego profilu anteny. Strona HTML tej samej obserwacji ujawnia metadane klienta, w tym port `RX`, ale nie fizyczną charakterystykę anteny.

Kod modelu SatNOGS z wcześniej przypiętego zatwierdzenia `41e0ab7c1359b1dc18348b9f3bae647ba2b8bf52` przechowuje `station_antennas`. Szablon strony szczegółowej z tego zatwierdzenia nie wyświetla tego pola. Jego rzeczywistym plikiem jest `network/templates/includes/observation_detail.html`; szablon nadrzędny jedynie go dołącza. Pobrany ponownie kod modelu ma sumę identyczną z poprzednim audytem. To dowód dotyczący przypiętego kodu i jednej aktualnej odpowiedzi, nie twierdzenie, że nie istnieje żaden prywatny eksport lub archiwum operatora.

Źródła:

- [Odpowiedź API obserwacji 12296860](https://network.satnogs.org/api/observations/12296860/?format=json), pobrana 10 września 2026 o 13:00 UTC.
- [Strona tej obserwacji](https://network.satnogs.org/observations/12296860/), pobrana tego dnia o 12:57 UTC.
- [Przypięty model danych](https://gitlab.com/librespacefoundation/satnogs/satnogs-network/-/raw/41e0ab7c1359b1dc18348b9f3bae647ba2b8bf52/network/base/models.py).
- [Przypięty szablon szczegółów](https://gitlab.com/librespacefoundation/satnogs/satnogs-network/-/raw/41e0ab7c1359b1dc18348b9f3bae647ba2b8bf52/network/templates/includes/observation_detail.html).

Odpowiedzi źródłowe zachowano w `work/antenna-public-evidence-v2-20260910`. Nie zostały użyte do ponownego treningu ani dopisane do zamrożonej kohorty. Pełne metadane klienta mogą zawierać techniczne ścieżki urządzenia; nie są potrzebne do raportowania i nie należy ich automatycznie publikować.

## Import rzeczywiście dostarczonych dokumentów

Istniejący importer deklaracji sprzętu sprawdza, czy podano poprawnie sformatowaną sumę SHA-256, ale nie otwiera dokumentu, którego suma ma dotyczyć. Dodano zatem osobne, bardziej rygorystyczne narzędzie: `work/operations/import_verified_hardware.py`. Zamrożonego importera i programu kampanii nie zmieniono.

Nowy importer wymaga oryginalnej deklaracji sprzętu oraz indeksu dowodów. Dla każdej konfiguracji indeks musi określać numer stacji, identyfikator konfiguracji, względną ścieżkę pliku pomiarowego, jego sumę, datę pomiaru, datę przeglądu i tożsamość recenzującego operatora. Operator jawnie potwierdza uprawnienie do złożenia deklaracji oraz zgodność pomiarów i konfiguracji z dokumentem. Indeks zawiera dokładne wartości pomiarów, typ anteny i zakresy częstotliwości odpowiadające deklaracji.

Narzędzie weryfikuje istnienie i treść pliku przez SHA-256, zgodność konfiguracji i wartości, jednostki wynikające z nazw pól (`dBi`, `K`, `Hz`), chronologię pomiaru i przeglądu oraz ograniczenie ścieżek do dostarczonego katalogu. Zachowuje kopię dokumentu wraz z sumą. Nie uznaje ustawienia wzmocnienia RF za zysk anteny. Brakujące wartości nadal mogą pozostać puste.

**Ograniczenie:** zgodność dokumentu z rzeczywistym pomiarem jest potwierdzeniem operatora, a nie niezależnym pomiarem wykonanym przez ten program. Suma kontrolna dowodzi tożsamości pliku, nie prawdziwości opisanej w nim kalibracji.

Nowo otrzymane dane mają operacyjny czas dostępności nie wcześniejszy niż ich przyjęcie przez system. Wcześniejszy czas zadeklarowany przez operatora pozostaje w manifeście, ale nie pozwala automatycznie uzupełniać dawnych predykcji. Wiarygodnie datowane archiwum historyczne wymaga osobnego przeglądu i protokołu; ten importer go nie zastępuje.

Indeks używa schematu `station-hardware-evidence-index-v1` i tablicy `records`. Pola każdego wpisu:

| Pole | Znaczenie |
|---|---|
| `station_id`, `configuration_id` | Dokładnie ta sama konfiguracja co w deklaracji. |
| `evidence_relative_path`, `evidence_sha256` | Niepusty dokument znajdujący się w dostarczonym katalogu i jego rzeczywista suma. |
| `measurement_completed_at`, `reviewed_at` | Daty ze strefą czasową, nie późniejsze niż przyjęcie danych. |
| `reviewed_by` | Rzeczywisty recenzujący; nie wpis zastępczy. |
| `owner_or_authorized_operator` | Jawne potwierdzenie uprawnienia, wymagane `true`. |
| `configuration_and_measurements_verified` | Jawne potwierdzenie przeglądu, wymagane `true`. |
| `verified_measurements` | Dokładne podane wartości zysku anteny i/lub temperatury szumowej; pusty obiekt, jeśli żadnej nie podano. |
| `antenna_type`, `frequency_ranges_hz` | Typ i dokładny zakres stosowalności deklaracji. |

Uruchomienie po dostarczeniu prawdziwych plików:

```bash
rtk proxy /home/ubuntu/telemetry-yield/.venv/bin/python \
  /home/ubuntu/telemetry-yield/work/operations/import_verified_hardware.py \
  --declarations /absolute/path/to/declarations.json \
  --evidence-index /absolute/path/to/evidence-index.json \
  --evidence-root /absolute/path/to/documents \
  --output-dir /absolute/path/to/new-staging-directory
```

Wynik jest zgodny z istniejącym formatem archiwum sprzętu, ale zostaje tylko przygotowany do przeglądu. Nie jest automatycznie włączany do kampanii. Narzędzie nie nadpisuje istniejącego katalogu. Do chwili otrzymania rzeczywistych dokumentów liczba zaimportowanych pomiarów produkcyjnych wynosi zero; testowe pliki nie są dowodami antenowymi.

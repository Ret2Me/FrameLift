# Kalendarze operatorów a wykonanie miesięcznego planu

## Stan i wymagane dane

Główna kampania pozostaje w trybie `shadow`: zapisuje rekomendacje, ale nie zleca odbiorów. Plik `work/prospective-v4h/blockers.json` zawiera pustą listę. Nie ma w nim poświadczenia, że operatorzy wszystkich stacji potwierdzili brak konserwacji lub priorytetowych obserwacji. Nie można zatem uznać pustej listy za potwierdzoną dostępność przez miesiąc ani twierdzić, że obecny eksperyment mierzy zysk z wykonania harmonogramu.

Powstał [importer deklaracji operatorów](/home/ubuntu/telemetry-yield/work/operations/operator_calendar_intake_v1.py), [szablon do uzupełnienia](/home/ubuntu/telemetry-yield/work/operations/operator-calendar-declaration-v1.template.json) oraz kontrolowany odczyt zachowanych deklaracji. To przygotowana i przetestowana funkcja, **nie pozyskane kalendarze ani wykonana obserwacja**. Nie zmieniono plików zamrożonej kampanii, jej listy stacji, modelu ani istniejących prognoz. O wskazanie dostępnych stacji i kalendarzy zapytano właściciela zadania; odpowiedź jest nadal potrzebna.

## Znaczenie pól

Każda stacja w zamierzonym zakresie eksperymentu wymaga osobnej deklaracji. `station_id` to dodatni identyfikator SatNOGS, a `operator_reference` wskazuje źródło deklaracji, np. numer zgłoszenia od operatora. Ten tekst sam w sobie nie uwierzytelnia operatora i nie musi zawierać danych osobowych. `declared_at` określa deklarowany czas utworzenia kalendarza. Importer osobno utrwala rzeczywisty lokalny czas odczytu, którego nie zastępuje wcześniejszą datą podaną w pliku.

`coverage_start` i `coverage_end` muszą obejmować cały żądany okres planowania. `completeness` musi mieć jawną wartość `all_known_reservations`. Puste `blocks` są dopuszczalne wyłącznie z `no_known_reservations=true`; przy co najmniej jednej blokadzie pole musi być `false`. Brak kalendarza, niepełny zakres lub nieznana kompletność powodują odmowę importu, a nie przyjęcie wolnego czasu.

Każda pozycja `blocks` zawiera dokładnie:

| Pole | Znaczenie |
|---|---|
| `id` | Niepusty identyfikator rezerwacji, unikatowy wewnątrz stacji |
| `start`, `end` | Początek i koniec, ISO 8601 z jawną strefą; koniec musi być późniejszy |
| `kind` | `maintenance` albo `priority-reservation` |
| `reason` | Zwięzłe uzasadnienie blokady |

Przykład **wyłącznie ilustracyjnej** pozycji, nie rzeczywistej konserwacji:

```json
{
  "id": "przyklad-do-zastapienia",
  "start": "2026-09-15T10:00:00+02:00",
  "end": "2026-09-15T12:00:00+02:00",
  "kind": "maintenance",
  "reason": "Przykład: wymiana odbiornika; zastąpić rzeczywistą rezerwacją"
}
```

Czas jest normalizowany do UTC, więc ten przykład blokowałby 08:00–10:00 UTC. Przedziały są lewostronnie domknięte i prawostronnie otwarte: odbiór zaczynający się dokładnie po końcu blokady nie jest przez nią odrzucany. Nakładające się konserwacje i rezerwacje priorytetowe są zachowane; istniejący silnik planowania odejmuje ich sumę z okna przelotu.

Ten wariant blokuje całą stację, w tym wszystkie jej odbiorniki. Jest to konserwatywne ograniczenie: nie wdrożono deklarowania niezależnej dostępności zasobów bez potwierdzonej inwentaryzacji. Reguły cykliczne trzeba podać jako konkretne wystąpienia w okresie. Importer odrzuca nieobsługiwane pola takie jak `rrule`, zamiast je ignorować. Nie deklaruje obsługi ICS, stref nazwanych ani automatycznego rozwijania powtarzających się rezerwacji.

## Import i późniejsze użycie

Po uzyskaniu rzeczywistych deklaracji należy zapisać uzupełniony plik jako nowy dokument, wskazać dokładne identyfikatory stacji i przyszły zakres. Nie należy nadpisywać wcześniejszego importu. Wywołanie `operator_calendar_intake_v1.py` wymaga parametrów `--source`, `--destination`, `--station-ids`, `--start` i `--end`; identyfikatory i terminy muszą wynikać z rzeczywistego eksperymentu, nie z ilustracyjnych przykładów.

Powstają trzy pliki: oryginalne bajty `operator-input.json`, zgodny z istniejącym parserem `blockers.json` i `receipt.json`, który wiąże oba pliki sumami kontrolnymi oraz rzeczywistymi czasami. Konsument powinien użyć `read_intake(...)` z wcześniej zapisanym skrótem `expected_receipt_sha256`, zamierzonym zakresem stacji i horyzontem. Kontrola odtwarza blokady z oryginału, weryfikuje zakres, wersję kodu i chronologię. Samo wczytanie `blockers.json` pomija te kontrole pochodzenia.

W nowym doświadczeniu planer powinien związać oba pliki i potwierdzenie odbioru z własnymi artefaktami wejściowymi. Nie wolno dopisywać takiego kalendarza do dawnych prognoz z fikcyjną datą dostępności. Ten etap nie podłącza nowych deklaracji do obecnej zamrożonej kampanii i nie rejestruje jeszcze nowego eksperymentu wykonawczego.

Każde potwierdzenie importu zachowuje `operator_identity_verified=false`, `reservation_truth_verified=false` i `execution_authorized=false`. Przekazanie kalendarza nie jest uprawnieniem do zlecania zadań na cudzej stacji. Weryfikacja dostępu, bieżącego stanu stacji oraz sposób porównania wykonanych harmonogramów wymagają osobnych, rzeczywistych danych i decyzji. Importer nie wywołuje sieci ani `/jobs/`.

## Testy i ograniczenia dowodów

Końcowy przebieg zaliczył **101 testów w 17,19 s**: 38 przypadków importu/odczytu, 35 istniejących przypadków dokładnej walidacji obserwacji i 28 przypadków podstawowego planowania. Wszystkie 38 załadowanych modułów projektu pochodziły z właściwego zamrożonego środowiska; jego tożsamość pozostała niezmieniona.

Sprawdzono brakujące i powtórzone stacje, jawne puste kalendarze, zakres miesiąca, strefy czasowe, daty przyszłych deklaracji, odrzucone powtórzenia kluczy JSON, brak cichego pomijania reguł, nakładające się blokady, nadpisywanie wyników, zmienione pliki, dowiązania i próbę nadania uprawnień samym potwierdzeniem. Test integracyjny wykorzystuje rzeczywisty zamrożony parser i funkcję wycinania zablokowanych fragmentów przelotu. Dane operatora w testach są syntetyczne.

Pierwszy przebieg wykrył pięć błędów wynikających z niezgodności typu czasu przy wywołaniu istniejącego pomocnika. Poprawiono lokalny adapter czasu, nie zamrożony kod ani znaczenie testu. Zachowano nieudany przebieg, a poprawiony wariant i rozszerzone kontrole przeszły przed sporządzeniem tego raportu.

Dowody: [101 testów](/home/ubuntu/telemetry-yield/reports/operator-calendar-intake-v1-release-tests-20260910.xml), [kontrola importów i sum źródeł](/home/ubuntu/telemetry-yield/reports/operator-calendar-intake-v1-release-tests-20260910-runtime.json), [testy implementacji](/home/ubuntu/telemetry-yield/work/operations/test_operator_calendar_intake_v1.py).

Graphify wskazał `Blocker` w `planning/models.py`, `run_shadow_once()` oraz deklarowane ograniczenia w `prospective.py`. Analiza tych źródeł pozwoliła wykorzystać istniejący format blokad bez naruszania zamrożonego środowiska. Użyto istniejącego grafu i słów `calendar operator blocker execution shadow prospective readiness`; zapytanie miało budżet około 1500 tokenów i pokazało 42 z 551 znalezionych węzłów. Nowej ekstrakcji LLM nie wykonywano; zero tokenów ekstrakcji nie oznacza zerowego kosztu całej pracy.

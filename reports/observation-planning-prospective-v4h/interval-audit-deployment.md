# Kontrola przedziałów odbioru — stan wdrożenia

10 września 2026 uruchomiono dodatkową usługę `telemetry-yield-prospective-v4h-interval-audit.service` oraz aktywny zegar uruchamiający ją po starcie systemu i następnie co godzinę. Pierwsze wykonanie usługi rozpoczęło się o 14:09:25 UTC i zakończyło poprawnie. Sprawdzony sumą wskaźnik wyniku znajduje się w `interval-quality-latest.json`, a pełne migawki w `interval-audits`.

Narzędzie, jego 14 testów, kontrola faktycznych ścieżek importu, definicje usługi i zegara oraz opis metodologiczny są przypięte w `interval-audit-tooling.sha256`. Weryfikacja sum jest warunkiem uruchomienia usługi.

Kontrola jest wyłącznie odczytowa wobec rejestracji i dziennika. Przy niezmienionych licznikach nie wypisuje kolejnego komunikatu. Obecnie nie ma jeszcze wyników kampanii: zero nie jest zaliczane jako przejście kontroli zgodności przedziałów.

Ta usługa **diagnozuje**, ale nie naprawia dopasowania różnych okien czasowych i nie zastępuje głównych bramek jakości. Dalsza decyzja o regule dopasowania lub o prognozowaniu dokładnych, z góry znanych okien obserwacji pozostaje odrębnym krokiem. Nie nadano zgody na publikację i nie zgłoszono ukończenia celu.

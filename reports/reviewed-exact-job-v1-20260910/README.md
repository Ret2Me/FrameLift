# Prospektywny pilot nowej historii warunkowego odbioru

Dwie prognozy nowego wariantu zapisano 10 września 2026 o 17:53:41 UTC, przed przelotami i z ponad godzinnym wyprzedzeniem. Są to te same obserwacje co w dotychczasowym pilocie: 14967355, okno 19:03:25–19:08:34 UTC, i 14967447, okno 22:19:45–22:25:04 UTC; stacja 766, NORAD 68635.

Nie zmieniono wcześniej zapisanych prognoz modelu ani reguł historycznych. Nowy wariant ma osobny protokół, plik prognoz i rzeczywisty znacznik zakończenia zapisu. Informacje wejściowe są ograniczone do tego samego wcześniejszego momentu przechwycenia 14:32:45.037784 UTC. Ten wcześniejszy czas nie jest przedstawiany jako czas powstania nowej prognozy.

Metoda `reviewed_last10` korzysta z 5570 wcześniejszych wyników z niezależnie potwierdzonym sygnałem, w tym pięciu dla tej pary satelita–stacja. Zwróciła około 4,67% dla obu przelotów. Zamrożony model podał około 64,6% i 64,4%, a szeroka reguła historii łącza około 98,6%. Są to prognozy porównawcze, nie pomiar skuteczności. Nowy procent ma węższe znaczenie: artefakt demodulacji w podzbiorze z niezależnie ocenionym sygnałem; nie jest bezwarunkową szansą odbioru satelity.

Tak duża rozbieżność jest powodem do rzeczywistego sprawdzenia, nie do uznania nowej metody za poprawną. Wszystkie warianty zostaną ocenione na tych samych dostępnych etykietach. Brak ręcznej oceny nie będzie porażką i nie zostanie automatycznie zastąpiony oceną sygnału na podstawie artefaktu.

Pięć testów nowego pilota przeszło, w tym wykonanie na rzeczywistych, niezmienionych danych wejściowych, blokada dodatkowego HTTP, kontrola czasu, niezmienność poprzednich prognoz i wspólna populacja oceny. Łączny zestaw z kontrolerem miesięcznym, predyktorem i adapterem dał 47 zaliczeń. Rzeczywisty wczesny [raport](/home/ubuntu/telemetry-yield/reports/reviewed-exact-job-v1-20260910/early-score/results.json) ma zero dopasowanych wyników i puste miary skuteczności.

Zainstalowano jednorazowy harmonogram oceny na 11 września o 23:15 UTC. Pobranie etykiet wykonuje wcześniej istniejący pilot o 22:30 UTC; nowy moduł używa jego surowych odpowiedzi i ponownie sprawdza ich zgodność, bez dodatkowego HTTP. Jeśli nadrzędne pobranie nie zakończy się poprawnie lub dane zostaną zmienione, usługa zgłosi błąd zamiast wymyślać wyniki.

Ten pilot nie wchodzi do miesięcznej próby rozpoczynającej się 13 września. Nie wysłano zleceń obserwacji i nie zmieniono głównego modelu ani planera.

Artefakty: [prognozy](/home/ubuntu/telemetry-yield/work/reviewed-exact-job-v1/pilot-20260910/predictions.json), [zapis czasu](/home/ubuntu/telemetry-yield/work/reviewed-exact-job-v1/pilot-20260910/commit.json), [protokół](/home/ubuntu/telemetry-yield/work/reviewed-exact-job-v1/pilot-20260910/protocol.json), [miesięczne porównanie](/home/ubuntu/telemetry-yield/reports/monthly-exact-comparison-v1-20260910/README.md).

# Dopracowanie odbiornika OGG — rozwój i mała walidacja

Nowa wersja przyspiesza sprawdzanie AX.25 i zachowuje dotychczasowe hipotezy zegara, dodając niewielki bank zróżnicowanych faz i błędów prędkości.

## Próbki rozwojowe — wyniki były znane

| Obserwacja | Archiwum | gr-satellites / to samo OGG | Szybki stary bank | Rozszerzony bank | Nowe wyłącznie nasze wobec obu |
|---|---:|---:|---:|---:|---:|
| 14366383 | 6 | 2 | 5 | 6 | 0 |
| 14115025 | 10 | 5 | 10 | 10 | 0 |

## Dwie inne obserwacje — wybrane przed uruchomieniem kandydata

| Obserwacja | Archiwum | gr-satellites / to samo OGG | Szybki stary bank | Rozszerzony bank | Nowe wyłącznie nasze wobec obu |
|---|---:|---:|---:|---:|---:|
| 14956101 | 1 | 0 | 0 | 0 | 0 |
| 14936397 | 86 | 66 | 85 | 85 | 0 |

## Interpretacja

Liczniki oznaczają unikalne pełne PDU w obrębie obserwacji, bez FCS i bez doliczania powtórek. Wszystkie natywne FCS sprawdzono ponownie bitowo.
Nie łączymy próbek rozwojowych i walidacyjnych w pozorną skuteczność ogólną. Wszystkie cztery obserwacje dotyczą tej samej misji.
Zera, brakujące ramki i wyniki ujemne pozostają w raporcie. Treść ramek archiwalnych nie była podawana dekoderowi.
Test wielkoskalowy nie został uruchomiony. Krótkie kontrole szumu nie są kwalifikacją fałszywych alarmów nowej ścieżki audio.
Pełne bajty, bilanse, czasy i ograniczenia znajdują się w JSON obok tego dokumentu.

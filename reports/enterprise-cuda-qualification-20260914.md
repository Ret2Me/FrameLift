# CPU/CUDA — wynik kwalifikacji, 14 września 2026

Wdrożono **wersję kwalifikacyjną**, nie ogłoszono pełnej gotowości enterprise
ani publikacyjnej. CPU pozostaje niezależnym, domyślnym trybem. CUDA jest
opcjonalnym feature i obecnie obejmuje filtry FIR audio/IQ oraz filtry
dopasowane BPSK/QPSK/OQPSK. Synchronizacja, MLSE, FEC i ramkowanie pozostają
na CPU. Nie przedstawiamy tego jako nowego algorytmu demodulacji.

## Co dodano

- `--compute cpu|cuda`, osobna liczba wątków FIR, wybór urządzenia CUDA,
  współbieżne strumienie i ograniczony, wielokrotnie używany bufor urządzenia.
- CUDA nie jest wymagana przy kompilacji/uruchomieniu wersji CPU. Żądanie
  CUDA bez działającego backendu kończy się błędem, bez ukrytego fallbacku.
- Tożsamość backendu w checkpointach progresywnych, generycznych, kampanii
  i ścieżki clipping. Pierwsze użycie zamraża politykę obliczeń procesu.
- SIGINT/SIGTERM: kontrolowane zatrzymanie, sprzątanie własnego procesu
  roboczego, potwierdzenie przerwania i zachowanie zatwierdzonych zadań.
- `qualify-compute`, `audit-compute-pair`, `benchmark-compute-pair`:
  kontrola bitów, pełnych zadań/modeli/ramek, sumy kontrolne i jawne niepowodzenia.

## Zweryfikowane wyniki

| Sprawdzenie | Wynik |
|---|---|
| Rdzeń Rust, końcowa seria CPU | 443 przeszły, 0 błędów, 6 jawnie pominiętych |
| Testy CLI, w tym przerwanie/wznowienie | 19/19 |
| Niezależny audytor kontrolek | 34 przeszły, 0 błędów, 2 pominięte |
| Testy warstwy hosta w kompilacji CUDA | 10 przeszło, 2 testy wymagające dodatkowego środowiska pominięte |
| Osobna kompilacja kernela przez NVIDIA NVRTC | przeszła; to nie wykonanie na GPU |
| Zgodność filtrów z niezależną pętlą referencyjną | 195/195 przypadków, w tym 108 fragmentów całego rzeczywistego pliku |
| Pełny odbiornik: obserwacja 14967393, CPU FIR 1 vs 4 wątki | 20 ramek vs 20 ramek; 0 utraconych i 0 dodanych |
| Szczegółowy audyt tych sesji | po 1372 ukończone zadania; 0 różnic w modelach, próbach i ramkach |

Jedna wcześniejsza seria została zakłócona równoległą przebudową pliku
wykonywalnego, którego niezmienność testy celowo sprawdzają. Nie rozluźniono
kontroli hashy. Po rozdzieleniu budowania i testowania pełna końcowa seria
przeszła. Powyższa tabela podaje wyniki końcowego uruchomienia.

Na rzeczywistym nagraniu czas całego procesu wyniósł **127,44 s vs 128,72 s**.
To jedna para na współdzielonej VM: **nie wykazano przyspieszenia całego
odbiornika**. Nie przenosimy przyspieszenia pojedynczego filtra na cały pipeline.
Domyślnie zachowano dotychczasową równoległość okien i jeden wątek FIR.

Nagranie jest wcześniej znanym materiałem rozwojowym, a nie niezależnym
holdoutem. Te 20 ramek porównano między konfiguracjami naszego programu,
**nie** z SatNOGS/gr-satellites. To dowód braku regresji w tym porównaniu,
nie 100% skuteczności odbioru i nie dodatkowy zysk telemetryczny.

## Blokady pełnej kwalifikacji

VM nie udostępnia sterownika/urządzenia NVIDIA. Próba `--compute cuda
compute-info` kończy się kontrolowanym błędem. Nie ma pomiaru GPU ani
sprzętowego dowodu zgodności CPU–GPU. Do dalszej kwalifikacji trzeba udostępnić
GPU tej VM albo uruchomić te same polecenia na hoście z NVIDIA i NVRTC.

Pozostają: testy sprzętowe CUDA, powtarzane izolowane pomiary wydajności,
niezależny grupowany zbiór archiwalny, odpowiednie kontrole protokołów/szumu,
testy długotrwałe i kwalifikacja bezpieczeństwa oraz parametrów wdrożenia.
Nie zmieniono historycznych wyników kampanii 266 obserwacji ani jej
zamrożonych plików wykonywalnych. Nie wdrażano zmian na stronie internetowej.

## Artefakty

- Instrukcja: `docs/enterprise-compute-and-benchmark-v1.md`.
- Raport maszynowy: `reports/enterprise-cuda-qualification-20260914.json`.
- Zamrożony program: `work/enterprise-cuda-v1/release-v2/telemetry-yield-rs`.
- SHA-256 programu: `c745e2bccd23ed4472a8f8b3d51662729b7ec51dcda8a3c2c2616a107b76eaa5`.
- Spis 130 plików źródłowych: `work/enterprise-cuda-v1/source-inventory-v2.json`.
- Audyt ramek/zadań: `work/enterprise-cuda-v1/real-14967393-cpu-pair-v2/r0-parity.json`.
- Pełny benchmark: `work/enterprise-cuda-v1/real-14967393-cpu-pair-v2/summary.json`.
- Filtry: `work/enterprise-cuda-v1/qualification-real-cpu4-v2.json`.
- Jawna blokada GPU: `work/enterprise-cuda-v1/cuda-hardware-admission-v2.json`.

Pliki bez końcówki `v2` w katalogu tej pracy są zachowanymi etapami
pośrednimi. Do odtworzenia końcowego wyniku należy użyć wskazanej wersji v2.

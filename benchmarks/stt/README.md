# Lokalny benchmark polskiego STT

Ten katalog zawiera wyłącznie format i instrukcję benchmarku. Nagrania oraz wyniki
pozostają lokalne i są ignorowane przez Git.

## Przygotowanie korpusu

1. Utwórz `benchmarks/stt/local/`.
2. Umieść tam własne pliki WAV: mono, PCM16, 8–48 kHz, maksymalnie 120 sekund na
   próbkę.
3. Skopiuj `manifest.example.json` jako `manifest.local.json` i dopasuj względne
   ścieżki oraz transkrypcje referencyjne.
4. Nie dodawaj nagrań, transkrypcji prywatnych ani wyników do repozytorium.

Dobry korpus zawiera zwykłą polską rozmowę, krótkie słowa (np. „siekiera”), nazwy,
liczby, nazwy aplikacji, zdania polsko-angielskie, polecenia oraz próbki z hałasem.
Próbki powinny pochodzić z tego samego mikrofonu i pomieszczenia, na których
asystent będzie używany.

## Uruchomienie

Z katalogu `services/core`, po zainstalowaniu zależności:

```powershell
uv run moj-asystent-stt-benchmark ..\..\benchmarks\stt\manifest.local.json `
  --output ..\..\benchmarks\stt\results\voice-v2.json
```

Benchmark kolejno sprawdza:

- `medium` na CPU z `int8`,
- `large-v3-turbo` na CUDA z `int8_float16`,
- `large-v3-turbo` na CUDA z `float16`.

Profile bez lokalnego modelu lub zgodnego CUDA są oznaczane jako niedostępne.
Narzędzie używa wyłącznie lokalnej pamięci podręcznej (`local_files_only`) i nie
pobiera modeli. Raport zawiera transkrypcję i WER każdej numerowanej próbki oraz
zbiorcze opóźnienie, RTF, liczbę błędów i dostępną pamięć procesu/GPU. Nie zawiera
audio ani nazw plików. Wynik może zawierać prywatną treść mowy, dlatego katalog
`results/` jest ignorowany przez Git i raportu nie należy commitować.
Istniejący plik wyniku nie jest nadpisywany; wybierz nową nazwę dla kolejnego
pomiaru.

## Interpretacja

Niższy WER i RTF są lepsze. Dla profilu produkcyjnego oceniaj przede wszystkim
realne zdania i krótkie słowa, nie tylko średnią całego korpusu. Różnice mierzone na
małym korpusie traktuj jako sygnał do dalszego testu, nie dowód poprawy.

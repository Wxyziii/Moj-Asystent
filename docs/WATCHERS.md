# Watchery i powiadomienia

Milestone 11 dodaje lokalny, deterministyczny scheduler obserwacji. Watcher
porównuje krótkie obserwacje systemu i emituje zdarzenie dopiero przy
znaczącej zmianie. Nie wysyła cyklicznych promptów do modelu i nie wykonuje
żadnych działań systemowych.

## Obsługiwane typy

- `window` — tożsamość aktywnego okna, jego zamknięcie, pojawienie się,
  aktywacja lub zmiana tytułu;
- `process` — start, zakończenie albo aktywność procesu. Obserwacja PID-u wymaga
  czasu utworzenia, aby chronić przed ponownym użyciem PID-u;
- `file` — utworzenie, modyfikacja, usunięcie i stabilny rozmiar pliku;
- `resource` — próg CPU, RAM, GPU, VRAM lub temperatury GPU z czasem trwania i
  histerezą;
- `build` — rozpoznanie jawnie podanych markerów sukcesu lub błędu w ograniczonym
  pliku logu;
- `download` — pojawienie się pliku i stabilny rozmiar przez wskazane okno
  stabilności.

Ścieżki są kanonikalizowane przez tę samą politykę korzeni i reparse-pointów,
co narzędzia plikowe. Nie ma ogólnego watchera komend ani watchera całego
systemu plików.

## Cykl życia

Utworzenie wymaga `explicit_intent: true`. Definicje są przechowywane w wersji 2
lokalnego SQLite razem z ostatnim stanem. Po restarcie rdzeń przywraca tylko
aktywne, ponownie zweryfikowane obserwacje. Limit aktywnych/wstrzymanych
obserwacji wynosi 32, a sprawdzanie jest ograniczone do czterech równoległych
adapterów. Pauza, wznowienie, anulowanie, usunięcie i wygaszanie są
bezpieczne przy zamykaniu procesu.

Jednorazowa obserwacja przechodzi w `completed` po pierwszym zdarzeniu.
Niejednoznaczna zmiana tożsamości celu, niebezpieczny reparse point lub błąd
adaptera kończy obserwację stanem `failed` zamiast próbować innego celu.

## Powiadomienia i historia

Tylko znaczące przejścia trafiają do zdarzenia Protocol 1.3
`watcher.notification`. Zdarzenie zawiera identyfikator obserwacji,
identyfikator powiadomienia, typ, krótki komunikat, cel i akcje `inspect`,
`later`, `stop`. Powiadomienia są kierowane wyłącznie do uwierzytelnionych
sesji desktopu i nie kradną fokusu. Poziom `quiet` zapisuje zdarzenie bez
wyświetlania go.

Historia jest strukturalna i ograniczona do 256 ostatnich zdarzeń. Nie zawiera
obrazów, pełnych drzew UI, surowej telemetrii, nagrań ani pełnych logów builda.
Można ją wyczyścić przez uwierzytelnione API.

## Model i bezpieczeństwo

Model może zaproponować definicję dla jawnej prośby użytkownika przez typowane
narzędzie `create_watcher`. Rdzeń waliduje cały obiekt przed zapisem, wymaga
`explicit_intent: true`. W ścieżce modelu trwałe utworzenie i zmiany obserwacji
przechodzą zwykły mechanizm potwierdzeń. Dostępne są również ograniczone narzędzia
`list_watchers`, `pause_watcher`, `resume_watcher`, `cancel_watcher` i
`delete_watcher`. W Milestone 11 nie ma automatycznego tworzenia obserwacji ani
uruchamiania rutyn po wyzwoleniu. Zdarzenie obserwacji nie udziela uprawnień do
narzędzi; każda późniejsza akcja musi przejść zwykły ToolEngine i jego
potwierdzenia.

## Ręczna walidacja

Automatyczne testy obejmują wszystkie przejścia i adaptery przez fałszywe
źródła. Ręczna walidacja na docelowym Windowsie powinna używać wyłącznie
tymczasowego pliku, Notatnika i nieszkodliwego progu telemetrii; nie należy
dodawać prywatnych ścieżek, logów ani zrzutów do repozytorium. W tym środowisku
nie deklarujemy wykonania tej interaktywnej walidacji.

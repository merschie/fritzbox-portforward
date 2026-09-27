#!/bin/sh
# Unraid bringt kein "docker compose" mit. Dieses Skript fuehrt Compose aus dem
# offiziellen docker:cli-Image aus. Beispiele: ./compose.sh up -d --build | ./compose.sh logs -f | ./compose.sh down
cd "$(dirname "$0")" || exit 1
# -i/-t nur an einem Terminal, sonst liest docker run den stdin eines aufrufenden Skripts leer
TTY=""
[ -t 0 ] && TTY="-it"
exec docker run --rm $TTY \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$PWD:$PWD" -w "$PWD" \
  docker:cli compose -p portforward "$@"

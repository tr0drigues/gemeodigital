#!/usr/bin/env bash
# iniciar.sh — sobe o gemeo digital.

set -euo pipefail
cd "$(dirname "$0")"

PORTA="${PORTA:-8787}"
AMBIENTE="${AMBIENTE:-ambiente-continuidade.json}"
CENARIOS="${CENARIOS:-cenarios-continuidade.json}"
OBSERVADO="${OBSERVADO:-coletas/atual.json}"
ATUALIZAR="${ATUALIZAR:-5}"

azul()  { printf '\033[34m%s\033[0m\n' "$1"; }
cinza() { printf '\033[90m%s\033[0m\n' "$1"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$1"; }
erro()  { printf '  \033[31m✗\033[0m %s\n' "$1"; }

echo
azul "gemeo digital — Citrix Rendezvous V2 + EDT"
cinza "$(date '+%d/%m/%Y %H:%M:%S')"
echo

# ---------- dependencias ----------
command -v python3 >/dev/null || { erro "python3 nao encontrado"; exit 1; }
ok "python3 $(python3 -c 'import platform;print(platform.python_version())')"
if command -v node >/dev/null; then
  ok "node $(node --version) — validacao do motor disponivel"
  TEM_NODE=1
else
  erro "node ausente — o painel funciona, a validacao do motor nao"
  TEM_NODE=0
fi

# ---------- arquivos ----------
echo
cinza "arquivos"
for f in gemeo-citrix.html "$AMBIENTE" "$CENARIOS" "$OBSERVADO"; do
  if [ -f "$f" ]; then ok "$f"; else erro "$f (nao encontrado)"; exit 1; fi
done

# ---------- credenciais ----------
if [ -f credenciais.local.json ]; then
  MODELO=$(python3 -c "import json;print(json.load(open('credenciais.local.json')).get('modelo','?'))" 2>/dev/null || echo "?")
  ok "credenciais.local.json — modelo $MODELO"
else
  cinza "  sem credenciais.local.json — a analise por IA fica desligada"
fi

# ---------- validacao ----------
if [ "$TEM_NODE" = "1" ]; then
  echo
  cinza "validando o modelo"
  python3 twin.py check "$AMBIENTE" --cenarios "$CENARIOS" | sed 's/^/  /'
  if [ -f casos-continuidade.json ]; then
    echo
    cinza "suite de casos"
    python3 twin.py test casos-continuidade.json --quiet | sed 's/^/  /'
  fi
fi

# ---------- servidor ----------
echo
cinza "servidor"
if lsof -ti tcp:"$PORTA" >/dev/null 2>&1; then
  erro "porta $PORTA ja esta em uso — encerre o processo ou use PORTA=8788 ./iniciar.sh"
  exit 1
fi

python3 -m http.server "$PORTA" --bind 127.0.0.1 >/dev/null 2>&1 &
PID=$!
trap 'echo; cinza "encerrando..."; kill $PID 2>/dev/null || true' EXIT INT TERM
sleep 1
kill -0 $PID 2>/dev/null || { erro "o servidor nao subiu"; exit 1; }
ok "http://127.0.0.1:$PORTA  (pid $PID)"

URL="http://127.0.0.1:$PORTA/gemeo-citrix.html?ambiente=$AMBIENTE&cenarios=$CENARIOS&observado=$OBSERVADO&atualizar=$ATUALIZAR"
echo
cinza "abrindo"
ok "ambiente   $AMBIENTE"
ok "cenarios   $CENARIOS"
ok "observado  $OBSERVADO"
echo
case "$(uname -s)" in
  Darwin) open "$URL" ;;
  Linux)  command -v xdg-open >/dev/null && xdg-open "$URL" >/dev/null 2>&1 || true ;;
esac
azul "$URL"
echo
cinza "ctrl+c encerra"
wait $PID

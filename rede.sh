#!/usr/bin/env bash
# rede.sh — liga e desliga degradação de rede no macOS, para exercitar o modelo.
#
# Usa dummynet (dnctl) e pf. Só macOS. Exige sudo.
#
#   ./rede.sh latencia 200      adiciona 200 ms na saída (RTT sobe ~200 ms)
#   ./rede.sh perda 5           descarta 5% dos pacotes de saída
#   ./rede.sh ruim              latência 250 ms + 3% de perda
#   ./rede.sh bloqueia-udp      corta UDP 443 de saída
#   ./rede.sh normal            devolve tudo ao estado original
#   ./rede.sh status            mostra o que está ativo
#
# O modelo só enxerga LATÊNCIA: o coletor mede o tempo de conexão, e o limiar
# em ambiente-*.json liga a condição a partir dele. Perda de pacote não vira
# condição hoje — degrada a sessão de verdade, mas o painel não reporta.

set -euo pipefail

PIPE=1
PORTA=443

precisa_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "precisa de sudo: sudo $0 $*" >&2
    exit 1
  fi
}

aplicar() {  # $1 = config do pipe, $2 = descrição, $3 = proto
  local proto="${3:-{tcp,udp}}"
  dnctl pipe $PIPE config $1
  printf 'dummynet out proto %s to any port %d pipe %d\n' "$proto" "$PORTA" "$PIPE" \
    | pfctl -f - -E 2>/dev/null
  echo "ativo: $2"
  echo "para desfazer:  sudo $0 normal"
}

case "${1:-}" in
  latencia)
    precisa_root "$@"
    MS="${2:-200}"
    aplicar "delay ${MS}" "latência +${MS} ms na saída da porta ${PORTA}"
    ;;

  perda)
    precisa_root "$@"
    PCT="${2:-5}"
    PLR=$(python3 -c "print(${PCT}/100)")
    aplicar "plr ${PLR}" "perda de ${PCT}% na saída da porta ${PORTA}"
    ;;

  ruim)
    precisa_root "$@"
    aplicar "delay 250 plr 0.03" "latência +250 ms e 3% de perda"
    ;;

  bloqueia-udp)
    precisa_root "$@"
    printf 'block drop out proto udp to any port %d\n' "$PORTA" | pfctl -f - -E 2>/dev/null
    echo "ativo: UDP ${PORTA} de saída bloqueado"
    echo "para desfazer:  sudo $0 normal"
    ;;

  normal)
    precisa_root "$@"
    # o -E conta referências: desliga até o pf ficar realmente parado
    for _ in 1 2 3 4 5; do
      pfctl -d 2>/dev/null || true
      pfctl -s info 2>/dev/null | grep -q "Status: Disabled" && break
    done
    dnctl -q flush 2>/dev/null || true
    pfctl -f /etc/pf.conf 2>/dev/null || true
    echo "rede normalizada"
    pfctl -s info 2>/dev/null | head -1
    ;;

  status)
    echo "--- pf ---"
    sudo pfctl -s info 2>/dev/null | head -2 || true
    echo "--- regras ativas ---"
    sudo pfctl -s rules 2>/dev/null || true
    echo "--- pipes ---"
    sudo dnctl list 2>/dev/null || echo "(nenhum)"
    ;;

  *)
    sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
    ;;
esac

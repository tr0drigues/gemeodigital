#!/usr/bin/env python3
"""
echo-udp.py — devolve na porta UDP o mesmo pacote que recebeu.

O coletor precisa disto para medir UDP de forma conclusiva. Mandar pacote para
o Gateway Service da Citrix não serve: o serviço não responde a pacote
arbitrário, e a falta de resposta seria lida como bloqueio sem sê-lo.

ONDE RODAR
----------
Em produção: numa máquina FORA da rede que você quer testar. Aí o teste prova
o caminho de saída de verdade, de ponta a ponta.

Para verificar uma regra local de firewall (pf, por exemplo): na própria
máquina, em 127.0.0.1. O pf avalia o tráfego de saída antes do loopback quando
a regra padrão do sistema foi substituída, então uma regra que bloqueia UDP 443
de saída também corta este caminho — e o coletor passa a reportar bloqueado.

Uso:
    sudo ./echo-udp.py                 # porta 443, precisa de root
    ./echo-udp.py --porta 4443         # porta alta, sem root

    # no outro terminal
    python3 coletor-citrix.py --refletor-udp 127.0.0.1 --saida /tmp/estado.json
    python3 coletor-citrix.py --refletor-udp 127.0.0.1 --porta-udp 4443 --saida /tmp/estado.json
"""

import argparse
import datetime
import socket
import sys


def main():
    ap = argparse.ArgumentParser(description="Refletor UDP para o coletor.")
    ap.add_argument("--porta", type=int, default=443)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--silencioso", action="store_true")
    args = ap.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((args.bind, args.porta))
    except PermissionError:
        sys.exit("porta %d exige root. Use sudo, ou --porta 4443 e "
                 "--porta-udp 4443 no coletor." % args.porta)
    except OSError as e:
        sys.exit("não consegui abrir %s:%d — %s" % (args.bind, args.porta, e))

    print("refletor UDP em %s:%d — ctrl+c encerra" % (args.bind, args.porta))
    n = 0
    try:
        while True:
            dados, origem = s.recvfrom(4096)
            s.sendto(dados, origem)
            n += 1
            if not args.silencioso:
                print("[%s] %s:%d  %d bytes devolvidos"
                      % (datetime.datetime.now().strftime("%H:%M:%S"),
                         origem[0], origem[1], len(dados)))
    except KeyboardInterrupt:
        print("\nencerrado após %d pacote(s)" % n)
    finally:
        s.close()


if __name__ == "__main__":
    main()

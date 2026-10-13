#!/usr/bin/env python3
"""
coletor-citrix.py — mede o caminho de rede e grava estado-observado.json
para o gemeo digital consumir.

Roda em macOS, Linux e Windows. Só biblioteca padrão, nada para instalar.

SOBRE OS ALVOS
--------------
Os requisitos de firewall da Citrix são wildcards, e wildcard não se resolve:

  *.*.nssvc.net     dois níveis de wildcard. Não existe host derivável do padrão.
                    Um host real e resolvível do Gateway Service é
                    global.g.nssvc.net, usado aqui como alvo padrão do controle.

  *.xendesktop.net  o apex não tem registro A, e o domínio responde NODATA para
                    subdomínio inexistente. O alvo testável é
                    <customer_id>.xendesktop.net, com o seu ID do Citrix Cloud.
                    Passe --customer-id; sem ele o registro não é testado.

Para descobrir quais PoPs este host está realmente usando:
    ./coletor-citrix.py --descobrir

O QUE MEDE DE FORMA CONCLUSIVA
------------------------------
  - resolução DNS, distinguindo nome inexistente de nome sem registro A
  - conexão TCP até os destinos
  - emissor do certificado TLS, o que detecta inspeção no caminho de saída
  - UDP contra um refletor próprio, quando configurado

O QUE NÃO MEDE, E POR QUÊ
-------------------------
  - UDP contra o Gateway Service. O serviço não responde a pacote arbitrário,
    então o teste daria falso negativo. Suba um echo UDP 443 fora da sua rede
    e aponte --refletor-udp para ele: aí o resultado é conclusivo.
  - qual transporte cada sessão está usando. Vem da sua monitoração de sessão.
    Veja o bloco SESSÕES no fim do arquivo.

Uso:
    ./coletor-citrix.py --customer-id SEUID --saida estado-observado.json
    ./coletor-citrix.py --descobrir
    ./coletor-citrix.py --alvo ctl=global.g.nssvc.net --refletor-udp echo.seudominio.com
"""

import argparse
import datetime
import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
import uuid

CAS_PUBLICAS = (
    "DigiCert", "Sectigo", "GlobalSign", "Amazon", "Baltimore",
    "Entrust", "Let's Encrypt", "ISRG", "Microsoft", "Google Trust",
)

GATEWAY_PADRAO = "global.g.nssvc.net"

OK            = "ok"
BLOQUEADO     = "bloqueado"       # resolveu, mas a conexão não passa
NAO_RESOLVIDO = "nao-resolvido"   # o nome não existe (NXDOMAIN)
SEM_REGISTRO  = "sem-registro-a"  # o domínio existe, mas não tem endereço
NAO_TESTADO   = "nao-testado"


def agora():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def resolver(fqdn):
    """Distingue NXDOMAIN de NODATA: são problemas diferentes, com ações diferentes."""
    try:
        infos = socket.getaddrinfo(fqdn, None, socket.AF_INET, socket.SOCK_STREAM)
        return {"ok": True, "ips": sorted({i[4][0] for i in infos})}
    except socket.gaierror as e:
        code = e.args[0]
        if code == socket.EAI_NONAME:
            return {"ok": False, "ips": [], "estado": NAO_RESOLVIDO,
                    "erro": "o nome não existe no DNS",
                    "acao": "confira o FQDN. Wildcard de allowlist não é um host resolvível."}
        if code == getattr(socket, "EAI_NODATA", -5):
            return {"ok": False, "ips": [], "estado": SEM_REGISTRO,
                    "erro": "o domínio existe mas não tem registro A",
                    "acao": "este nome só serve em regra de firewall, não como alvo de teste."}
        return {"ok": False, "ips": [], "estado": NAO_RESOLVIDO, "erro": str(e)}
    except OSError as e:
        return {"ok": False, "ips": [], "estado": NAO_RESOLVIDO, "erro": str(e)}


def testar_tcp(fqdn, porta, timeout, dns):
    """Falha de DNS nunca é reportada como bloqueio de firewall."""
    if not dns["ok"]:
        return {"estado": dns.get("estado", NAO_RESOLVIDO), "erro": dns["erro"],
                "acao": dns.get("acao"), "testado": False}
    inicio = time.monotonic()
    try:
        with socket.create_connection((fqdn, porta), timeout=timeout):
            return {"estado": OK, "rtt_ms": int((time.monotonic() - inicio) * 1000),
                    "testado": True}
    except socket.timeout:
        return {"estado": BLOQUEADO, "erro": "timeout em %ds" % timeout, "testado": True}
    except OSError as e:
        return {"estado": BLOQUEADO, "erro": str(e), "testado": True}


def inspecionar_tls(fqdn, porta, timeout, dns):
    """
    Handshake com validação normal. Dois resultados interessam:
      - validou e o emissor é CA interna -> há inspeção TLS, e a CA do proxy
        está instalada neste host.
      - a validação falhou -> algo reescreve o TLS com CA que o host não confia.
    Sem validação o Python devolve certificado vazio, por isso o caminho verificado.
    """
    if not dns["ok"]:
        return {"ok": False, "erro": dns["erro"], "inspecao": None}
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((fqdn, porta), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=fqdn) as tls:
                cert = tls.getpeercert() or {}
                emissor = {}
                for campo in cert.get("issuer", ()):
                    for chave, valor in campo:
                        emissor[chave] = valor
                nome = emissor.get("organizationName") or emissor.get("commonName") or "?"
                publica = any(ca.lower() in nome.lower() for ca in CAS_PUBLICAS)
                return {"ok": True, "validou": True, "emissor": nome,
                        "emissor_cn": emissor.get("commonName"),
                        "expira": cert.get("notAfter"), "protocolo": tls.version(),
                        "inspecao": not publica}
    except ssl.SSLCertVerificationError as e:
        return {"ok": True, "validou": False, "erro": str(e), "inspecao": True}
    except OSError as e:
        return {"ok": False, "erro": str(e), "inspecao": None}


def refletor_local(alvo):
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(alvo))
    except (OSError, ValueError):
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def testar_udp(alvo, porta, timeout):
    if not alvo:
        return {"estado": NAO_TESTADO,
                "erro": "nenhum refletor UDP configurado (--refletor-udp)",
                "acao": "sem um echo UDP próprio não há como provar o caminho de saída UDP."}
    marca = uuid.uuid4().hex.encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    inicio = time.monotonic()
    try:
        s.sendto(b"twin-probe:" + marca, (alvo, porta))
        dados, _ = s.recvfrom(2048)
        if marca in dados:
            r = {"estado": OK, "rtt_ms": int((time.monotonic() - inicio) * 1000)}
            if refletor_local(alvo):
                r["aviso"] = ("refletor na rede local: serve para laboratório, "
                              "não prova a saída UDP 443 até o Gateway")
            return r
        return {"estado": "degradado", "erro": "resposta não confere com o probe enviado"}
    except socket.timeout:
        return {"estado": BLOQUEADO, "erro": "sem resposta em %ds" % timeout}
    except OSError as e:
        return {"estado": BLOQUEADO, "erro": str(e)}
    finally:
        s.close()


def descobrir():
    """Lista conexões de saída na 443 e resolve o reverso, para achar os PoPs em uso."""
    saida = None
    for c in (["ss", "-tn", "state", "established"], ["netstat", "-n"]):
        try:
            r = subprocess.run(c, capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                saida = r.stdout
                break
        except (OSError, subprocess.TimeoutExpired):
            continue
    if saida is None:
        print("não consegui listar conexões (ss e netstat indisponíveis)", file=sys.stderr)
        return 1

    ips = set()
    for linha in saida.splitlines():
        for m in re.finditer(r"(\d+\.\d+\.\d+\.\d+)[:.](\d+)", linha):
            if m.group(2) == "443":
                ips.add(m.group(1))
    if not ips:
        print("nenhuma conexão de saída na 443 neste momento.")
        print("abra uma sessão HDX e rode de novo — os PoPs aparecem enquanto há sessão.")
        return 0

    print("conexões na 443 agora:\n")
    for ip in sorted(ips):
        try:
            nome = socket.gethostbyaddr(ip)[0]
        except OSError:
            nome = "(sem reverso)"
        marca = "   <-- Gateway Service" if "nssvc" in nome else ""
        print("  %-16s %s%s" % (ip, nome, marca))
    print("\nOs nomes com nssvc são PoPs do Gateway Service.")
    print("Use um deles em --alvo para medir exatamente o PoP que este host usa.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Coletor do gêmeo digital Citrix.")
    ap.add_argument("--saida", default="estado-observado.json")
    ap.add_argument("--alvo", action="append", default=[], metavar="ID=FQDN[:PORTA]",
                    help="fluxo a testar; o ID deve bater com o id no ambiente.json")
    ap.add_argument("--customer-id", default="",
                    help="ID do Citrix Cloud, para montar <id>.xendesktop.net")
    ap.add_argument("--refletor-udp", default="",
                    help="host do seu echo UDP, fora da rede a testar")
    ap.add_argument("--porta-udp", type=int, default=443)
    ap.add_argument("--timeout", type=int, default=4)
    ap.add_argument("--origem", default=socket.gethostname())
    ap.add_argument("--descobrir", action="store_true",
                    help="lista os PoPs em uso neste host e sai")
    args = ap.parse_args()

    if args.descobrir:
        sys.exit(descobrir())

    alvos = list(args.alvo)
    if not any(a.startswith("ctl=") for a in alvos):
        alvos.append("ctl=" + GATEWAY_PADRAO)
    if not any(a.startswith("reg=") for a in alvos):
        if args.customer_id:
            alvos.append("reg=%s.xendesktop.net" % args.customer_id)
        else:
            print("aviso: sem --customer-id o registro não é testado.", file=sys.stderr)
            print("       *.xendesktop.net é wildcard de allowlist, não um host.\n",
                  file=sys.stderr)

    parsed = []
    for item in alvos:
        if "=" not in item:
            sys.exit("alvo inválido: %s (esperado ID=FQDN[:PORTA])" % item)
        fid, destino = item.split("=", 1)
        porta = 443
        if ":" in destino:
            destino, p = destino.rsplit(":", 1)
            porta = int(p)
        parsed.append((fid, destino, porta))

    print("coletando...", file=sys.stderr)

    anterior = {}
    if os.path.exists(args.saida):
        try:
            with open(args.saida, encoding="utf-8") as fp:
                anterior = json.load(fp).get("dns", {})
        except (OSError, ValueError):
            anterior = {}

    dns, fluxos, tls_por_alvo, alvo_de = {}, {}, {}, {}
    rotacionou_algum = False

    for fid, destino, porta in parsed:
        r = resolver(destino)
        antes = anterior.get(fid, {}).get("ips", [])
        r["rotacionou"] = bool(antes) and sorted(antes) != sorted(r["ips"])
        r["fqdn"] = destino
        rotacionou_algum = rotacionou_algum or r["rotacionou"]
        dns[fid] = r
        alvo_de[fid] = destino
        fluxos[fid] = testar_tcp(destino, porta, args.timeout, r)
        tls_por_alvo[fid] = inspecionar_tls(destino, porta, args.timeout, r)

    udp = testar_udp(args.refletor_udp, args.porta_udp, args.timeout)

    inspecao = any(t.get("inspecao") for t in tls_por_alvo.values())
    testados = [f for f in fluxos.values() if f.get("testado")]
    tcp_falhou = any(f["estado"] != OK for f in testados)

    # ---------------------------------------------------------------- SESSÕES
    # O host não sabe dizer sozinho se a sessão está em EDT ou TCP.
    # Preencha a partir da sua monitoração de sessão e deixe o resto como está.
    sessoes = None
    # exemplo: sessoes = {"total": 24, "edt": 19, "tcp": 5}

    estado = {
        "schema": "gemeo-citrix-observado/1.1",
        "coletado_em": agora(),
        "origem": "coletor-citrix.py / %s / %s" % (args.origem, sys.platform),
        "alvos": alvo_de,
        "dns": dns,
        "tls": tls_por_alvo,
        "fluxos": dict(fluxos, **({"edt-dc": udp} if args.refletor_udp else {})),
        "condicoes": {
            "tls": inspecao,
            "ip-fixo": rotacionou_algum and tcp_falhou,
            "proxy-tcp": udp["estado"] == BLOQUEADO and not tcp_falhou,
        },
        "sessoes": sessoes,
        "nota": "Fluxos da rede do cliente não são alcançáveis daqui. "
                "Rode uma cópia no endpoint do cliente e junte os dois arquivos.",
    }

    destino_dir = os.path.dirname(os.path.abspath(args.saida))
    os.makedirs(destino_dir, exist_ok=True)
    with open(args.saida, "w", encoding="utf-8") as fp:
        json.dump(estado, fp, indent=2, ensure_ascii=False)

    print("gravado em %s\n" % args.saida, file=sys.stderr)
    for fid, r in fluxos.items():
        linha = "  %-8s %-14s %s" % (fid, r["estado"], alvo_de[fid])
        if r.get("rtt_ms") is not None:
            linha += "  %d ms" % r["rtt_ms"]
        print(linha, file=sys.stderr)
        if r.get("acao"):
            print("           %s" % r["acao"], file=sys.stderr)
    print("  %-8s %-14s %s" % ("udp", udp["estado"], args.refletor_udp or "-"),
          file=sys.stderr)

    if inspecao:
        quais = [f for f, t in tls_por_alvo.items() if t.get("inspecao")]
        print("\n  inspeção TLS detectada em: %s" % ", ".join(quais), file=sys.stderr)
    if rotacionou_algum:
        print("  IPs mudaram desde a última coleta", file=sys.stderr)
    if any(f["estado"] in (NAO_RESOLVIDO, SEM_REGISTRO) for f in fluxos.values()):
        print("\n  Falha de DNS não é bloqueio de firewall. Rode --descobrir para ver",
              file=sys.stderr)
        print("  quais PoPs este host está realmente usando.", file=sys.stderr)


if __name__ == "__main__":
    main()

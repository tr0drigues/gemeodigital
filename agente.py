#!/usr/bin/env python3
"""
agente.py — coleta em intervalo fixo e publica o estado corrente.

Em produção roda como serviço agendado.Para acompanhar o ambiente em tempo real executar manualmente
enquanto o painel fica aberto.

Dois modos:

  --real        mede de verdade a cada ciclo, com o mesmo coletor.
                Depende de rede; o RTT varia a cada ciclo, que é o ponto.

  --roteiro ARQ reproduz uma sequência de estados já gravados, em intervalos.
                Não toca na rede. É o modo para quando a conexão não pode
                ser variável de risco.

Publica sempre em coletas/atual.json, que é o arquivo que o painel lê, e
arquiva cada ciclo em coletas/<data>_<hora>.json.

Uso:
    ./agente.py --real --intervalo 20 --customer-id SEU_CCID
    ./agente.py --roteiro roteiro.json
    ./agente.py --roteiro roteiro.json --intervalo 12 --repetir
"""

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
DESTINO = os.path.join(AQUI, "coletas")
ATUAL = os.path.join(DESTINO, "atual.json")

V, A, R, C, Z = "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[0m"
if not sys.stdout.isatty():
    V = A = R = C = Z = ""

COR = {"ok": V, "deg": A, "fail": R}


def agora():
    return datetime.datetime.now(datetime.timezone.utc)


def hora_local():
    return datetime.datetime.now().strftime("%H:%M:%S")


def publicar(estado):
    os.makedirs(DESTINO, exist_ok=True)
    with open(ATUAL, "w", encoding="utf-8") as fp:
        json.dump(estado, fp, indent=2, ensure_ascii=False)
    ts = datetime.datetime.fromisoformat(estado["coletado_em"])
    arquivo = os.path.join(DESTINO, ts.strftime("%Y-%m-%d_%H%M%S") + ".json")
    shutil.copy(ATUAL, arquivo)
    return os.path.basename(arquivo)


def veredito(ambiente, estado):
    """Roda o motor real sobre o estado publicado. Sem Node, devolve None.
    A tradução coleta -> modelo é o aplicarObservado() do motor, a mesma do painel."""
    if not shutil.which("node"):
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fp:
        json.dump(estado, fp)
        caminho = fp.name
    try:
        r = subprocess.run([sys.executable, os.path.join(AQUI, "twin.py"),
                            "eval", ambiente, "--json", "--observado", caminho],
                           capture_output=True, text=True, timeout=45)
        return json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    finally:
        os.unlink(caminho)


def linha_coleta(estado, arquivo):
    fluxos = estado.get("fluxos") or {}
    ok = sum(1 for f in fluxos.values() if f.get("estado") == "ok")
    rtts = [f.get("rtt_ms") for f in fluxos.values() if isinstance(f.get("rtt_ms"), int)]
    resumo = "%d/%d fluxos" % (ok, len(fluxos))
    if rtts:
        resumo += "   rtt %d–%d ms" % (min(rtts), max(rtts))
    cor = V if ok == len(fluxos) else (A if ok else R)
    print("%s[%s]%s  %s%-30s%s  %s%s" % (C, hora_local(), Z, cor, resumo, Z, C, arquivo + Z))
    for fid, f in sorted(fluxos.items()):
        if f.get("estado") != "ok":
            print("            %s%-14s %s%s  %s" % (R, fid, f.get("estado"), Z,
                                                   f.get("erro", "")[:52]))


def linha_veredito(v):
    if not v:
        return
    partes = ["%s%s: %s%s" % (COR.get(p["nivel"], ""), p["perfil"], p["titulo"], Z) for p in v]
    print("            " + "   ·   ".join(partes))


# ------------------------------------------------------------------ modo real
def ciclo_real(args, ciclo):
    saida = os.path.join(DESTINO, "_bruto-dc.json")
    cmd = [sys.executable, os.path.join(AQUI, "coletor-citrix.py"),
           "--saida", saida, "--timeout", str(args.timeout)]
    if args.customer_id:
        cmd += ["--customer-id", args.customer_id]
    for a in args.alvo or []:
        cmd += ["--alvo", a]
    if args.refletor_udp:
        cmd += ["--refletor-udp", args.refletor_udp]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("%s[%s]  coletor falhou: %s%s" % (R, hora_local(), r.stderr.strip()[:70], Z))
        return None
    with open(saida, encoding="utf-8") as fp:
        estado = json.load(fp)
    estado["ciclo"] = ciclo
    return estado


# --------------------------------------------------------------- modo roteiro
def ciclo_roteiro(passo, ciclo):
    caminho = os.path.join(AQUI, passo["estado"])
    with open(caminho, encoding="utf-8") as fp:
        estado = json.load(fp)
    estado["coletado_em"] = agora().isoformat()
    estado["ciclo"] = ciclo
    return estado


def main():
    ap = argparse.ArgumentParser(description="Agente de coleta do gêmeo digital.")
    ap.add_argument("--real", action="store_true", help="mede de verdade a cada ciclo")
    ap.add_argument("--roteiro", help="arquivo de roteiro a reproduzir")
    ap.add_argument("--intervalo", type=int, default=20, help="segundos entre ciclos")
    ap.add_argument("--ambiente", default="ambiente-continuidade.json")
    ap.add_argument("--customer-id", default="")
    ap.add_argument("--alvo", action="append", default=[])
    ap.add_argument("--refletor-udp", default="")
    ap.add_argument("--timeout", type=int, default=4)
    ap.add_argument("--repetir", action="store_true", help="roteiro em laço")
    ap.add_argument("--sem-veredito", action="store_true")
    args = ap.parse_args()

    if not args.real and not args.roteiro:
        sys.exit("escolha --real ou --roteiro ARQ")

    roteiro = None
    if args.roteiro:
        with open(os.path.join(AQUI, args.roteiro), encoding="utf-8") as fp:
            roteiro = json.load(fp)

    print()
    print("%sagente de coleta%s   ambiente %s   intervalo %ds   publica em coletas/atual.json"
          % (C, Z, args.ambiente, args.intervalo))
    if roteiro:
        print("%sroteiro%s %s   %d passo(s)%s"
              % (C, Z, roteiro.get("nome", args.roteiro), len(roteiro["passos"]),
                 "   em laço" if args.repetir else ""))
    print("%sctrl+c encerra%s" % (C, Z))
    print()

    ciclo = 0
    try:
        while True:
            ciclo += 1
            if roteiro:
                passos = roteiro["passos"]
                i = (ciclo - 1) % len(passos)
                if not args.repetir and ciclo > len(passos):
                    print("%sroteiro concluído%s" % (C, Z))
                    break
                passo = passos[i]
                estado = ciclo_roteiro(passo, ciclo)
                if passo.get("nota"):
                    print("%s— %s%s" % (C, passo["nota"], Z))
            else:
                estado = ciclo_real(args, ciclo)
                if estado is None:
                    time.sleep(args.intervalo)
                    continue

            arquivo = publicar(estado)
            linha_coleta(estado, arquivo)
            if not args.sem_veredito:
                linha_veredito(veredito(args.ambiente, estado))
            time.sleep(args.intervalo)
    except KeyboardInterrupt:
        print("\n%sencerrado após %d ciclo(s)%s" % (C, ciclo, Z))


if __name__ == "__main__":
    main()

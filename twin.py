#!/usr/bin/env python3
"""
twin.py — ferramenta de linha de comando do gêmeo digital Citrix.

O motor NÃO é reimplementado aqui. Este script extrai o bloco delimitado por
MOTOR:INICIO / MOTOR:FIM de dentro do gemeo-citrix.html e executa esse mesmo
código no Node. Se o painel e esta ferramenta discordarem, é bug de extração,
nunca divergência entre duas implementações — porque só existe uma.

Comandos:
    check     valida os arquivos de ambiente e cenários (referências, ids, órfãos)
    merge     junta coletas de pontos diferentes num estado observado só
    eval      avalia um estado e imprime o veredito por perfil
    sweep     varredura exaustiva de 1 ou 2 eventos
    test      roda uma suíte de casos esperados contra o motor

Exemplos:
    ./twin.py check ambiente-continuidade.json --cenarios cenarios-continuidade.json
    ./twin.py eval  ambiente-continuidade.json --no workspace=off
    ./twin.py sweep ambiente-continuidade.json --profundidade 2
    ./twin.py test  casos-continuidade.json
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
HTML_PADRAO = os.path.join(AQUI, "gemeo-citrix.html")
INI, FIM = "/* ===== MOTOR:INICIO", "/* ===== MOTOR:FIM ===== */"

VERDE, VERM, AMAR, CINZA, FIMC = "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[0m"
if not sys.stdout.isatty():
    VERDE = VERM = AMAR = CINZA = FIMC = ""

COR = {"ok": VERDE, "deg": AMAR, "fail": VERM}


# ------------------------------------------------------------------ motor
def extrair_motor(html=HTML_PADRAO):
    if not os.path.exists(html):
        sys.exit("não encontrei %s" % html)
    with open(html, encoding="utf-8") as fp:
        s = fp.read()
    try:
        i = s.index(INI)
        j = s.index(FIM)
    except ValueError:
        sys.exit("marcadores MOTOR:INICIO / MOTOR:FIM não encontrados no HTML")
    return s[i:j]


def rodar_motor(env, estado, expressao, html=HTML_PADRAO, observado=None, extensao=None, ecoar=True):
    """Executa `expressao` com ENV e S carregados, devolve o JSON impresso.
    Com `observado`, aplica a coleta pelo mesmo aplicarObservado() do painel.
    Com `extensao`, valida e liga as condições hipotéticas como o painel faz;
    as descartadas saem em stderr."""
    if not shutil.which("node"):
        sys.exit("este comando precisa do Node instalado (o motor é JavaScript).\n"
                 "  macOS:  brew install node\n"
                 "  Debian: apt install nodejs")

    harness = (
        "let OBS = %s;\n"
        "let ENV = %s;\n"
        "const S = { fluxos:{}, condicoes:{}, nos:{}, sem_efeito:{}, perfil:null };\n"
        "%s\n"
        "resetEstado(); aplicarObservado();\n"
        "// mesma ordem do painel: a extensão entra sobre o estado já montado\n"
        "const _ext = validarExtensao(%s);\n"
        "aplicarExtensao(_ext.aceitas);\n"
        "_ext.aceitas.condicoes.forEach(c => S.condicoes[c.id] = true);\n"
        "_ext.descartadas.forEach(d => console.error('hipótese descartada: ' + d.id + ': ' + d.motivo));\n"
        "const _sobre = %s;\n"
        "[\"fluxos\",\"condicoes\",\"nos\",\"sem_efeito\"].forEach(g => Object.assign(S[g], _sobre[g] || {}));\n"
        "if(_sobre.perfil) S.perfil = _sobre.perfil;\n"
        "console.log(JSON.stringify(%s));\n"
    ) % (json.dumps(observado), json.dumps(env), extrair_motor(html), json.dumps(extensao),
           json.dumps(estado), expressao)

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fp:
        fp.write(harness)
        caminho = fp.name
    try:
        r = subprocess.run(["node", caminho], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit("o motor falhou ao executar:\n" + r.stderr.strip())
        if ecoar and r.stderr.strip():
            print(AMAR + r.stderr.strip() + FIMC, file=sys.stderr)
        return json.loads(r.stdout)
    finally:
        os.unlink(caminho)


def resumo_perfis(saida):
    return [
        {
            "perfil": p["nome"],
            "nivel": p["nivel"],
            "titulo": p["titulo"],
            "fatores": p.get("fatores") or [],
            "composta": bool(p.get("composta")),
            "estagios": {e["id"]: {"status": p["res"][e["id"]]["status"],
                                   "caminho": p["res"][e["id"]].get("caminho")}
                         for e in p["estagios"]},
        }
        for p in saida["perfis"]
    ]


# ------------------------------------------------------------------ check
def cmd_check(args):
    env = json.load(open(args.ambiente, encoding="utf-8"))
    problemas, avisos = [], []

    for campo in ("zonas", "nos", "estagios", "fluxos"):
        if not isinstance(env.get(campo), list):
            problemas.append("campo obrigatório ausente ou inválido: %s" % campo)
    if problemas:
        return imprimir_check(args.ambiente, problemas, avisos)

    ids_zona = {z["id"] for z in env["zonas"]}
    ids_no = {n["id"] for n in env["nos"]}
    ids_est = {e["id"] for e in env["estagios"]}
    ids_fl = {f["id"] for f in env["fluxos"]}
    ids_cond = {c["id"] for c in env.get("condicoes", [])}
    ids_se = {r["id"] for r in env.get("regras_sem_efeito", [])}
    ids_perf = {p["id"] for p in env.get("perfis", [])}

    for nome, lista in (("nós", env["nos"]), ("fluxos", env["fluxos"]),
                        ("estágios", env["estagios"]), ("condições", env.get("condicoes", []))):
        vistos = set()
        for it in lista:
            if it["id"] in vistos:
                problemas.append("%s: id repetido '%s'" % (nome, it["id"]))
            vistos.add(it["id"])

    for n in env["nos"]:
        if n["zona"] not in ids_zona:
            problemas.append("nó '%s': zona inexistente '%s'" % (n["id"], n["zona"]))
    for f in env["fluxos"]:
        for lado in ("de", "para"):
            if f[lado] not in ids_no:
                problemas.append("fluxo '%s': nó inexistente '%s'" % (f["id"], f[lado]))
        if f["estagio"] not in ids_est:
            problemas.append("fluxo '%s': estágio inexistente '%s'" % (f["id"], f["estagio"]))
        if f.get("zona") and f["zona"] not in ids_zona:
            problemas.append("fluxo '%s': zona inexistente '%s'" % (f["id"], f["zona"]))
    for e in env["estagios"]:
        for pf in e.get("perfis", []):
            if pf not in ids_perf:
                problemas.append("estágio '%s': perfil inexistente '%s'" % (e["id"], pf))
        se_tem = [f for f in env["fluxos"] if f["estagio"] == e["id"]]
        if not se_tem:
            avisos.append("estágio '%s' não tem nenhum fluxo" % e["id"])
        if e.get("exige") == "preferencial":
            prefs = {f.get("preferencia", 2 if f.get("papel") in ("fallback", "alternativo") else 1)
                     for f in se_tem}
            for k in prefs:
                if str(k) not in (e.get("caminhos") or {}):
                    avisos.append("estágio '%s': preferência %s sem nome em 'caminhos'" % (e["id"], k))
    for c in env.get("condicoes", []):
        alvos = c.get("bloqueia", []) + c.get("degrada", [])
        if not alvos:
            avisos.append("condição '%s' não afeta nenhum fluxo" % c["id"])
        for fid in alvos:
            if fid not in ids_fl:
                problemas.append("condição '%s': fluxo inexistente '%s'" % (c["id"], fid))
        if c.get("zona") and c["zona"] not in ids_zona:
            problemas.append("condição '%s': zona inexistente '%s'" % (c["id"], c["zona"]))

    orfaos = [n["id"] for n in env["nos"]
              if not any(f["de"] == n["id"] or f["para"] == n["id"] for f in env["fluxos"])]
    for o in orfaos:
        avisos.append("nó '%s' não participa de nenhum fluxo" % o)

    if args.cenarios:
        cen = json.load(open(args.cenarios, encoding="utf-8"))
        lista = cen if isinstance(cen, list) else cen.get("cenarios", [])
        amb_cen = cen.get("ambiente") if isinstance(cen, dict) else None
        if amb_cen and env.get("ambiente") and amb_cen != env["ambiente"]:
            avisos.append("cenários são do ambiente '%s', e você validou contra '%s'"
                          % (amb_cen, env["ambiente"]))
        grupos = {"fluxos": ids_fl, "condicoes": ids_cond, "nos": ids_no, "sem_efeito": ids_se}
        for c in lista:
            for g, validos in grupos.items():
                for k in (c.get("aplica", {}).get(g, {})):
                    if k not in validos:
                        problemas.append("cenário '%s': %s inexistente '%s'" % (c["id"], g, k))
            if not c.get("aplica"):
                avisos.append("cenário '%s' não altera nada" % c["id"])

    return imprimir_check(args.ambiente, problemas, avisos)


def imprimir_check(arquivo, problemas, avisos):
    print("%s%s%s" % (CINZA, arquivo, FIMC))
    for a in avisos:
        print("  %saviso%s  %s" % (AMAR, FIMC, a))
    for p in problemas:
        print("  %serro%s   %s" % (VERM, FIMC, p))
    if not problemas and not avisos:
        print("  %sok%s     sem problemas" % (VERDE, FIMC))
    elif not problemas:
        print("  %sok%s     nenhum erro, %d aviso(s)" % (VERDE, FIMC, len(avisos)))
    return 1 if problemas else 0


# ------------------------------------------------------------------ merge
def cmd_merge(args):
    """Junta coletas de pontos diferentes. Cada coletor vê só o seu lado da rede."""
    mapa = {}
    for m in (args.mapear or []):
        if "=" not in m:
            sys.exit("--mapear espera velho=novo, recebi: %s" % m)
        k, v = m.split("=", 1)
        mapa[k] = v

    juntos = {"schema": "gemeo-citrix-observado/1.2", "origens": [], "alvos": {},
              "dns": {}, "tls": {}, "fluxos": {}, "medicoes": {},
              "condicoes": {}, "sessoes": None}
    coletas, avisos = [], []
    pior = {"ok": 0, "degradado": 1, "nao-testado": 1, "sem-registro-a": 2,
            "nao-resolvido": 2, "bloqueado": 3}

    for caminho in args.arquivos:
        with open(caminho, encoding="utf-8") as fp:
            o = json.load(fp)
        origem = o.get("origem", os.path.basename(caminho))
        ponto = origem.split("/")[1].strip() if "/" in origem else origem
        juntos["origens"].append(origem)
        coletas.append(o.get("coletado_em"))

        for secao in ("alvos", "dns", "tls"):
            for k, v in (o.get(secao) or {}).items():
                juntos[secao][mapa.get(k, k)] = v

        for k, v in (o.get("fluxos") or {}).items():
            fid = mapa.get(k, k)
            ips = (o.get("dns") or {}).get(k, {}).get("ips") or []
            med = dict(v, ponto=ponto, origem=origem, ip=(ips[0] if ips else None))
            juntos["medicoes"].setdefault(fid, []).append(med)

            atual = juntos["fluxos"].get(fid)
            if atual is None:
                juntos["fluxos"][fid] = med
            else:
                # duas medições do mesmo fluxo não se sobrescrevem: fica a pior,
                # e o RTT mais alto, porque é a que o modelo tem que respeitar
                avisos.append("'%s' medido em mais de um ponto" % fid)
                if pior.get(med.get("estado"), 9) > pior.get(atual.get("estado"), 9):
                    juntos["fluxos"][fid] = med
                elif (med.get("rtt_ms") or 0) > (atual.get("rtt_ms") or 0):
                    juntos["fluxos"][fid] = med

        for k, v in (o.get("condicoes") or {}).items():
            juntos["condicoes"][k] = bool(juntos["condicoes"].get(k)) or bool(v)
        if o.get("sessoes"):
            juntos["sessoes"] = o["sessoes"]

    if args.anonimizar:
        n = anonimizar(juntos)
        if n:
            avisos.append("%d identificador(es) de tenant mascarado(s)" % n)

    validas = [c for c in coletas if c]
    juntos["coletado_em"] = max(validas) if validas else None
    juntos["origem"] = " + ".join(juntos["origens"])
    if validas and len(set(validas)) > 1:
        juntos["janela"] = {"primeira": min(validas), "ultima": max(validas)}

    with open(args.saida, "w", encoding="utf-8") as fp:
        json.dump(juntos, fp, indent=2, ensure_ascii=False)

    print("%s%s%s" % (CINZA, args.saida, FIMC))
    print("  %d coleta(s) · %d fluxo(s)" % (len(args.arquivos), len(juntos["fluxos"])))
    for fid in sorted(juntos["fluxos"]):
        for m in juntos["medicoes"][fid]:
            c = VERDE if m.get("estado") == "ok" else (AMAR if m.get("estado") == "nao-testado" else VERM)
            rtt = "%5d ms" % m["rtt_ms"] if m.get("rtt_ms") is not None else "        "
            print("    %s%-14s%s %-14s %s  %s%s%s"
                  % (c, fid, FIMC, m.get("estado", "?"), rtt, CINZA, m.get("ponto", ""), FIMC))
    for a in sorted(set(avisos)):
        print("  %saviso%s  %s" % (AMAR, FIMC, a))
    if juntos.get("janela"):
        print("  %saviso%s  as coletas não são do mesmo instante" % (AMAR, FIMC))
    return 0


def anonimizar(estado):
    """Mascara o identificador de tenant nos FQDN, sem tocar nas medições."""
    trocas = 0
    padrao = re.compile(r"\b[a-z0-9]{8,16}(\.xendesktop\.net)\b", re.I)
    def limpar(txt):
        nonlocal trocas
        novo, n = padrao.subn(r"<customer-id>\1", txt)
        trocas += n
        return novo
    for secao in ("alvos", "dns", "tls", "fluxos", "medicoes"):
        bloco = estado.get(secao) or {}
        for k, v in list(bloco.items()):
            if isinstance(v, str):
                bloco[k] = limpar(v)
            elif isinstance(v, dict):
                for kk, vv in list(v.items()):
                    if isinstance(vv, str):
                        v[kk] = limpar(vv)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        for kk, vv in list(item.items()):
                            if isinstance(vv, str):
                                item[kk] = limpar(vv)
    return trocas


# ------------------------------------------------------------------ eval
def montar_estado(args):
    est = {"fluxos": {}, "condicoes": {}, "nos": {}, "sem_efeito": {}}
    def par(lista, grupo, ligado_por_padrao):
        for item in lista or []:
            if "=" in item:
                k, v = item.split("=", 1)
                val = v.lower() in ("on", "true", "1", "sim", "up")
            else:
                k, val = item, ligado_por_padrao
            est[grupo][k] = val
    par(args.fluxo, "fluxos", False)      # --fluxo edt-dc  => bloqueia
    par(args.cond, "condicoes", True)     # --cond tls      => liga a condição
    par(args.no, "nos", False)            # --no workspace  => derruba o componente
    par(args.regra, "sem_efeito", True)
    if getattr(args, "perfil", None):
        est["perfil"] = args.perfil
    return est


def carregar_json_opcional(caminho):
    if not caminho:
        return None
    with open(caminho, encoding="utf-8") as fp:
        return json.load(fp)


def carregar_observado(args):
    return carregar_json_opcional(getattr(args, "observado", None))


def carregar_extensao(args):
    return carregar_json_opcional(getattr(args, "extensao", None))


def cmd_eval(args):
    env = json.load(open(args.ambiente, encoding="utf-8"))
    saida = rodar_motor(env, montar_estado(args), "avaliar()", args.html,
                        carregar_observado(args), carregar_extensao(args))
    if args.json:
        print(json.dumps(resumo_perfis(saida), ensure_ascii=False, indent=2))
        return 0
    for p in resumo_perfis(saida):
        c = COR[p["nivel"]]
        print("%s%-22s %-5s%s  %s" % (c, p["perfil"], p["nivel"], FIMC, p["titulo"]))
        for eid, st in p["estagios"].items():
            cam = "  [%s]" % st["caminho"] if st["caminho"] else ""
            print("    %s%-14s%s %s%s" % (CINZA, eid, FIMC, st["status"], cam))
        if p["fatores"]:
            print("    %s%-14s%s %s" % (CINZA, "fatores", FIMC, ", ".join(p["fatores"])))
    return 0


# ------------------------------------------------------------------ sweep
def cmd_sweep(args):
    env = json.load(open(args.ambiente, encoding="utf-8"))
    sw = rodar_motor(env, montar_estado(args),
                     "calcularVarredura(%d)" % args.profundidade, args.html,
                     carregar_observado(args), carregar_extensao(args))
    itens = sw["itens"]
    unico = [x for x in itens if len(x["eventos"]) == 1]

    print("%s%d combinações · base %s%s\n" % (CINZA, sw["total"], sw["base"], FIMC))

    def bloco(titulo, filtro, cor):
        sel = [x for x in unico if filtro(x)]
        if not sel:
            return
        print("%s%s%s" % (cor, titulo, FIMC))
        for x in sel:
            ev = x["eventos"][0]
            det = ""
            if len(x.get("porPerfil") or []) > 1:
                det = "  " + CINZA + " · ".join(
                    "%s: %s" % (p["nome"], p["titulo"]) for p in x["porPerfil"]) + FIMC
            print("  %-28s %s%s" % (ev["ref"], ev["rotulo"][:46], det))
        print()

    bloco("um evento derruba", lambda x: x["nivel"] == "fail", VERM)
    bloco("um evento degrada sem gerar erro", lambda x: x["nivel"] == "deg", AMAR)
    bloco("sem efeito no modelo", lambda x: x["nivel"] == "ok", CINZA)

    if args.profundidade >= 2:
        duplas = [x for x in itens if len(x["eventos"]) == 2]
        f2 = len([x for x in duplas if x["nivel"] == "fail"])
        d2 = len([x for x in duplas if x["nivel"] == "deg"])
        o2 = len([x for x in duplas if x["nivel"] == "ok"])
        emg = [x for x in duplas if x.get("emergente")]
        print("%sduplas: %d derrubam · %d degradam · %d sem efeito%s" % (CINZA, f2, d2, o2, FIMC))
        if emg:
            print("\n%semergentes: a dupla é pior do que cada evento sozinho (%d)%s"
                  % (VERM, len(emg), FIMC))
            for x in sorted(emg, key=lambda x: x["nivel"] != "fail"):
                det = " · ".join("%s: %s" % (p["nome"], p["titulo"]) for p in x["porPerfil"])
                print("  %s%-5s%s %-44s %s%s%s" % (COR[x["nivel"]], x["nivel"], FIMC,
                      "  +  ".join(e["ref"] for e in x["eventos"]), CINZA, det, FIMC))
        if args.mostrar_duplas:
            print()
            for x in duplas:
                if x["nivel"] == "ok":
                    continue
                print("  %s%-5s%s %s" % (COR[x["nivel"]], x["nivel"], FIMC,
                                         "  +  ".join(e["ref"] for e in x["eventos"])))
    return 0


# ------------------------------------------------------------------ test
def cmd_test(args):
    suite = json.load(open(args.suite, encoding="utf-8"))
    base = os.path.dirname(os.path.abspath(args.suite))
    env = json.load(open(os.path.join(base, suite["ambiente"]), encoding="utf-8"))

    falhas = 0
    for caso in suite["casos"]:
        estado = {"fluxos": {}, "condicoes": {}, "nos": {}, "sem_efeito": {}}
        estado.update(caso.get("aplica", {}))
        # no teste, descarte de hipótese é resultado esperado e é conferido por "descartadas"
        saida = rodar_motor(env, estado, "avaliar()", args.html,
                            caso.get("observado"), caso.get("extensao"), ecoar=False)
        obtido = {p["perfil"]: p for p in resumo_perfis(saida)}

        erros = []
        if "descartadas" in caso:
            # quais hipóteses a validação recusou; a ordem não importa
            real = rodar_motor(env, estado, "_ext.descartadas.map(d => d.id)", args.html,
                               caso.get("observado"), caso.get("extensao"), ecoar=False)
            if sorted(real) != sorted(caso["descartadas"]):
                erros.append("descartadas esperadas %s, obtidas %s" % (sorted(caso["descartadas"]), sorted(real)))
        if "varredura_inclui" in caso:
            # a hipótese precisa virar evento da varredura, senão ninguém descobre se ela é ponto único de falha
            evs = rodar_motor(env, estado, "eventosPossiveis().map(e => e.id)", args.html,
                              caso.get("observado"), caso.get("extensao"), ecoar=False)
            faltam = [e for e in caso["varredura_inclui"] if e not in evs]
            if faltam:
                erros.append("fora da varredura: %s" % ", ".join(faltam))
        for perfil, esperado in caso["espera"].items():
            got = obtido.get(perfil)
            if not got:
                erros.append("perfil '%s' não existe no modelo" % perfil)
                continue
            if "nivel" in esperado and got["nivel"] != esperado["nivel"]:
                erros.append("%s: nível esperado %s, obtido %s"
                             % (perfil, esperado["nivel"], got["nivel"]))
            if "composta" in esperado and got["composta"] != esperado["composta"]:
                erros.append("%s: degradação composta esperada %s, obtida %s (fatores: %s)"
                             % (perfil, esperado["composta"], got["composta"],
                                ", ".join(got["fatores"]) or "nenhum"))
            for eid, st in (esperado.get("estagios") or {}).items():
                real = got["estagios"].get(eid, {}).get("status")
                if real != st:
                    erros.append("%s/%s: esperado %s, obtido %s" % (perfil, eid, st, real))

        if erros:
            falhas += 1
            print("%sfalhou%s  %s" % (VERM, FIMC, caso["nome"]))
            for e in erros:
                print("         %s" % e)
        elif not args.quiet:
            print("%spassou%s  %s" % (VERDE, FIMC, caso["nome"]))

    total = len(suite["casos"])
    print("\n%d de %d caso(s) passaram" % (total - falhas, total))
    return 1 if falhas else 0


# ------------------------------------------------------------------ cli
def main():
    ap = argparse.ArgumentParser(description="Ferramenta do gêmeo digital Citrix.")
    ap.add_argument("--html", default=HTML_PADRAO, help="painel de onde extrair o motor")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="valida os arquivos de ambiente e cenários")
    c.add_argument("ambiente")
    c.add_argument("--cenarios")
    c.set_defaults(func=cmd_check)

    def estado_args(p):
        p.add_argument("--fluxo", action="append", metavar="ID[=on|off]", help="bloqueia um fluxo")
        p.add_argument("--cond", action="append", metavar="ID[=on|off]", help="liga uma condição")
        p.add_argument("--no", action="append", metavar="ID[=on|off]", help="derruba um componente")
        p.add_argument("--regra", action="append", metavar="ID[=on|off]")
        p.add_argument("--observado", metavar="ARQUIVO",
                       help="coleta (ex.: coletas/atual.json) aplicada antes dos demais ajustes")
        p.add_argument("--extensao", metavar="ARQUIVO",
                       help='condições hipotéticas {"condicoes":[{"id":"hip-..."}]}, validadas e ligadas')

    m = sub.add_parser("merge", help="junta coletas de pontos diferentes")
    m.add_argument("arquivos", nargs="+")
    m.add_argument("--saida", default="estado-observado.json")
    m.add_argument("--mapear", action="append", metavar="VELHO=NOVO",
                   help="renomeia o id de um fluxo, quando o coletor usou id de outro ambiente")
    m.add_argument("--anonimizar", action="store_true",
                   help="mascara o identificador de tenant nos FQDN, para material publico")
    m.set_defaults(func=cmd_merge)

    e = sub.add_parser("eval", help="avalia um estado")
    e.add_argument("ambiente")
    estado_args(e)
    e.add_argument("--perfil")
    e.add_argument("--json", action="store_true")
    e.set_defaults(func=cmd_eval)

    v = sub.add_parser("sweep", help="varredura exaustiva")
    v.add_argument("ambiente")
    estado_args(v)
    v.add_argument("--profundidade", type=int, default=1, choices=[1, 2])
    v.add_argument("--mostrar-duplas", action="store_true")
    v.set_defaults(func=cmd_sweep)

    t = sub.add_parser("test", help="roda a suíte de casos esperados")
    t.add_argument("suite")
    t.add_argument("--quiet", action="store_true")
    t.set_defaults(func=cmd_test)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()

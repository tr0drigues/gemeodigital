# Gêmeo digital — Citrix Rendezvous V2 + EDT

Modelo executável do caminho de rede entre o VDA, o Citrix Cloud, o Gateway Service
e o endpoint do usuário. Responde o que acontece com a sessão quando uma regra de
firewall muda, quando um componente cai ou quando a rede degrada.

O motor calcula, o coletor mede e a IA só interpreta. Quem decide o desfecho é
sempre o motor.

---

## Começar

    ./iniciar.sh

O `iniciar.sh` valida o modelo, roda a suíte de testes, sobe um servidor local e abre
o navegador já com tudo carregado. Servidor estático apenas: não há backend nem API,
e nada sai da máquina além das chamadas que você fizer à IA.

Variáveis aceitas:

    PORTA=8788 ./iniciar.sh
    OBSERVADO=coletas/2026-09-14_1545.json ./iniciar.sh
    ATUALIZAR=0 ./iniciar.sh                      # desliga a releitura automática
    AMBIENTE=ambiente-rendezvous-v2.json CENARIOS=cenarios-rendezvous-v2.json ./iniciar.sh

Precisa de `python3`. Com `node` instalado, o `iniciar.sh` também valida o motor.

---

## Os arquivos

### Painel e motor

| arquivo | o que é |
|---|---|
| `gemeo-citrix.html` | o painel inteiro. O motor mora aqui, entre os marcadores `MOTOR:INICIO` e `MOTOR:FIM` |
| `iniciar.sh` | valida, sobe o servidor e abre o navegador |

### Modelo

| arquivo | o que é |
|---|---|
| `ambiente-continuidade.json` | modelo principal. Separa sessão nova de sessão em andamento e cobre service continuity |
| `ambiente-rendezvous-v2.json` | modelo do caminho de rede, sem os perfis de ciclo de vida |
| `cenarios-continuidade.json` | cenários de falha do modelo principal |
| `cenarios-rendezvous-v2.json` | cenários do modelo base |
| `casos-continuidade.json` | suíte de casos esperados, usada pelo `twin.py test` |

Cenários ficam separados da topologia de propósito: a topologia é revisada pela
arquitetura, o cenário é de quem está investigando.

### Ferramentas

| arquivo | o que é | onde roda |
|---|---|---|
| `twin.py` | linha de comando: `check`, `eval`, `sweep`, `test`, `merge` | sua máquina |
| `coletor-citrix.py` | mede o caminho de rede e grava o estado observado | no VDA e numa máquina da rede do cliente |
| `agente.py` | coleta em laço e publica `coletas/atual.json` | onde o coletor roda |
| `echo-udp.py` | refletor UDP; sem ele o UDP não é medido | num host fora da rede testada |
| `rede.sh` | degrada ou bloqueia a rede local, para exercitar o modelo |

O `twin.py` não reimplementa o motor: ele extrai o JavaScript de dentro do HTML e
executa no Node. Existe uma implementação só, e é a mesma que o painel usa.

### Dados

| arquivo | o que é |
|---|---|
| `coletas/` | histórico de estados observados. `atual.json` é o corrente, que o painel lê |
| `coletas/index.json` | índice das coletas com data, pontos e contagem de falhas |
| `roteiro.json` | sequência de estados para o agente reproduzir sem tocar na rede |
| `credenciais.local.json` | chave do OpenRouter. Está no `.gitignore` e nunca vai para o repositório |

---

## Como o painel se organiza

**Veredito** — um bloco por perfil de ciclo de vida. Sessão nova depende de registro,
autorização e transporte; sessão em andamento depende só do transporte. Por isso o
portal do usuário pode cair sem derrubar quem já está conectado.

**Fluxo de rede** — o diagrama, pintado pelo perfil selecionado. Verde em uso, âmbar
em uso degradado, cinza liberado e ocioso, vermelho bloqueado.

**Validar uma mudança** — você descreve a mudança em texto e a IA traduz para o estado
do modelo. Os toggles mexem sozinhos, o motor recalcula e só então a IA escreve a
leitura.

Quando a pergunta descreve uma causa que o modelo não tem, mas cujo efeito direto é
bloquear ou degradar fluxos que já existem (SD-WAN sem UDP, firewall novo, QoS, proxy
novo), a IA declara uma **hipótese**: uma condição `hip-*` com o que ela bloqueia ou
degrada e o porquê. O motor valida a hipótese antes de ela existir (id, zona, fluxos
reais, justificativa; no máximo 5; só acrescenta, nunca altera o que foi revisado) e
calcula o desfecho, inclusive em cadeia com o resto do modelo. O painel mostra o
veredito sem e com a hipótese, e entrega a condição já no esquema do ambiente, com um
caso de teste, para quem for levar à topologia.

A IA declara o efeito; o motor decide o desfecho. Se a hipótese estiver errada, o
veredito também está — por isso ela aparece separada, em violeta, e a análise é
instruída a não usá-la como evidência.

A hipótese também pode trazer **componente e fluxo novos** (um proxy, um segundo
Cloud Connector, uma VPN até o VDA). O fluxo novo entra num estágio que já existe, e a
preferência decide o papel dele: na mesma preferência de um caminho, é dependência a
mais (um proxy inline: se ele cai, o caminho cai); numa preferência nova, é caminho
alternativo (a VPN que segura a sessão quando o Gateway de dados cai). Componente sem
fluxo novo é descartado, porque não mudaria nada no motor. O componente hipotético entra
no diagrama, nos controles e na varredura — é ali que se descobre se ele vira ponto
único de falha.

A hipótese só acrescenta: não remove nem redireciona fluxo existente. Um proxy posto no
meio de um fluxo que já existe vira dependência adicional, e o fluxo antigo continua.

A tradução decompõe a mudança no vocabulário do motor (componente, fluxo, condição,
preferência) sem depender de exemplos de cenário. O teste que decide é o de
disponibilidade: se a coisa nova ficar fora do ar, alguma etapa para de funcionar? Então
é dependência e vira componente e fluxo — inclusive quando uma premissa diz que aquilo
"não está modelado", porque premissa descreve o escopo da topologia revisada, e
hipótese existe para ir além dele. Trocar AD por Okta, por exemplo, vira o Okta como
dependência da autorização; federação, MFA e provisionamento ficam fora.

Dois passos automáticos completam a tradução, e cada um custa uma chamada a mais só
quando é preciso:

- **2ª passada**: o que ficou fora do modelo volta à IA, que tenta extrair o que cabe
  no vocabulário e reescreve só o que continua fora.
- **autocorreção**: hipótese recusada pelo validador volta à IA uma vez, com o motivo.
  O que não for corrigido aparece com o porquê.

O painel mostra as duas etapas em "como a tradução chegou aqui". Só fica fora o que
muda a regra de avaliação (estágio novo, lógica de decisão) ou o funcionamento interno
de algo sem mudar do que ele depende — validade de token, por exemplo.

**Fatos do motor** — o que está bloqueado ou degradado, por qual causa e em qual zona.
Calculado, sem texto escrito à mão.

**Evidência medida** — o que veio do coletor. Sem coleta carregada, ele diz na cara que
tudo na tela é o modelo respondendo ao que você marcou, não o ambiente real.

**Análise** — a IA. Fica vazia até você mandar analisar.

**Varredura de falhas** — testa todas as combinações de um ou dois eventos contra o
motor e separa o que derruba, o que degrada sem gerar erro e o que não muda nada.

---

## Testes

### Validar o modelo

    python3 twin.py check ambiente-continuidade.json --cenarios cenarios-continuidade.json
    python3 twin.py test casos-continuidade.json

O `check` procura referência quebrada, id repetido, estágio sem fluxo, nó órfão e
cenário apontando para coisa que não existe. O `test` roda os casos esperados contra
o motor. Os dois devolvem código de saída diferente de zero quando falham, então dá
para pendurar num hook de commit.

### Avaliar um estado

    python3 twin.py eval ambiente-continuidade.json --no workspace
    python3 twin.py eval ambiente-continuidade.json --fluxo edt-cli=off
    python3 twin.py eval ambiente-continuidade.json --cond inspecao-tls=on
    python3 twin.py eval ambiente-continuidade.json --observado coletas/atual.json
    python3 twin.py eval ambiente-continuidade.json --extensao hipotese.json
    python3 twin.py sweep ambiente-continuidade.json --extensao hipotese.json

O arquivo de extensão tem o mesmo formato que a IA devolve:
`{"nos": [...], "fluxos": [...], "condicoes": [...]}`, com ids `hip-*`. Os casos de
`casos-continuidade.json` que começam com "hipotese" servem de exemplo.

O `--observado` aplica a coleta pelo mesmo `aplicarObservado()` do painel, e os
demais ajustes entram por cima. O `agente.py` usa esse caminho para o veredito, então
painel, agente e linha de comando não têm como discordar.

Quando dois ou mais fatores de degradação pesam sobre os fluxos em uso, o título ganha
"N fatores somados". O nível continua `deg`: é aviso de risco, não veredito. O limite
fica em `degradacao_composta` no ambiente.

### Varredura

    python3 twin.py sweep ambiente-continuidade.json
    python3 twin.py sweep ambiente-continuidade.json --profundidade 2 --mostrar-duplas

Na profundidade 2 a varredura separa as **duplas emergentes**: as que são piores, em
algum perfil, do que cada evento sozinho. É onde mora o risco que a varredura simples
esconde — lease vencido só morde com o portal fora; UDP fechado no cliente só derruba
com TCP fechado no datacenter; inspeção TLS tira o fallback TCP em silêncio e qualquer
perda de UDP passa a derrubar quem já está conectado. O painel lista essas primeiro.

---

## Simular

Os comandos abaixo mexem no firewall da sua máquina. **Ensaie o desfazer antes de
precisar dele.**

### Latência

Dois comandos, e os dois são necessários: o `dnctl` cria o pipe, o `pfctl` manda o
tráfego para dentro dele.

    sudo dnctl pipe 1 config delay 200
    echo "dummynet out proto {tcp,udp} to any port 443 pipe 1" | sudo pfctl -f - -E

Desfazer:

    sudo pfctl -d
    sudo dnctl -q flush
    sudo pfctl -f /etc/pf.conf

Ou, pelo script:

    sudo ./rede.sh latencia 200
    sudo ./rede.sh normal

Só contam para o limiar os fluxos listados em `limiares.rtt_ms.fluxos` — os que vão
até o Gateway. RTT alto do registro no Citrix Cloud não liga latência de sessão.

**O que esperar.** O `delay` é por sentido e a regra é só de saída, então o RTT sobe
cerca de 200 ms, não 400. O limiar do modelo é 150 ms — passando dele, a condição
`latencia-alta` liga sozinha e o painel vai de íntegro para degradado, sem você tocar
em nenhum toggle.

### Perda de pacote

    sudo dnctl pipe 1 config plr 0.05
    echo "dummynet out proto {tcp,udp} to any port 443 pipe 1" | sudo pfctl -f - -E

Ou `sudo ./rede.sh perda 5`.

**O modelo não enxerga perda.** O coletor mede o tempo de conexão, não a taxa de
descarte. A sessão degrada de verdade, mas o painel não reporta. Latência é o que
vira condição hoje.

### Bloquear UDP 443

    echo "block drop out proto udp to any port 443" | sudo pfctl -f - -E

Desfazer:

    sudo pfctl -d
    sudo pfctl -f /etc/pf.conf

Ou `sudo ./rede.sh bloqueia-udp` e `sudo ./rede.sh normal`.

**Para isto aparecer no painel, o UDP precisa estar sendo medido.** Sem refletor, o
coletor marca `nao-testado` e o campo nem entra no arquivo — foi por isso que o
bloqueio não mudou nada da primeira vez. Suba o refletor antes:

    sudo python3 echo-udp.py

E aponte o coletor para ele com `--refletor-udp`. Com refletor em loopback ou rede
privada o coletor marca a medição com aviso: vale para exercitar o modelo, não prova
que a saída UDP 443 até o Gateway está aberta.

### Conferir o que está ativo

    sudo dnctl list
    sudo pfctl -s info | head -2
    sudo pfctl -s rules
    ./rede.sh status

O `-E` do `pfctl` conta referências: se você rodou o comando de ligar mais de uma vez,
precisa de um `pfctl -d` para cada vez, até o `pfctl -s info` dizer `Status: Disabled`.
Se algo ficar estranho, reiniciar o Mac devolve tudo — nada disso persiste.

---

## Ciclo completo com medição ao vivo

Quatro terminais.

**1 · refletor UDP**

    sudo python3 echo-udp.py

**2 · painel**

    ./iniciar.sh

**3 · agente medindo de verdade**

    python3 agente.py --real --intervalo 10 \
      --customer-id SEU_CCID \
      --alvo ctl-gw=global.g.nssvc.net \
      --refletor-udp 127.0.0.1

**4 · exercitar**

    sudo ./rede.sh latencia 200
    sudo ./rede.sh normal
    sudo ./rede.sh bloqueia-udp
    sudo ./rede.sh normal

Espere dois ciclos do agente entre um comando e outro para o painel reagir.

### Sem depender da rede

O agente também reproduz estados já gravados, sem tocar em nada:

    python3 agente.py --roteiro roteiro.json --intervalo 15 --repetir

Nesse modo os terminais 1 e 4 ficam de fora. É a opção quando a conexão não pode ser
variável de risco.

---

## Coletar do ambiente real

O modelo tem duas pontas saindo para o mesmo Gateway, e o EDT só sobe com UDP aberto
nas duas. Um coletor só enxerga um lado.

No VDA:

    python3 coletor-citrix.py --customer-id SEU_CCID \
        --alvo ctl-gw=global.g.nssvc.net \
        --refletor-udp echo.seudominio.com \
        --saida estado-dc.json

Numa máquina da rede do cliente, pela mesma rota de saída do usuário:

    python3 coletor-citrix.py --alvo tcp-cli=global.g.nssvc.net \
        --refletor-udp echo.seudominio.com \
        --saida estado-cliente.json

Juntar:

    python3 twin.py merge estado-dc.json estado-cliente.json \
        --mapear ctl=ctl-gw --mapear reg=reg-cloud \
        --anonimizar --saida coletas/atual.json

O coletor grava ids genéricos (`ctl`, `reg`, `tls`, `proxy-tcp`). Cada ambiente
traduz esses ids em `aliases_observados`; para qualquer outro, o id do `--alvo` precisa
bater com o id de fluxo do ambiente. Na evidência medida, fluxo sem correspondência
aparece como "fora do modelo". O
`--anonimizar` mascara o identificador de tenant nos FQDN, para material que vai
virar público.

### Sobre os alvos

Os requisitos de firewall da Citrix são wildcards, e wildcard não resolve:

- `*.*.nssvc.net` tem dois níveis de curinga. Um host real e resolvível do Gateway
  Service é `global.g.nssvc.net`.
- `*.xendesktop.net` responde sem registro para subdomínio inexistente. O alvo
  testável é `<CCID>.xendesktop.net`, com o Customer ID do Citrix Cloud.

Para descobrir qual PoP este host está usando de fato, com uma sessão aberta:

    python3 coletor-citrix.py --descobrir

---

## IA

Copie `credenciais.local.json.exemplo` para `credenciais.local.json`:

    { "openrouter_key": "sk-or-v1-...", "modelo": "anthropic/claude-sonnet-5" }

O painel carrega esse arquivo ao abrir. Ele está no `.gitignore`, e o
`gemeo-citrix.html` nunca contém a chave. Sem o arquivo, dá para digitar no painel em
*Fontes e integrações* — aí a chave fica só na memória da aba.

A IA faz duas traduções e nenhuma decisão: texto para estado, e resultado para texto.
Ela não pode contradizer o veredito do motor, não pode citar número que não esteja no
contexto, e cada causa que ela levanta precisa declarar em que dado se apoia.

---

## Limites do modelo

As premissas estão no topo de cada `ambiente-*.json`. As do modelo de continuidade:
máquinas AD domain joined, acesso via StoreFront Cloud e Gateway Service, Session
Reliability habilitada, autenticação federada no IdP não modelada, e o mesmo protocolo
de transporte nas duas pernas (EDT numa e TCP na outra conta como sem caminho — a
hipótese que mais vale validar em laboratório).

O modelo ainda não foi confrontado com laboratório. Os casos em
`casos-continuidade.json` dizem que ele é coerente consigo mesmo, não que corresponde
ao ambiente. Quando um teste real contradisser um caso, corrija o modelo e o caso
junto, para não regredir.

Pergunta aberta que não traduz para o estado é informação útil: cada uma delas é uma
coisa que o modelo deveria representar e não representa.

# Passo a passo

## 1. Sobe o gêmeo

```
./iniciar.sh
```

## 2. Sobe o refletor UDP

```
sudo python3 echo-udp.py
```

## 3. Inicia o coletor ao vivo

```
python3 agente.py --real --intervalo 10 --customer-id SEU_CCID --alvo ctl-gw=global.g.nssvc.net --refletor-udp 127.0.0.1
```

## 4. Aplica latência

```
sudo dnctl pipe 1 config delay 200
echo "dummynet out proto {tcp,udp} to any port 443 pipe 1" | sudo pfctl -f - -E
```

## 5. Remove a latência

```
sudo pfctl -d
sudo dnctl -q flush
sudo pfctl -f /etc/pf.conf
```

## 6. Bloqueia o UDP

```
echo "block drop out proto udp to any port 443" | sudo pfctl -f - -E
```

## 7. Devolve o UDP

```
sudo pfctl -d
sudo pfctl -f /etc/pf.conf
```

## 8. Encerra

```
ctrl+c nos terminais 1, 2 e 3
```

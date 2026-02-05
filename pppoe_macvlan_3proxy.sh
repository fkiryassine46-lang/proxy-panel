#!/bin/bash

# ==============================================================================
# OPTIMISATIONS SYSTÈME (Indispensable pour 2000+)
# ==============================================================================
sysctl -w net.ipv4.neigh.default.gc_thresh1=2048 >/dev/null 2>&1
sysctl -w net.ipv4.neigh.default.gc_thresh2=4096 >/dev/null 2>&1
sysctl -w net.ipv4.neigh.default.gc_thresh3=8192 >/dev/null 2>&1
ulimit -n 500000

# ==============================================================================
# 0. LOGIQUE DE LICENCE (Images 1 & 2)
# ==============================================================================
BAR_WIDTH=30
BAR_CHAR="#"
GREEN='\e[32m'
RED='\e[31m'
RESET='\e[0m'

TMP_OUT=$(mktemp)
/usr/local/bin/check_license.sh --no-reboot >"$TMP_OUT" 2>&1 &
LIC_PID=$!

progress=0
printf "Checking licence... ${GREEN}[%*s]${RESET}" "$BAR_WIDTH" ""
while kill -0 "$LIC_PID" 2>/dev/null; do
    if [ "$progress" -lt "$BAR_WIDTH" ]; then progress=$((progress + 1)); fi
    filled=$(printf "%*s" "$progress" "" | tr ' ' "$BAR_CHAR")
    empty=$(printf "%*s" "$((BAR_WIDTH - progress))" "")
    printf "\rChecking licence... ${GREEN}[%s%s]${RESET}" "$filled" "$empty"
    sleep 0.1
done
wait "$LIC_PID" || exit 1
rm -f "$TMP_OUT"

# ==============================================================================
# 1. PARAMÈTRES (Images 3, 4, 5)
# ==============================================================================
DEFAULT_COUNT=3000
echo
read -r -p "How many proxies? [default: ${DEFAULT_COUNT}]: " USER_COUNT
COUNT=${USER_COUNT:-$DEFAULT_COUNT}

WAN_IF="ens160"
BASE_PORT1=30000
BASE_PORT2=60000
MAX_PER_INSTANCE=1000
TABLE_BASE=4000
PPP_USER="centre04"
PPP_PASS="centre04"
LISTEN_IP=$(ip -4 addr show dev "$WAN_IF" | awk '/inet / {print $2}' | cut -d/ -f1 | head -n1)

PROXY_LIST_RAW="/root/proxies_raw.txt"
PROXY_LIST_FILE="/root/proxies.txt"
THREEPROXY_BIN="/usr/local/bin/3proxy"
THREEPROXY_CFG1="/usr/local/etc/3proxy/3proxy1.cfg"
THREEPROXY_CFG2="/usr/local/etc/3proxy/3proxy2.cfg"

# ==============================================================================
# 4. NETTOYAGE & CONFIG (Image 7, 8, 9)
# ==============================================================================
pkill -9 3proxy || true
pkill -9 pppd || true
for i in $(seq 0 $((COUNT + 100))); do ip link delete "macvlan$i" 2>/dev/null || true; done
: > "$PROXY_LIST_RAW"

mkdir -p /etc/ppp/peers /usr/local/etc/3proxy /var/log/3proxy

# Config 3proxy avec maxconn augmenté (Important !)
cat >"$THREEPROXY_CFG1" <<EOF
daemon
maxconn 15000
nserver 8.8.8.8
nscache 65536
timeouts 1 5 30 60 180 1000 15 60
auth none
allow *
EOF
cp "$THREEPROXY_CFG1" "$THREEPROXY_CFG2"

# ==============================================================================
# 7. PHASE 1 : PPPOE (Image 10)
# ==============================================================================
echo "Phase 1: Starting PPPoE sessions..."
for i in $(seq 0 $((COUNT - 1))); do
    ip link add link "$WAN_IF" name "macvlan$i" type macvlan mode bridge
    ip link set "macvlan$i" up
    cat >"/etc/ppp/peers/pppoe$i" <<EOF
plugin rp-pppoe.so
macvlan$i
noauth
persist
maxfail 0
user "$PPP_USER"
password "$PPP_PASS"
unit $i
EOF
    pppd call "pppoe$i" >/dev/null 2>&1 &
    [ $(( (i+1) % 50 )) -eq 0 ] && echo " -> $i sessions..." && sleep 1
done

# ==============================================================================
# 8. PHASE 2 : ROUTAGE (Image 11)
# ==============================================================================
echo "Phase 2: Configuring Routing..."
for i in $(seq 0 $((COUNT - 1))); do
    PPP_IF="ppp$i"
    T=0
    while ! ip addr show "$PPP_IF" >/dev/null 2>&1 && [ $T -lt 15 ]; do sleep 0.5; T=$((T+1)); done
    
    LOCAL_IP=$(ip -4 addr show "$PPP_IF" 2>/dev/null | awk '/inet / {print $2}' | cut -d/ -f1)
    if [ -n "$LOCAL_IP" ]; then
        TABLE_ID=$((TABLE_BASE + i))
        ip route add default dev "$PPP_IF" table "$TABLE_ID" 2>/dev/null || true
        ip rule add from "$LOCAL_IP" table "$TABLE_ID" 2>/dev/null || true
        
        # Correction du mapping des ports
        if [ "$i" -lt "$MAX_PER_INSTANCE" ]; then
            PORT=$((BASE_PORT1 + i))
            echo "proxy -p$PORT -i$LISTEN_IP -e$LOCAL_IP" >> "$THREEPROXY_CFG1"
        else
            PORT=$((BASE_PORT2 + (i - MAX_PER_INSTANCE)))
            echo "proxy -p$PORT -i$LISTEN_IP -e$LOCAL_IP" >> "$THREEPROXY_CFG2"
        fi
        echo "$LISTEN_IP:$PORT" >> "$PROXY_LIST_RAW"
    fi
done

# ==============================================================================
# 9. LANCEMENT & CHECK FINAL
# ==============================================================================
"$THREEPROXY_BIN" "$THREEPROXY_CFG1"
[ "$COUNT" -gt "$MAX_PER_INSTANCE" ] && "$THREEPROXY_BIN" "$THREEPROXY_CFG2"
cp "$PROXY_LIST_RAW" "$PROXY_LIST_FILE"

echo -e "\n${GREEN}================ CHECK FINAL ====================${RESET}"
# 1. Vérification des processus
if pgrep 3proxy > /dev/null; then
    echo -e "3proxy status: ${GREEN}[RUNNING]${RESET}"
else
    echo -e "3proxy status: ${RED}[FAILED]${RESET}"
fi

# 2. Vérification du nombre d'interfaces UP
UP_COUNT=$(ip addr show | grep -c "ppp")
echo -e "Interfaces PPP UP: ${GREEN}$UP_COUNT / $COUNT${RESET}"

# 3. Test de connectivité (Test sur le premier et le dernier proxy)
echo "Testing connectivity..."
FIRST_PROXY=$(head -n 1 "$PROXY_LIST_FILE")
TEST=$(curl -s -x "$FIRST_PROXY" --connect-timeout 5 http://ifconfig.me || echo "FAILED")
if [ "$TEST" != "FAILED" ]; then
    echo -e "Proxy Test: ${GREEN}[OK] -> External IP: $TEST${RESET}"
else
    echo -e "Proxy Test: ${RED}[FAILED]${RESET} - Check your credentials or VMware Forged Transmits"
fi

# 4. Relance du Mini-Serveur
fuser -k 8888/tcp >/dev/null 2>&1 || true
nohup python3 -m http.server 8888 --directory /root > /dev/null 2>&1 &
echo -e "Mini-Server: ${GREEN}[ONLINE]${RESET} at http://$LISTEN_IP:8888/proxies.txt"
echo -e "${GREEN}=================================================${RESET}"

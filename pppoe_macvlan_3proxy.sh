#!/bin/bash
set -euo pipefail

###########################################
# 0. Licence check with progress bar
###########################################

BAR_WIDTH=30
BAR_CHAR="#"

GREEN="\e[32m"
RED="\e[31m"
YELLOW="\e[33m"
ORANGE="\e[38;5;208m"
PINK="\e[35m"
BLUE="\e[34m"
RESET="\e[0m"

TMP_OUT=$(mktemp)

# Lance le check licence en arrière-plan
/usr/local/bin/check_license.sh --no-reboot >"$TMP_OUT" 2>&1 &
LIC_PID=$!
progress=0

printf "Checking licence... ${GREEN}[%-*s]${RESET}" "$BAR_WIDTH" ""

while kill -0 "$LIC_PID" 2>/dev/null; do
  if [ "$progress" -lt "$BAR_WIDTH" ]; then
    progress=$((progress + 1))
  fi

  filled=$(printf "%*s" "$progress" "" | tr ' ' "$BAR_CHAR")
  empty=$(printf "%*s" "$((BAR_WIDTH - progress))" "")

  printf "\rChecking licence... ${GREEN}[%s%s]${RESET}" "$filled" "$empty"
  sleep 0.1
done

if wait "$LIC_PID"; then
  LIC_RC=0
else
  LIC_RC=$?
fi

filled=$(printf "%*s" "$BAR_WIDTH" "" | tr ' ' "$BAR_CHAR")
printf "\rChecking licence... ${GREEN}[%s]${RESET}\n" "$filled"

if [ "$LIC_RC" -ne 0 ]; then
  echo
  cat "$TMP_OUT"
  rm -f "$TMP_OUT"
  exit "$LIC_RC"
fi

rm -f "$TMP_OUT"
echo

###########################################
# 1. Ask how many proxies to generate
###########################################

DEFAULT_COUNT=3000

echo
read -r -p "How many proxies do you want to generate? [default: ${DEFAULT_COUNT}]: " USER_COUNT

if [ -z "${USER_COUNT}" ]; then
  COUNT="$DEFAULT_COUNT"
elif [[ "${USER_COUNT}" =~ ^[0-9]+$ ]] && [ "${USER_COUNT}" -gt 0 ]; then
  COUNT="$USER_COUNT"
else
  echo "Invalid number, using default ${DEFAULT_COUNT}."
  COUNT="$DEFAULT_COUNT"
fi

echo
echo "Using COUNT=${COUNT} proxies."
echo

###########################################
# 2. Global configuration
###########################################

WAN_IF="ens160"

BASE_PORT1=30000
BASE_PORT2=60000
MAX_PER_INSTANCE=1000
PARALLEL_START=20

TABLE_BASE=4000

PPP_USER="centre04"
PPP_PASS="centre04"

PROXY_LIST_RAW="/root/proxies_raw.txt"
PROXY_LIST_FILE="/root/proxies.txt"

THREEPROXY_BIN="/usr/local/bin/3proxy"
THREEPROXY_CFG1="/usr/local/etc/3proxy/3proxy1.cfg"
THREEPROXY_CFG2="/usr/local/etc/3proxy/3proxy2.cfg"

###########################################
# 2.1 Kernel tuning (important for 2000+)
###########################################

# FDs
ulimit -n 200000 || true

# ARP/NEIGH for lots of macvlan
for scope in default "$WAN_IF"; do
  sysctl -w "net.ipv4.neigh.${scope}.gc_thresh1=8192"  >/dev/null 2>&1 || true
  sysctl -w "net.ipv4.neigh.${scope}.gc_thresh2=16384" >/dev/null 2>&1 || true
  sysctl -w "net.ipv4.neigh.${scope}.gc_thresh3=32768" >/dev/null 2>&1 || true
done

# rp_filter can drop replies with policy routing
sysctl -w net.ipv4.conf.all.rp_filter=0 >/dev/null 2>&1 || true
sysctl -w net.ipv4.conf.default.rp_filter=0 >/dev/null 2>&1 || true
sysctl -w "net.ipv4.conf.${WAN_IF}.rp_filter=0" >/dev/null 2>&1 || true

# Conntrack huge (if enabled)
if [ -e /proc/sys/net/netfilter/nf_conntrack_max ]; then
  sysctl -w net.netfilter.nf_conntrack_max=1048576 >/dev/null 2>&1 || true
fi

# Backlog / TCP safe tuning
sysctl -w net.core.somaxconn=4096 >/dev/null 2>&1 || true
sysctl -w net.core.netdev_max_backlog=250000 >/dev/null 2>&1 || true
sysctl -w net.ipv4.ip_local_port_range="10240 65535" >/dev/null 2>&1 || true
sysctl -w net.ipv4.tcp_fin_timeout=15 >/dev/null 2>&1 || true

###########################################
# 3. Detect local IP on WAN_IF
###########################################

LISTEN_IP=$(ip -4 addr show dev "$WAN_IF" | awk '/inet / {print $2}' | cut -d/ -f1 | head -n1)
if [ -z "$LISTEN_IP" ]; then
  echo "Unable to automatically detect IP address on ${WAN_IF}"
  exit 1
fi
echo "$(date) - Local IP on ${WAN_IF}: ${LISTEN_IP}"
echo

if [ ! -x "$THREEPROXY_BIN" ]; then
  echo "3proxy binary not found at: $THREEPROXY_BIN"
  exit 1
fi

if ! ip link show "$WAN_IF" >/dev/null 2>&1; then
  echo "Interface ${WAN_IF} not found."
  exit 1
fi

###########################################
# 4. Cleanup old configs + macvlans + rules
###########################################

echo "$(date) - Cleaning old configuration..."

pkill 3proxy >/dev/null 2>&1 || true
pkill pppd   >/dev/null 2>&1 || true

MAX_CLEAN=$((COUNT + 500))

for i in $(seq 0 "$MAX_CLEAN"); do
  ip link show "macvlan$i" >/dev/null 2>&1 && ip link delete "macvlan$i" || true
done

: > "$PROXY_LIST_RAW"

# Flush our policy tables
for i in $(seq 0 "$MAX_CLEAN"); do
  ip route flush table $((TABLE_BASE + i)) >/dev/null 2>&1 || true
done

# Remove our rules by priority range
ip rule show | awk '{print $1}' | sed 's/://g' | while read -r pref; do
  if [[ "$pref" =~ ^[0-9]+$ ]] && [ "$pref" -ge 4000 ] && [ "$pref" -le 12000 ]; then
    ip rule del pref "$pref" >/dev/null 2>&1 || true
  fi
done

###########################################
# 5. Ensure PPP secrets contain our user
###########################################

for f in /etc/ppp/chap-secrets /etc/ppp/pap-secrets; do
  if [ -f "$f" ]; then
    if ! grep -q "[[:space:]]${PPP_USER}[[:space:]]" "$f" 2>/dev/null; then
      echo "${PPP_USER} * ${PPP_PASS} *" >>"$f"
    fi
  fi
done

mkdir -p /etc/ppp/peers
mkdir -p "$(dirname "$THREEPROXY_CFG1")"
mkdir -p /var/log/3proxy

###########################################
# 6. Prepare 3proxy configs (two instances)
###########################################

cat >"$THREEPROXY_CFG1" <<'EOF1'
daemon
maxconn 8192
nserver 8.8.8.8
nserver 1.1.1.1
nscache 65536
log /var/log/3proxy/log1.log D
logformat "L%y-%m-%d %H:%M:%S %e %E %C:%c %R:%r %O %I %h %T"
timeouts 1 5 30 60 180 1000 15 60
auth none
allow *
EOF1

cat >"$THREEPROXY_CFG2" <<'EOF2'
daemon
maxconn 8192
nserver 8.8.8.8
nserver 1.1.1.1
nscache 65536
log /var/log/3proxy/log2.log D
logformat "L%y-%m-%d %H:%M:%S %e %E %C:%c %R:%r %O %I %h %T"
timeouts 1 5 30 60 180 1000 15 60
auth none
allow *
EOF2

###########################################
# 7. Phase 1 - Create PPPoE sessions
###########################################

echo "Phase 1: creating PPPoE sessions..."
echo

for i in $(seq 0 $((COUNT - 1))); do
  MACVLAN_IF="macvlan$i"

  if ! ip link add link "$WAN_IF" name "$MACVLAN_IF" type macvlan mode bridge 2>/dev/null; then
    echo "!!! Failed to create ${MACVLAN_IF}"
    continue
  fi

  ip link set "$MACVLAN_IF" up

  PEER_FILE="/etc/ppp/peers/pppoe$i"

  # IMPORTANT: nodefaultroute -> pppd NE TOUCHE PAS la table main
  cat >"$PEER_FILE" <<EOF
plugin rp-pppoe.so
$MACVLAN_IF

noauth
usepeerdns

nodefaultroute
noipdefault

mtu 1492
mru 1492

persist
maxfail 0
holdoff 5

lcp-echo-interval 30
lcp-echo-failure 10

ipcp-accept-local
ipcp-accept-remote
noccp

user "$PPP_USER"
password "$PPP_PASS"
unit $i
EOF

  pppd call "pppoe$i" >/var/log/pppoe_$i.log 2>&1 &

  if ((( (i + 1) % PARALLEL_START == 0 ))); then
    echo " -> $((i + 1)) sessions started, short pause..."
    sleep 1
  fi
done

###########################################
# 8. Phase 2 - Policy routing + 3proxy entries
###########################################

echo
echo "Phase 2: configuring policy routing + proxies..."
echo

for i in $(seq 0 $((COUNT - 1))); do
  PPP_IF="ppp$i"

  TIMEOUT=60
  while ! ip addr show "$PPP_IF" >/dev/null 2>&1 && [ "$TIMEOUT" -gt 0 ]; do
    sleep 1
    TIMEOUT=$((TIMEOUT - 1))
  done

  if ! ip addr show "$PPP_IF" >/dev/null 2>&1; then
    echo "!!! PPPoE failed: ${PPP_IF} not created"
    continue
  fi

  IP_PPP=""
  TIMEOUT_IP=30
  while [ -z "$IP_PPP" ] && [ "$TIMEOUT_IP" -gt 0 ]; do
    IP_PPP=$(ip -4 addr show dev "$PPP_IF" | awk '/inet / {print $2}' | cut -d/ -f1 | head -n1)
    [ -n "$IP_PPP" ] && break
    sleep 1
    TIMEOUT_IP=$((TIMEOUT_IP - 1))
  done

  if [ -z "$IP_PPP" ]; then
    echo "!!! Unable to get IP on ${PPP_IF}"
    continue
  fi

  TABLE_ID=$((TABLE_BASE + i))

  ip route flush table "$TABLE_ID" >/dev/null 2>&1 || true
  ip route add "$IP_PPP"/32 dev "$PPP_IF" table "$TABLE_ID" >/dev/null 2>&1 || continue
  ip route add default dev "$PPP_IF" table "$TABLE_ID" >/dev/null 2>&1 || continue

  while ip rule show | grep -q "from $IP_PPP"; do
    ip rule del from "$IP_PPP" >/dev/null 2>&1 || break
  done

  ip rule add from "$IP_PPP" table "$TABLE_ID" priority $((4000 + i)) >/dev/null 2>&1 || true

  if [ "$i" -lt "$MAX_PER_INSTANCE" ]; then
    PORT=$((BASE_PORT1 + i))
    echo "proxy -a -p${PORT} -i0.0.0.0 -e${IP_PPP}" >>"$THREEPROXY_CFG1"
  else
    INDEX2=$((i - MAX_PER_INSTANCE))
    PORT=$((BASE_PORT2 + INDEX2))
    echo "proxy -a -p${PORT} -i0.0.0.0 -e${IP_PPP}" >>"$THREEPROXY_CFG2"
  fi

  if command -v iptables >/dev/null 2>&1; then
    iptables -C INPUT -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null || \
    iptables -A INPUT -p tcp --dport "$PORT" -j ACCEPT
  fi

  echo "${IP_PPP}:${PORT}:fibre123:fibrebebe123" >>"$PROXY_LIST_RAW"
  echo " -> READY ${PPP_IF} IP ${IP_PPP} PORT ${PORT}"
done

###########################################
# 9. NEVER change global default route
###########################################
# (IMPORTANT for stability at scale)
# We do nothing here on purpose.

###########################################
# 10. Mini HTTP server to download proxies.txt
###########################################

if command -v python3 >/dev/null 2>&1; then
  (
    cd "$(dirname "$PROXY_LIST_FILE")" || exit 1
    python3 -m http.server 1991 --bind 0.0.0.0 >/dev/null 2>&1
  ) &
  echo " -> http://${LISTEN_IP}:1991/$(basename "$PROXY_LIST_FILE")"
fi

###########################################
# 11. Start 3proxy
###########################################

echo
echo "Starting 3proxy..."
if [ -s "$THREEPROXY_CFG1" ]; then
  "$THREEPROXY_BIN" "$THREEPROXY_CFG1" &
  echo " -> 3proxy instance 1 started"
fi
if [ -s "$THREEPROXY_CFG2" ]; then
  "$THREEPROXY_BIN" "$THREEPROXY_CFG2" &
  echo " -> 3proxy instance 2 started"
fi
echo

###########################################
# 12. Export list FAST (no false negatives)
###########################################

cp "$PROXY_LIST_RAW" "$PROXY_LIST_FILE"
echo "Proxy list exported to: $PROXY_LIST_FILE"
echo

###########################################
# 13. Live proxy health check loop (Google)
###########################################

while true; do
  echo '----------------------------------------------'
  echo -e " ${PINK}Proxy health check at $(date)${RESET}"
  echo -e " File: ${PINK}${PROXY_LIST_FILE}${RESET}"
  echo '----------------------------------------------'

  if [ ! -f "$PROXY_LIST_FILE" ]; then
    echo "Proxy list file ${PROXY_LIST_FILE} not found."
    break
  fi

  OK=0
  FAIL=0
  FAILED_LIST=""

  while IFS=':' read -r HOST PORT USER PASS; do
    [ -z "$HOST" ] && continue
    PROXY_URL="http://${USER}:${PASS}@${HOST}:${PORT}"

    if curl -sS --max-time 5 -x "$PROXY_URL" https://www.google.com >/dev/null 2>&1; then
      echo -e "${HOST}:${PORT} -> ${GREEN}STATUS OK${RESET}"
      OK=$((OK + 1))
    else
      echo -e "${HOST}:${PORT} -> ${RED}STATUS FAIL${RESET}"
      FAIL=$((FAIL + 1))
      FAILED_LIST+="${HOST}:${PORT}\n"
    fi
  done < "$PROXY_LIST_FILE"

  echo "----------------------------------------------"
  echo -e " ${YELLOW}Summary:${RESET} OK=${GREEN}${OK}${RESET} FAIL=${RED}${FAIL}${RESET}"
  if [ "$FAIL" -gt 0 ]; then
    echo
    echo -e "${RED}Failed proxies:${RESET}"
    printf '%b' "$FAILED_LIST"
    echo
  fi

  echo -e " ${BLUE}Next check in 60 sec... (Ctrl+C to stop)${RESET}"
  sleep 60
done

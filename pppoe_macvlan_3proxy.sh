#!/bin/bash
set -euo pipefail

###########################################
# CONFIG
###########################################

WAN_IF="ens160"

BASE_PORT1=30000
BASE_PORT2=60000
MAX_PER_INSTANCE=1000

TABLE_BASE=4000
PARALLEL_START=20

PPP_USER="centre04"
PPP_PASS="centre04"

PROXY_LIST_RAW="/root/proxies_raw.txt"
PROXY_LIST_FILE="/root/proxies.txt"

THREEPROXY_BIN="/usr/local/bin/3proxy"
THREEPROXY_CFG1="/usr/local/etc/3proxy/3proxy1.cfg"
THREEPROXY_CFG2="/usr/local/etc/3proxy/3proxy2.cfg"

# Vérifs (désactive par défaut pour éviter faux FAIL + gagner du temps)
DO_INITIAL_VERIFY=false
VERIFY_JOBS=50
VERIFY_TIMEOUT=5

###########################################
# UI COLORS
###########################################
GREEN="\e[32m"
RED="\e[31m"
YELLOW="\e[33m"
BLUE="\e[34m"
PINK="\e[35m"
RESET="\e[0m"

###########################################
# ASK COUNT
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
echo "Using COUNT=${COUNT} proxies."
echo

###########################################
# SAFETY CHECKS
###########################################
if ! ip link show "$WAN_IF" >/dev/null 2>&1; then
  echo "Interface ${WAN_IF} not found."
  exit 1
fi
if [ ! -x "$THREEPROXY_BIN" ]; then
  echo "3proxy binary not found at: $THREEPROXY_BIN"
  exit 1
fi

LISTEN_IP=$(ip -4 addr show dev "$WAN_IF" | awk '/inet / {print $2}' | cut -d/ -f1 | head -n1)
if [ -z "$LISTEN_IP" ]; then
  echo "Unable to detect IP on ${WAN_IF}"
  exit 1
fi
echo "$(date) - Local IP on ${WAN_IF}: ${LISTEN_IP}"
echo

###########################################
# KERNEL TUNING (PRO)
###########################################

# File descriptors
ulimit -n 200000 || true

# ARP/NEIGH tables (macvlan massive)
for scope in default "$WAN_IF"; do
  sysctl -w "net.ipv4.neigh.${scope}.gc_thresh1=8192"  >/dev/null 2>&1 || true
  sysctl -w "net.ipv4.neigh.${scope}.gc_thresh2=16384" >/dev/null 2>&1 || true
  sysctl -w "net.ipv4.neigh.${scope}.gc_thresh3=32768" >/dev/null 2>&1 || true
done

# rp_filter can break policy routing with many interfaces
sysctl -w net.ipv4.conf.all.rp_filter=0 >/dev/null 2>&1 || true
sysctl -w net.ipv4.conf.default.rp_filter=0 >/dev/null 2>&1 || true
sysctl -w "net.ipv4.conf.${WAN_IF}.rp_filter=0" >/dev/null 2>&1 || true

# Conntrack (important for thousands of outbound flows)
# If module exists, increase max
if [ -e /proc/sys/net/netfilter/nf_conntrack_max ]; then
  sysctl -w net.netfilter.nf_conntrack_max=1048576 >/dev/null 2>&1 || true
fi

# TCP / backlog tuning (safe)
sysctl -w net.core.somaxconn=4096 >/dev/null 2>&1 || true
sysctl -w net.core.netdev_max_backlog=250000 >/dev/null 2>&1 || true
sysctl -w net.ipv4.ip_local_port_range="10240 65535" >/dev/null 2>&1 || true
sysctl -w net.ipv4.tcp_fin_timeout=15 >/dev/null 2>&1 || true
sysctl -w net.ipv4.tcp_keepalive_time=60 >/dev/null 2>&1 || true
sysctl -w net.ipv4.tcp_keepalive_intvl=10 >/dev/null 2>&1 || true
sysctl -w net.ipv4.tcp_keepalive_probes=6 >/dev/null 2>&1 || true

###########################################
# CLEANUP
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
while ip rule show | awk '{print $1}' | grep -qE '^[0-9]+:$'; do
  break
done
ip rule show | awk '{print $1}' | sed 's/://g' | while read -r pref; do
  if [[ "$pref" =~ ^[0-9]+$ ]] && [ "$pref" -ge 4000 ] && [ "$pref" -le 12000 ]; then
    ip rule del pref "$pref" >/dev/null 2>&1 || true
  fi
done

###########################################
# PPP SECRETS
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
# 3PROXY BASE CONFIGS
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
# PHASE 1: CREATE PPP SESSIONS
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

  # IMPORTANT: nodefaultroute (NEVER touch main default route)
  cat >"$PEER_FILE" <<EOF
plugin rp-pppoe.so
$MACVLAN_IF

noauth
usepeerdns

# DO NOT touch main routing table
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
# PHASE 2: POLICY ROUTING + PROXY MAPPING
###########################################
echo
echo "Phase 2: configuring policy routing + 3proxy..."
echo

for i in $(seq 0 $((COUNT - 1))); do
  PPP_IF="ppp$i"

  # Wait PPP interface
  TIMEOUT=60
  while ! ip addr show "$PPP_IF" >/dev/null 2>&1 && [ "$TIMEOUT" -gt 0 ]; do
    sleep 1
    TIMEOUT=$((TIMEOUT - 1))
  done
  if ! ip addr show "$PPP_IF" >/dev/null 2>&1; then
    echo "!!! PPPoE failed: ${PPP_IF} not created"
    continue
  fi

  # Get IP
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

  # Flush table & install routes
  ip route flush table "$TABLE_ID" >/dev/null 2>&1 || true
  ip route add "$IP_PPP"/32 dev "$PPP_IF" table "$TABLE_ID" >/dev/null 2>&1 || continue
  ip route add default dev "$PPP_IF" table "$TABLE_ID" >/dev/null 2>&1 || continue

  # Remove old rule for same IP then add rule (unique priority per i)
  while ip rule show | grep -q "from $IP_PPP"; do
    ip rule del from "$IP_PPP" >/dev/null 2>&1 || break
  done
  ip rule add from "$IP_PPP" table "$TABLE_ID" priority $((4000 + i)) >/dev/null 2>&1 || true

  # 3proxy mapping
  if [ "$i" -lt "$MAX_PER_INSTANCE" ]; then
    PORT=$((BASE_PORT1 + i))
    echo "proxy -a -p${PORT} -i0.0.0.0 -e${IP_PPP}" >>"$THREEPROXY_CFG1"
  else
    INDEX2=$((i - MAX_PER_INSTANCE))
    PORT=$((BASE_PORT2 + INDEX2))
    echo "proxy -a -p${PORT} -i0.0.0.0 -e${IP_PPP}" >>"$THREEPROXY_CFG2"
  fi

  # Firewall (open port)
  if command -v iptables >/dev/null 2>&1; then
    iptables -C INPUT -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null || \
    iptables -A INPUT -p tcp --dport "$PORT" -j ACCEPT
  fi

  echo "${IP_PPP}:${PORT}:fibre123:fibrebebe123" >>"$PROXY_LIST_RAW"
  echo " -> READY ppp$i IP ${IP_PPP} PORT ${PORT}"
done

###########################################
# HTTP server for proxies.txt
###########################################
if command -v python3 >/dev/null 2>&1; then
  (
    cd "$(dirname "$PROXY_LIST_FILE")" || exit 1
    python3 -m http.server 1991 --bind 0.0.0.0 >/dev/null 2>&1
  ) &
  echo " -> http://${LISTEN_IP}:1991/$(basename "$PROXY_LIST_FILE")"
fi

###########################################
# START 3PROXY
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
# EXPORT PROXY LIST
###########################################
# By default: export ALL (fast + no false negatives).
# Optional verify can be enabled.
cp "$PROXY_LIST_RAW" "$PROXY_LIST_FILE"
echo "Exported proxies list to: $PROXY_LIST_FILE"
echo

###########################################
# OPTIONAL INITIAL VERIFY (Google)
###########################################
if [ "$DO_INITIAL_VERIFY" = true ]; then
  echo -e "Initial verify via Google (timeout=${VERIFY_TIMEOUT}s jobs=${VERIFY_JOBS})..."
  TMP_OK=$(mktemp)
  TMP_FAIL=$(mktemp)

  while IFS=':' read -r HOST PORT USER PASS; do
    [ -z "$HOST" ] && continue
    (
      PROXY_URL="http://${USER}:${PASS}@${HOST}:${PORT}"
      if curl -sS --max-time "$VERIFY_TIMEOUT" -x "$PROXY_URL" https://www.google.com >/dev/null 2>&1; then
        echo "${HOST}:${PORT}:${USER}:${PASS}" >>"$TMP_OK"
      else
        echo "${HOST}:${PORT}" >>"$TMP_FAIL"
      fi
    ) &
    while [ "$(jobs -rp | wc -l)" -ge "$VERIFY_JOBS" ]; do
      sleep 0.1
    done
  done < "$PROXY_LIST_RAW"
  wait

  mv "$TMP_OK" "$PROXY_LIST_FILE"
  rm -f "$TMP_FAIL"

  echo "Verify done. Final list: $PROXY_LIST_FILE"
  echo
fi

###########################################
# LIVE HEALTH CHECK (Google)
###########################################
while true; do
  echo '----------------------------------------------'
  echo -e " ${PINK}Proxy health check at $(date)${RESET}"
  echo -e " File: ${PINK}${PROXY_LIST_FILE}${RESET}"
  echo '----------------------------------------------'

  OK=0
  FAIL=0

  while IFS=':' read -r HOST PORT USER PASS; do
    [ -z "$HOST" ] && continue
    PROXY_URL="http://${USER}:${PASS}@${HOST}:${PORT}"
    if curl -sS --max-time 5 -x "$PROXY_URL" https://www.google.com >/dev/null 2>&1; then
      echo -e "${HOST}:${PORT} -> ${GREEN}OK${RESET}"
      OK=$((OK+1))
    else
      echo -e "${HOST}:${PORT} -> ${RED}FAIL${RESET}"
      FAIL=$((FAIL+1))
    fi
  done < "$PROXY_LIST_FILE"

  echo -e "${YELLOW}Summary:${RESET} OK=${OK} FAIL=${FAIL}"
  echo -e "Next check in 60s (Ctrl+C to stop)"
  sleep 60
done

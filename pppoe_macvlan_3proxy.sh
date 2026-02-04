#!/bin/bash
set -euo pipefail

###########################################
# 0) Licence check with progress bar
###########################################
BAR_WIDTH=30
BAR_CHAR="#"
GREEN="\e[32m"; RED="\e[31m"; YELLOW="\e[33m"; BLUE="\e[34m"; PINK="\e[35m"; RESET="\e[0m"

TMP_OUT=$(mktemp)
 /usr/local/bin/check_license.sh --no-reboot >"$TMP_OUT" 2>&1 &
LIC_PID=$!
progress=0
printf "Checking licence... ${GREEN}[%-*s]${RESET}" "$BAR_WIDTH" ""
while kill -0 "$LIC_PID" 2>/dev/null; do
  if [ "$progress" -lt "$BAR_WIDTH" ]; then progress=$((progress + 1)); fi
  filled=$(printf "%*s" "$progress" "" | tr ' ' "$BAR_CHAR")
  empty=$(printf "%*s" "$((BAR_WIDTH - progress))" "")
  printf "\rChecking licence... ${GREEN}[%s%s]${RESET}" "$filled" "$empty"
  sleep 0.1
done
wait "$LIC_PID" || { echo; cat "$TMP_OUT"; rm -f "$TMP_OUT"; exit 1; }
rm -f "$TMP_OUT"
echo

###########################################
# INPUT
###########################################
WAN_IF="ens160"
PPP_USER="centre04"
PPP_PASS="centre04"

read -r -p "TARGET stable proxies? [default 2000]: " TARGET_IN
TARGET="${TARGET_IN:-2000}"

read -r -p "MAX sessions to try (overshoot)? [default 3000]: " MAXTRY_IN
MAXTRY="${MAXTRY_IN:-3000}"

BASE_PORT=30000
TABLE_BASE=4000
PARALLEL_START=30

PROXY_LIST_FILE="/root/proxies.txt"
THREEPROXY_BIN="/usr/local/bin/3proxy"
THREEPROXY_CFG="/usr/local/etc/3proxy/3proxy.cfg"

TEST_TIMEOUT=6

echo "TARGET=${TARGET} stable proxies, MAXTRY=${MAXTRY} sessions."
echo

###########################################
# Checks
###########################################
ip link show "$WAN_IF" >/dev/null 2>&1 || { echo "WAN_IF $WAN_IF not found"; exit 1; }
[ -x "$THREEPROXY_BIN" ] || { echo "3proxy not found: $THREEPROXY_BIN"; exit 1; }

###########################################
# Tuning
###########################################
ulimit -n 200000 || true
sysctl -w net.ipv4.conf.all.rp_filter=0 >/dev/null 2>&1 || true
sysctl -w net.ipv4.conf.default.rp_filter=0 >/dev/null 2>&1 || true
sysctl -w "net.ipv4.conf.${WAN_IF}.rp_filter=0" >/dev/null 2>&1 || true
if [ -e /proc/sys/net/netfilter/nf_conntrack_max ]; then
  sysctl -w net.netfilter.nf_conntrack_max=1048576 >/dev/null 2>&1 || true
fi
for scope in default "$WAN_IF"; do
  sysctl -w "net.ipv4.neigh.${scope}.gc_thresh1=8192"  >/dev/null 2>&1 || true
  sysctl -w "net.ipv4.neigh.${scope}.gc_thresh2=16384" >/dev/null 2>&1 || true
  sysctl -w "net.ipv4.neigh.${scope}.gc_thresh3=32768" >/dev/null 2>&1 || true
done

###########################################
# Cleanup
###########################################
pkill 3proxy >/dev/null 2>&1 || true
pkill pppd   >/dev/null 2>&1 || true

for i in $(seq 0 $((MAXTRY + 500))); do
  ip link show "macvlan$i" >/dev/null 2>&1 && ip link delete "macvlan$i" || true
  ip route flush table $((TABLE_BASE + i)) >/dev/null 2>&1 || true
done

ip rule show | awk '{print $1}' | sed 's/://g' | while read -r pref; do
  if [[ "$pref" =~ ^[0-9]+$ ]] && [ "$pref" -ge 4000 ] && [ "$pref" -le 25000 ]; then
    ip rule del pref "$pref" >/dev/null 2>&1 || true
  fi
done

mkdir -p /etc/ppp/peers /var/log/3proxy "$(dirname "$THREEPROXY_CFG")"
: > "$PROXY_LIST_FILE"

###########################################
# PPP secrets
###########################################
for f in /etc/ppp/chap-secrets /etc/ppp/pap-secrets; do
  [ -f "$f" ] || continue
  grep -q "[[:space:]]${PPP_USER}[[:space:]]" "$f" 2>/dev/null || echo "${PPP_USER} * ${PPP_PASS} *" >>"$f"
done

###########################################
# 3proxy base cfg
###########################################
cat >"$THREEPROXY_CFG" <<'EOF'
daemon
maxconn 20000
nserver 8.8.8.8
nserver 1.1.1.1
nscache 65536
log /var/log/3proxy/3proxy.log D
logformat "L%y-%m-%d %H:%M:%S %e %E %C:%c %R:%r %O %I %h %T"
timeouts 1 5 30 60 180 1000 15 60
auth none
allow *
EOF

###########################################
# Helpers
###########################################
start_ppp() {
  local i="$1"
  local mac="macvlan$i"
  ip link add link "$WAN_IF" name "$mac" type macvlan mode bridge 2>/dev/null || return 1
  ip link set "$mac" up

  local peer="/etc/ppp/peers/pppoe$i"
  cat >"$peer" <<EOF
plugin rp-pppoe.so
$mac
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
  return 0
}

wait_ip() {
  local ppp_if="$1"
  local t=60
  while ! ip addr show "$ppp_if" >/dev/null 2>&1 && [ "$t" -gt 0 ]; do
    sleep 1; t=$((t-1))
  done
  ip -4 addr show dev "$ppp_if" | awk '/inet /{print $2}' | cut -d/ -f1 | head -n1
}

setup_policy() {
  local idx="$1" ppp_if="$2" ip_ppp="$3"
  local table_id=$((TABLE_BASE + idx))

  ip route flush table "$table_id" >/dev/null 2>&1 || true
  ip route add "${ip_ppp}/32" dev "$ppp_if" table "$table_id" >/dev/null 2>&1 || return 1
  ip route add default dev "$ppp_if" table "$table_id" >/dev/null 2>&1 || return 1

  while ip rule show | grep -q "from ${ip_ppp}"; do
    ip rule del from "$ip_ppp" >/dev/null 2>&1 || break
  done
  ip rule add from "$ip_ppp" table "$table_id" priority $((4000 + idx)) >/dev/null 2>&1 || true
  return 0
}

ppp_has_internet() {
  local ppp_if="$1"
  curl -sS --max-time "$TEST_TIMEOUT" --interface "$ppp_if" https://www.google.com >/dev/null 2>&1
}

kill_ppp() {
  local i="$1"
  pkill -f "pppd call pppoe$i" >/dev/null 2>&1 || true
  ip link show "macvlan$i" >/dev/null 2>&1 && ip link delete "macvlan$i" || true
}

###########################################
# Build stable pool
###########################################
echo "Building stable pool..."
STABLE=0
PORT_NEXT="$BASE_PORT"

for i in $(seq 0 $((MAXTRY - 1))); do
  echo "Starting PPP session $i ..."
  start_ppp "$i" || { echo "Failed to start $i"; continue; }

  if ((( (i + 1) % PARALLEL_START == 0 ))); then
    echo " -> launched $((i+1)) sessions..."
    sleep 1
  fi

  ppp_if="ppp$i"
  ip_ppp="$(wait_ip "$ppp_if" || true)"
  if [ -z "$ip_ppp" ]; then
    echo "ppp$i: no IP (skip)"
    kill_ppp "$i"
    continue
  fi

  setup_policy "$i" "$ppp_if" "$ip_ppp" || { echo "ppp$i policy fail"; kill_ppp "$i"; continue; }

  if ppp_has_internet "$ppp_if"; then
    port="$PORT_NEXT"
    echo "proxy -a -p${port} -i0.0.0.0 -e${ip_ppp}" >>"$THREEPROXY_CFG"
    echo "${ip_ppp}:${port}:fibre123:fibrebebe123" >>"$PROXY_LIST_FILE"
    STABLE=$((STABLE+1))
    PORT_NEXT=$((PORT_NEXT+1))
    echo -e " -> ${GREEN}STABLE${RESET} ppp$i IP $ip_ppp PORT $port (stable=${STABLE}/${TARGET})"
  else
    echo -e " -> ${RED}NO INTERNET${RESET} on ppp$i ($ip_ppp) (killed)"
    kill_ppp "$i"
    continue
  fi

  if [ "$STABLE" -ge "$TARGET" ]; then
    break
  fi
done

echo
echo "Stable proxies created: ${STABLE}"
if [ "$STABLE" -eq 0 ]; then
  echo -e "${RED}0 stable found. That means even ppp0 cannot reach google via interface test.${RESET}"
  echo "Check PPP connectivity: tail -n 50 /var/log/pppoe_0.log"
  exit 1
fi

###########################################
# Start 3proxy
###########################################
echo "Starting 3proxy..."
"$THREEPROXY_BIN" "$THREEPROXY_CFG" &
echo "Proxy list: $PROXY_LIST_FILE"
echo
echo "Done."

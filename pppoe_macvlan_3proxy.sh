#!/bin/bash
set -e

#############################################
# 0. Licence check with progress bar
#############################################

BAR_WIDTH=30
BAR_CHAR="#"
GREEN="\e[32m"
RESET="\e[0m"

TMP_OUT=$(mktemp)

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

#############################################
# 1. Ask how many proxies to generate
#############################################

DEFAULT_COUNT=2000
echo
read -r -p "How many proxies do you want to generate? [default: ${DEFAULT_COUNT}]: " USER_COUNT

if [ -z "$USER_COUNT" ]; then
    COUNT="$DEFAULT_COUNT"
elif [[ "$USER_COUNT" =~ ^[0-9]+$ ]] && [ "$USER_COUNT" -gt 0 ]; then
    COUNT="$USER_COUNT"
else
    echo "Invalid number, using default ${DEFAULT_COUNT}."
    COUNT="$DEFAULT_COUNT"
fi

echo
echo "Using COUNT=${COUNT} proxies."
echo

#############################################
# 2. Global configuration
#############################################

WAN_IF="ens33"               # Physical WAN interface
BASE_PORT=60000              # First proxy port (all others follow)
TABLE_BASE=1000              # Base routing table ID
PARALLEL_START=200           # How many PPPoE sessions to start before a short pause

PPP_USER="centre04"
PPP_PASS="centre04"

PROXY_LIST_FILE="/root/proxies.txt"
THREEPROXY_BIN="/usr/local/bin/3proxy"
THREEPROXY_CFG="/usr/local/etc/3proxy/3proxy.cfg"

#############################################
# 3. Detect local IP on WAN_IF
#############################################

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

if ! ip link show "$WAN_IF" &>/dev/null; then
    echo "Interface ${WAN_IF} not found."
    exit 1
fi

#############################################
# 4. Cleanup old configs + macvlans
#############################################

echo "$(date) - Cleaning old configuration..."

pkill 3proxy 2>/dev/null || true
pkill pppd 2>/dev/null || true

# Clean old macvlan interfaces (up to COUNT + 500, just to be safe)
MAX_CLEAN=$((COUNT + 500))
for i in $(seq 0 "$MAX_CLEAN"); do
    ip link show "macvlan$i" &>/dev/null && ip link delete "macvlan$i"
done

# Reset proxy list file
: > "$PROXY_LIST_FILE"

#############################################
# 5. Ensure PPP secrets contain our user
#############################################

for f in /etc/ppp/chap-secrets /etc/ppp/pap-secrets; do
    if [ -f "$f" ]; then
        if ! grep -q "^[[:space:]]*${PPP_USER}[[:space:]]" "$f" 2>/dev/null; then
            echo "${PPP_USER} * ${PPP_PASS} *" >>"$f"
        fi
    fi
done

mkdir -p /etc/ppp/peers
mkdir -p "$(dirname "$THREEPROXY_CFG")"
mkdir -p /var/log/3proxy

#############################################
# 6. Prepare 3proxy config (single instance)
#############################################

cat >"$THREEPROXY_CFG" <<EOF
daemon
maxconn 8192
nserver 81.192.17.62
nserver 8.8.8.8
nscache 65536

log /var/log/3proxy/log.log D
logformat "L%Y-%m-%d %H:%M:%S %E %U %C:%c %R:%r %O %I %h %T"

timeouts 1 5 30 60 180 1800 15 60

auth none
allow *
EOF

#############################################
# 7. Phase 1 - Create PPPoE sessions in parallel
#############################################

echo "Starting service..."
echo
echo "Phase 1: creating PPPoE sessions..."

for i in $(seq 0 $((COUNT - 1))); do
    echo "# Proxy number $i starting..."

    MACVLAN_IF="macvlan$i"

    if ! ip link add link "$WAN_IF" name "$MACVLAN_IF" type macvlan mode bridge 2>/dev/null; then
        echo "!!! Failed to create ${MACVLAN_IF}"
        continue
    fi

    ip link set "$MACVLAN_IF" up

    PEER_FILE="/etc/ppp/peers/pppoe$i"
    cat >"$PEER_FILE" <<EOF
plugin rp-pppoe.so
$MACVLAN_IF
noauth
defaultroute
usepeerdns
persist
maxfail 0
lcp-echo-interval 20
lcp-echo-failure 3
user "$PPP_USER"
password "$PPP_PASS"
unit $i
EOF

    # start pppd in BACKGROUND (we don't wait here)
    pppd call "pppoe$i" >/var/log/pppoe_$i.log 2>&1 &

    # small pause every PARALLEL_START sessions to avoid overloading everything at once
    if (((i + 1) % PARALLEL_START == 0)); then
        echo " -> $((i + 1)) sessions started, taking a short break..."
        sleep 2
    fi
done

#############################################
# 8. Phase 2 - Wait for PPP + routes + 3proxy entries
#############################################

echo
echo "Phase 2: configuring routes and proxies..."
echo

for i in $(seq 0 $((COUNT - 1))); do
    PPP_IF="ppp$i"
    MACVLAN_IF="macvlan$i"

    # Wait for pppX interface to appear
    TIMEOUT=30
    while ! ip addr show "$PPP_IF" &>/dev/null && [ "$TIMEOUT" -gt 0 ]; do
        sleep 1
        TIMEOUT=$((TIMEOUT - 1))
    done

    if ! ip addr show "$PPP_IF" &>/dev/null; then
        echo "!!! PPPoE failed on ${MACVLAN_IF} (${PPP_IF} not created)"
        continue
    fi

    # Get public IP on PPP interface
    IP_PPP=""
    TIMEOUT_IP=20
    while [ -z "$IP_PPP" ] && [ "$TIMEOUT_IP" -gt 0 ]; do
        IP_PPP=$(ip -4 addr show dev "$PPP_IF" | awk '/inet / {print $2}' | cut -d/ -f1 | head -n1)
        [ -n "$IP_PPP" ] && break
        sleep 1
        TIMEOUT_IP=$((TIMEOUT_IP - 1))
    done

    if [ -z "$IP_PPP" ]; then
        echo "!!! Unable to get IP on ${PPP_IF} (timeout)"
        continue
    fi

    echo " -> Proxy READY <<< ${PPP_IF} with IP ${IP_PPP}"

    # Dedicated routing table for this PPP
    TABLE_ID=$((TABLE_BASE + i))

    ip route flush table "$TABLE_ID" 2>/dev/null || true

    # host route for the PPP IP
    if ! ip route add "$IP_PPP/32" dev "$PPP_IF" table "$TABLE_ID" 2>/dev/null; then
        echo "!!! Failed to add host route ${IP_PPP}/32 via ${PPP_IF} (interface probably went down)"
        continue
    fi

    # default route via this PPP interface
    if ! ip route add default dev "$PPP_IF" table "$TABLE_ID" 2>/dev/null; then
        echo "!!! Failed to add default route via ${PPP_IF} (interface probably went down)"
        continue
    fi

    ip rule del from "$IP_PPP" table "$TABLE_ID" 2>/dev/null || true
    if ! ip rule add from "$IP_PPP" table "$TABLE_ID" priority $((1000 + i)) 2>/dev/null; then
        echo "!!! Failed to add ip rule from ${IP_PPP} for table ${TABLE_ID}"
        continue
    fi

    #########################################
    # 3proxy mapping for this PPP
    #########################################

    PORT=$((BASE_PORT + i))        # ONE instance, ONE simple formula

    echo "proxy -a -p${PORT} -i${LISTEN_IP} -e${IP_PPP}" >>"$THREEPROXY_CFG"

    # Open the port in firewall if iptables is available
    if command -v iptables >/dev/null 2>&1; then
        iptables -C INPUT -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null || \
            iptables -A INPUT -p tcp --dport "$PORT" -j ACCEPT
    fi

    # Add to proxy list file (example user/pass in the format IP:PORT:USER:PASS)
    echo "${IP_PPP}:${PORT}:fibre123:firebe123" >>"$PROXY_LIST_FILE"
done

#############################################
# 9. Optional: adjust default route via ppp0
#############################################

if ip link show ppp0 &>/dev/null; then
    ip route del default dev "$WAN_IF" 2>/dev/null || true
    ip route add default dev ppp0 2>/dev/null || true
fi

#############################################
# 10. Mini HTTP server to download proxies.txt
#############################################

if command -v python3 >/dev/null 2>&1; then
    echo "Server Ready For ${PROXY_LIST_FILE} (ALL interfaces):"
    (
        cd "$(dirname "$PROXY_LIST_FILE")" || exit 1
        python3 -m http.server 1991 --bind 0.0.0.0 >/dev/null 2>&1 &
    )
    echo "  -> http://IP_PUBLIC:1991/$(basename "$PROXY_LIST_FILE")"
else
    echo "python3 not found, HTTP server not started."
fi

#############################################
# 11. Start 3proxy instance
#############################################

echo
echo "Starting 3proxy with generated configuration..."

if [ -s "$THREEPROXY_CFG" ]; then
    "$THREEPROXY_BIN" "$THREEPROXY_CFG" &
    echo "  -> 3proxy started (ports from ${BASE_PORT})"
else
    echo "ERROR: 3proxy config file is empty, NOT starting 3proxy."
fi

echo
echo "Done."
echo "Proxy list available in: ${PROXY_LIST_FILE}"

#############################################
# 12. OPTIONAL: keep-alive health-check loop
#############################################
# This will send one HTTP request per proxy every 60s to keep PPP sessions active.
# Comment this block if it is too heavy.

if command -v curl >/dev/null 2>&1; then
    (
        echo "Starting background keep-alive loop (every 60 seconds)..."
        while true; do
            if [ -f "$PROXY_LIST_FILE" ]; then
                while IFS=: read -r ip port user pass; do
                    curl -s --max-time 10 -x "${ip}:${port}" http://api.ipify.org >/dev/null 2>&1 || true
                done < "$PROXY_LIST_FILE"
            fi
            sleep 60
        done
    ) &
fi

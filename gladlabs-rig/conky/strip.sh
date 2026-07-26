#!/usr/bin/env bash
# Sensor-strip renderer v3 for the 8.8" 1920x480 display (HDMI-A-1).
# Called by conky via ${execpi 1 ...} — emits conky markup, one frame per call.
# hwmon devices are resolved by NAME every call: indices shuffle across boots.
# v3: background image (make-strip-bg.py), scrolling graphs (cpugraph native;
# gpu util + wall watts via execgraph reading per-tick cache files), tightened
# column text to stop overflow into the next column.
# Palette: mint #00e5d6 / cool #33bbee / amber #ffb833 / hot #ff8000
#          text #e6edf3 / labels #9fb1bd / track #22323c

shopt -s nullglob

K10="" EC="" PSU=""
NV7="" NV6=""
DIMM_LINES=""
for d in /sys/class/hwmon/hwmon*; do
  case "$(<"$d/name")" in
    k10temp) K10=$d ;;
    asusec) EC=$d ;;
    corsairpsu) PSU=$d ;;
    spd5118) DIMM_LINES+="$(basename "$(readlink -f "$d/device")") $d"$'\n' ;;
    nvme)
      m=$(tr -d ' ' < "$d/device/model" 2>/dev/null)
      case "$m" in
        *MP700*) NV7=$d ;;
        *MP600*) NV6=$d ;;
      esac ;;
  esac
done
mapfile -t DIMMS < <(printf '%s' "$DIMM_LINES" | sort | awk '{print $2}')

mc()  { [ -r "$1" ] && awk '{printf "%.0f", $1/1000}' "$1" || printf -- "--"; }
mc1() { [ -r "$1" ] && awk '{printf "%.1f", $1/1000}' "$1" || printf -- "--"; }
uw()  { [ -r "$1" ] && awk '{printf "%.0f", $1/1000000}' "$1" || printf -- "--"; }
mv1() { [ -r "$1" ] && awk '{printf "%.1f", $1/1000}' "$1" || printf -- "--"; }
mv2() { [ -r "$1" ] && awk '{printf "%.2f", $1/1000}' "$1" || printf -- "--"; }
rpm() { [ -r "$1" ] && cat "$1" || printf -- "--"; }

pctcol() { # value warm hot -> ramp hex (load/util)
  local v=${1%%.*} w=${2:-45} h=${3:-75}
  [[ $v =~ ^[0-9]+$ ]] || { printf "9fb1bd"; return; }
  if [ "$v" -lt "$w" ]; then printf "009988"; elif [ "$v" -lt "$h" ]; then printf "ffb833"; else printf "ff8000"; fi
}
tcol() { # temp -> ramp hex (cool blue base)
  local v=${1%%.*} w=${2:-45} h=${3:-70}
  [[ $v =~ ^[0-9]+$ ]] || { printf "9fb1bd"; return; }
  if [ "$v" -lt "$w" ]; then printf "33bbee"; elif [ "$v" -lt "$h" ]; then printf "ffb833"; else printf "ff8000"; fi
}
bar() { # pct warm hot -> 24-char colored block bar
  local p=${1%%.*} w=$2 h=$3
  [[ $p =~ ^[0-9]+$ ]] || p=0
  [ "$p" -gt 100 ] && p=100
  local W=24
  local f=$(( (p*W+50)/100 ))
  local i fill="" rest=""
  for ((i=0;i<f;i++)); do fill+="█"; done
  for ((i=f;i<W;i++)); do rest+="░"; done
  printf '${color %s}%s${color 22323c}%s' "$(pctcol "$p" "$w" "$h")" "$fill" "$rest"
}

# CPU load % from /proc/stat delta vs previous call (state kept in runtime dir)
RD=${XDG_RUNTIME_DIR:-/tmp}
STATF=$RD/strip-cpustat
read -r _ cu cn cs cidle cio cirq csirq cst _ < /proc/stat
ctotal=$((cu+cn+cs+cidle+cio+cirq+csirq+cst)); cbusy=$((ctotal-cidle-cio))
CPUPCT=0
if [ -r "$STATF" ]; then
  read -r pt pb < "$STATF"
  dt=$((ctotal-pt)); db=$((cbusy-pb))
  [ "$dt" -gt 0 ] && CPUPCT=$(( db*100/dt ))
fi
printf '%s %s' "$ctotal" "$cbusy" > "$STATF"

TCTL=$(mc1 "$K10/temp1_input"); CCD1=$(mc "$K10/temp3_input"); CCD2=$(mc "$K10/temp4_input")
VRM=$(mc "$EC/temp4_input"); MB=$(mc "$EC/temp3_input")

PTOT=$(uw "$PSU/power1_input"); P12=$(uw "$PSU/power2_input"); P5=$(uw "$PSU/power3_input"); P33=$(uw "$PSU/power4_input")
VIN=$(mv1 "$PSU/in0_input"); V12=$(mv2 "$PSU/in1_input"); V5=$(mv2 "$PSU/in2_input"); V33=$(mv2 "$PSU/in3_input")
PSUT1=$(mc "$PSU/temp1_input"); PSUT2=$(mc "$PSU/temp2_input"); PSUFAN=$(rpm "$PSU/fan1_input")
PPCT=0; [[ $PTOT =~ ^[0-9]+$ ]] && PPCT=$(( PTOT*100/1500 ))

D1=$(mc "${DIMMS[0]}/temp1_input"); D2=$(mc "${DIMMS[1]}/temp1_input")
N7=$(mc "$NV7/temp1_input"); N6=$(mc "$NV6/temp1_input")

IFACE=$(ip route show default 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="dev"){print $(i+1); exit}}')

G0T="--" G0P="--" G0L="--" G0U=0 G0MU="--" G0MT="--" G0MP=0 G0C="--" G0PC="-" G0PW="-"
G1T="--" G1P="--" G1L="--" G1U=0 G1MU="--" G1MT="--" G1MP=0 G1C="--" G1PC="-" G1PW="-"
i=0
while IFS=',' read -r t p pl u mu mt c pg pw; do
  t=${t// /}; p=${p// /}; pl=${pl// /}; u=${u// /}; mu=${mu// /}; mt=${mt// /}; c=${c// /}; pg=${pg// /}; pw=${pw// /}
  p=${p%%.*}; pl=${pl%%.*}
  mug=$(awk -v x="$mu" 'BEGIN{printf "%.1f", x/1024}')
  mtg=$(awk -v x="$mt" 'BEGIN{printf "%.1f", x/1024}')
  mpc=0; [[ $mu =~ ^[0-9]+$ && $mt =~ ^[0-9]+$ && $mt -gt 0 ]] && mpc=$(( mu*100/mt ))
  if [ $i -eq 0 ]; then G0T=$t G0P=$p G0L=$pl G0U=$u G0MU=$mug G0MT=$mtg G0MP=$mpc G0C=$c G0PC=$pg G0PW=$pw
  else G1T=$t G1P=$p G1L=$pl G1U=$u G1MU=$mug G1MT=$mtg G1MP=$mpc G1C=$c G1PC=$pg G1PW=$pw; fi
  i=$((i+1))
done < <(nvidia-smi --query-gpu=temperature.gpu,power.draw,power.limit,utilization.gpu,memory.used,memory.total,clocks.sm,pcie.link.gen.current,pcie.link.width.current --format=csv,noheader,nounits 2>/dev/null)

# Water loop via OpenLinkHub REST (poller prints eval-safe KEY=VALUE lines)
LOOP_COOL="--" LOOP_BLOCK="--" LOOP_PUMP="--" LOOP_FN=0 LOOP_FMIN="--" LOOP_FMAX="--" LOOP_FAVG="--" LOOP_AIR="--"
eval "$(python3 "$HOME/.config/conky/loop-poll.py" 2>/dev/null)"

# graph caches (execgraph reads these; numbers only, 0 on missing)
[[ $G0U =~ ^[0-9]+$ ]] && printf '%s' "$G0U" > "$RD/strip-gpu0" || printf '0' > "$RD/strip-gpu0"
[[ $G1U =~ ^[0-9]+$ ]] && printf '%s' "$G1U" > "$RD/strip-gpu1" || printf '0' > "$RD/strip-gpu1"
[[ $PTOT =~ ^[0-9]+$ ]] && printf '%s' "$PTOT" > "$RD/strip-watts" || printf '0' > "$RD/strip-watts"

C1=28 C2=512 C3=992 C4=1464

# background (baked dividers, grid, hairlines)
printf '${image %s/.config/conky/strip-bg.png -p 0,0 -s 1920x480 -n}' "$HOME"

# R0 — section labels + clock
printf '${voffset 12}${font DejaVu Sans:bold:size=15}'
printf '${goto %s}${color 00e5d6}▎${color 9fb1bd}CPU · RYZEN 9950X3D${goto %s}${color 00e5d6}▎${color 9fb1bd}GPU · RTX 5090${goto %s}${color 00e5d6}▎${color 9fb1bd}GPU · RTX 3090${goto %s}${color 00e5d6}▎${color 9fb1bd}PSU · HX1500i WALL' $C1 $C2 $C3 $C4
printf '${goto 1770}${font DejaVu Sans Mono:bold:size=15}${color 00e5d6}${time %%H:%%M:%%S}\n'

# R1 — heroes
printf '${voffset 14}${font DejaVu Sans Mono:bold:size=52}'
printf '${goto %s}${color %s}%s°${goto %s}${color %s}%s°${goto %s}${color %s}%s°${goto %s}${color 00e5d6}%s W\n' \
  $C1 "$(tcol "$TCTL" 55 75)" "$TCTL" \
  $C2 "$(tcol "$G0T" 45 70)" "$G0T" \
  $C3 "$(tcol "$G1T" 45 70)" "$G1T" \
  $C4 "$PTOT"

# R2 — bars: cpu load / gpu util / gpu util / psu load
printf '${voffset -6}${font DejaVu Sans Mono:size=19}'
printf '${goto %s}%s${color 9fb1bd} %s%%${goto %s}%s${color 9fb1bd} %s%%${goto %s}%s${color 9fb1bd} %s%%${goto %s}%s${color 9fb1bd} %s%%\n' \
  $C1 "$(bar "$CPUPCT" 45 80)" "$CPUPCT" \
  $C2 "$(bar "$G0U" 45 80)" "$G0U" \
  $C3 "$(bar "$G1U" 45 80)" "$G1U" \
  $C4 "$(bar "$PPCT" 33 60)" "$PPCT"

# R3
printf '${voffset 12}${font DejaVu Sans Mono:size=19}${color e6edf3}'
printf '${goto %s}load %s%% @ ${freq_g} GHz${goto %s}power %s / %s W${goto %s}power %s / %s W${goto %s}12V %s W · 5V %s W · 3.3V %s W\n' \
  $C1 "$CPUPCT" $C2 "$G0P" "$G0L" $C3 "$G1P" "$G1L" $C4 "$P12" "$P5" "$P33"

# R4
printf '${voffset 8}${font DejaVu Sans Mono:size=19}${color e6edf3}'
printf '${goto %s}CCD %s·%s° · VRM %s° · MB %s°${goto %s}VRAM %s/%s G · %s%%${goto %s}VRAM %s/%s G · %s%%${goto %s}rails %s · %s · %s V\n' \
  $C1 "$CCD1" "$CCD2" "$VRM" "$MB" $C2 "$G0MU" "$G0MT" "$G0MP" $C3 "$G1MU" "$G1MT" "$G1MP" $C4 "$V12" "$V5" "$V33"

# R5
printf '${voffset 8}${font DejaVu Sans Mono:size=19}${color e6edf3}'
printf '${goto %s}RAM ${mem} · DIMM %s·%s°${goto %s}core %s MHz · PCIe %s x%s${goto %s}core %s MHz · PCIe %s x%s${goto %s}line %s V · PSU %s·%s°\n' \
  $C1 "$D1" "$D2" $C2 "$G0C" "$G0PC" "$G0PW" $C3 "$G1C" "$G1PC" "$G1PW" $C4 "$VIN" "$PSUT1" "$PSUT2"

# R6 — water loop
printf '${voffset 10}${font DejaVu Sans Mono:size=19}'
printf '${goto %s}${color 00e5d6}▎${color 9fb1bd}LOOP ${color %s}%s°${color e6edf3} · blk %s°${goto %s}${color e6edf3}pump %s rpm${goto %s}${color e6edf3}fans %s · avg %s rpm${goto %s}${color e6edf3}min %s · max %s · air %s°\n' \
  $C1 "$(tcol "$LOOP_COOL" 35 45)" "$LOOP_COOL" "$LOOP_BLOCK" $C2 "$LOOP_PUMP" $C3 "$LOOP_FN" "$LOOP_FAVG" $C4 "$LOOP_FMIN" "$LOOP_FMAX" "$LOOP_AIR"

# R7 — footer (graphs render below this, from the static conky.text tail —
# graph objects inside execpi lose their history every tick, so they live in
# sensor-strip.conf where they persist and actually scroll)
printf '${voffset 12}${font DejaVu Sans Mono:size=18}${color 9fb1bd}'
printf '${goto %s}top ${top name 1}${top cpu 1}%%${goto %s}NVMe MP700 %s° · MP600 %s°${goto %s}' $C1 $C2 "$N7" "$N6" $C3
if [ -n "$IFACE" ]; then
  printf '${color 33bbee}▼ ${downspeed %s}${color 9fb1bd} · ${color ff8000}▲ ${upspeed %s}${color 9fb1bd}' "$IFACE" "$IFACE"
else
  printf 'net --'
fi
printf '${goto %s}fan %s rpm · up ${uptime_short} · load ${loadavg 1}\n' $C4 "$PSUFAN"

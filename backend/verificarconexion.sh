#!/bin/bash
# =============================================================================
#  verificarconexion.sh  --  PRODUCTOR del monitoreo ICMP
# =============================================================================
#  Hace ping al router una vez por segundo y va guardando el resultado.
#
#  Uso:  verificarconexion.sh <ip> <archivo_de_salida>
#
#  Se usa  ping -c 1  (un paquete por vuelta) en vez de un ping continuo, y se
#  recorta el archivo cuando pasa de MAX_LINEAS.
# =============================================================================

IP="$1"
SALIDA="$2"
MAX_LINEAS=300

if [ -z "$SALIDA" ]; then
    echo "Uso: $0 <ip> <archivo_de_salida>" >&2
    exit 1
fi

while true
do
    # -c 1  un solo paquete    -W 2  espera maxima de 2 segundos
    respuesta=$(ping -c 1 -W 2 "$IP" 2>&1 | grep -E "ttl=|TTL=|Unreachable|unreachable|100% packet loss")

    if [ -z "$respuesta" ]; then
        respuesta="sin respuesta de $IP"
    fi

    echo "$(date '+%H:%M:%S') $respuesta" >> "$SALIDA"

    lineas=$(wc -l < "$SALIDA" 2>/dev/null || echo 0)
    if [ "$lineas" -gt "$MAX_LINEAS" ]; then
        tail -n 100 "$SALIDA" > "${SALIDA}.tmp" && mv -f "${SALIDA}.tmp" "$SALIDA"
    fi

    sleep 1
done

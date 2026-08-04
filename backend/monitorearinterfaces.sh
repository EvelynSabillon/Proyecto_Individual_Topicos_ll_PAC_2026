#!/bin/bash
# =============================================================================
#  monitorearinterfaces.sh  --  CONSUMIDOR del monitoreo de interfaces
# =============================================================================
#  Lee el archivo que deja verificarinterfaces.sh, lo interpreta y escribe:
#
#    - cuatro archivos con el valor INSTANTANEO, que es lo que lee la ventana
#      cada segundo para repintar los paneles:
#          estadoN.txt   ->  1 = UP, 0 = DOWN
#          traficoN.txt  ->  "<bits_in> <bits_out>"
#      Estos se SOBRESCRIBEN: solo interesa el ultimo valor.
#
#    - un REGISTRO HISTORICO en trafico.log, con una linea por interfaz y por
#      segundo, fechada. Este si se acumula, para poder demostrar despues que
#      hubo trafico y a que hora exactamente.
#
#  Uso:
#    monitorearinterfaces.sh <datos> <estado1> <trafico1> <estado2> <trafico2> \
#                            <log> <max_lineas> <nombre_if1> <nombre_if2>
# =============================================================================

DATOS="$1"
ESTADO1="$2"
TRAFICO1="$3"
ESTADO2="$4"
TRAFICO2="$5"
LOG="$6"
MAX_LINEAS="${7:-500}"
NOMBRE1="${8:-interfaz1}"
NOMBRE2="${9:-interfaz2}"

if [ -z "$TRAFICO2" ]; then
    echo "Uso: $0 <datos> <estado1> <trafico1> <estado2> <trafico2> <log> <max> <if1> <if2>" >&2
    exit 1
fi

# Convierte bits por segundo en algo legible.
# Se usa awk y no aritmetica de bash porque bash solo trabaja con enteros:
# no podria calcular "4.82 Mbps", se quedaria en "4 Mbps".
humano() {
    awk -v b="${1:-0}" 'BEGIN{
        if (b+0 >= 1000000000)   printf "%.2f Gbps", b/1000000000;
        else if (b+0 >= 1000000) printf "%.2f Mbps", b/1000000;
        else if (b+0 >= 1000)    printf "%.2f kbps", b/1000;
        else                     printf "%d bps", b+0;
    }'
}

# Escribe el estado, el trafico instantaneo y la linea del registro.
#   $1 = linea leida   $2 = archivo de estado   $3 = archivo de trafico
#   $4 = nombre de la interfaz
procesar() {
    linea="$1"
    archivo_estado="$2"
    archivo_trafico="$3"
    nombre="$4"

    # tr -d '\r' quita el retorno de carro que mete RouterOS al final de
    # cada linea; sin esto, el valor real es "true\r" y nunca compara igual.
    estado=$(echo "$linea" | tr -d '\r' | cut -d " " -f3)
    rx=$(echo "$linea" | tr -d '\r' | cut -d " " -f4)
    tx=$(echo "$linea" | tr -d '\r' | cut -d " " -f5)

    # Las comillas alrededor de $estado son obligatorias: si la variable
    # queda vacia, sin comillas el test da error de sintaxis y el bucle
    # muere en silencio.
    if [ "$estado" = "true" ]; then
        echo 1 > "$archivo_estado"
        echo "${rx:-0} ${tx:-0}" > "$archivo_trafico"
        etiqueta="UP"
    else
        echo 0 > "$archivo_estado"
        echo "0 0" > "$archivo_trafico"
        etiqueta="DOWN"
        rx=0
        tx=0
    fi

    printf '%s  %-10s %-4s  IN %-12s OUT %s\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" "$nombre" "$etiqueta" \
        "$(humano "${rx:-0}")" "$(humano "${tx:-0}")" >> "$LOG"
}

# Marca de inicio, para que en el registro se distinga una sesion de otra.
printf '\n===== monitoreo iniciado  %s  (%s y %s) =====\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" "$NOMBRE1" "$NOMBRE2" >> "$LOG"

while true
do
    if [ -s "$DATOS" ]; then
        linea1=$(grep "^DATO 1" "$DATOS" 2>/dev/null | tail -n1)
        linea2=$(grep "^DATO 2" "$DATOS" 2>/dev/null | tail -n1)

        [ -n "$linea1" ] && procesar "$linea1" "$ESTADO1" "$TRAFICO1" "$NOMBRE1"
        [ -n "$linea2" ] && procesar "$linea2" "$ESTADO2" "$TRAFICO2" "$NOMBRE2"

        # Recorte para que el registro no crezca sin fin. Se escribe aparte
        # y se mueve con mv, atomico, para no leerlo a medio recortar.
        lineas=$(wc -l < "$LOG" 2>/dev/null || echo 0)
        if [ "$lineas" -gt "$MAX_LINEAS" ]; then
            tail -n "$MAX_LINEAS" "$LOG" > "${LOG}.tmp" && mv -f "${LOG}.tmp" "$LOG"
        fi
    fi

    sleep 1
done

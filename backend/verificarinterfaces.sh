#!/bin/bash
# =============================================================================
#  verificarinterfaces.sh  --  PRODUCTOR del monitoreo de interfaces
# =============================================================================
#  Pregunta al router, una vez por segundo, el estado y el trafico de DOS
#  interfaces, y deja la respuesta en un archivo de texto.
#
#  Uso:
#    verificarinterfaces.sh <llave> <usuario> <ip> <timeout> <salida> <comando>
#
#  El <comando> de RouterOS lo arma Python y se pasa como argumento, para no
#  tener que escapar comillas dentro de comillas dentro de bash.
#
#  Formato de cada linea que escribe:
#      DATO <1|2> <true|false> <bits_in> <bits_out>
# =============================================================================

LLAVE="$1"
USUARIO="$2"
IP="$3"
TIMEOUT="${4:-5}"
SALIDA="$5"
CMD="$6"

if [ -z "$CMD" ]; then
    echo "Uso: $0 <llave> <usuario> <ip> <timeout> <salida> <comando>" >&2
    exit 1
fi

TMP="${SALIDA}.tmp"

while true
do
    ssh -T \
        -o BatchMode=yes \
        -o ConnectTimeout="$TIMEOUT" \
        -o LogLevel=ERROR \
        -o StrictHostKeyChecking=accept-new \
        -i "$LLAVE" "$USUARIO@$IP" "$CMD" > "$TMP" 2>/dev/null
    codigo=$?

    # Se comprueba el codigo Y que la respuesta traiga lineas DATO: RouterOS
    # devuelve 0 aunque rechace el comando.
    if [ $codigo -ne 0 ] || ! grep -q "^DATO " "$TMP" 2>/dev/null; then
        printf 'DATO 1 sinconexion 0 0\nDATO 2 sinconexion 0 0\n' > "$TMP"
    fi

    mv -f "$TMP" "$SALIDA"
    sleep 1
done
